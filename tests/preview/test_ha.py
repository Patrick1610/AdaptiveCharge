"""Actual HA objects with a tripwire on all outgoing services and source writes."""

from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.adaptive_charge_preview.const import DOMAIN
from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
from custom_components.adaptive_charge_preview.sources import local_time, measurement, price_rows


def entry(subentries=True):
    return ConfigEntry(domain=DOMAIN, title="Preview", version=1, minor_version=1, source="user", unique_id="sensor.iq_energy", data={"meter_entity":"sensor.iq_energy", "power_entity":"sensor.hw_power", "plug_entity":"binary_sensor.evse_plug", "connection_entity":"sensor.connection", "session_entity":"input_text.live_session", "active_entity":"input_boolean.live_active", "generation_entity":"counter.live_gen"}, options={}, discovery_keys={}, subentries_data=[{"subentry_type":"vehicle","title":"Tesla","unique_id":"sensor.tesla_soc","data":{"kind":"tesla","soc_entity":"sensor.tesla_soc","battery_entity":"sensor.tesla_energy","range_entity":"sensor.tesla_range","limit_entity":"number.tesla_limit","cable_entity":"binary_sensor.tesla_cable","target_entity":"input_number.live_goal","departure_entity":"input_datetime.live_departure","measurement_time_entity":"sensor.car_capture","limit_writer_entity":"number.tesla_limit"}}] if subentries else [])


async def setup(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    await hass.config.async_update(time_zone="Europe/Amsterdam")
    now = datetime.now(timezone.utc)
    for entity_id, value, attrs in [
        ("sensor.iq_energy",100,{"unit_of_measurement":"kWh"}),
        ("sensor.hw_power",0,{"unit_of_measurement":"W"}),
        ("sensor.car_capture",now.isoformat(),{}),
        ("sensor.tesla_soc",50,{"unit_of_measurement":"%"}),("sensor.tesla_energy",35,{"unit_of_measurement":"kWh"}),("sensor.tesla_range",200,{"unit_of_measurement":"km"}),
        ("number.tesla_limit",80,{"unit_of_measurement":"%"}),("binary_sensor.tesla_cable","on",{}),("binary_sensor.evse_plug","on",{}),
        ("sensor.connection","connection-a",{}),("input_number.live_goal",300,{}),
        ("input_datetime.live_departure","2026-11-13 12:00:00",{}),
        ("input_text.live_session",json.dumps({"v":"tesla","u":123,"t":300}),{}),
        ("input_boolean.live_active","on",{}),("counter.live_gen",27,{})]:
        hass.states.async_set(entity_id,value,attrs)
    c = PreviewCoordinator(hass,entry())
    c.store.async_load = AsyncMock(return_value=None)
    c.store.async_save = AsyncMock()
    c.store.async_delay_save = Mock()
    await c.async_initialize()
    return hass,c,next(iter(c.profiles))


@pytest.mark.asyncio
async def test_observer_actions_and_shutdown_have_zero_outgoing_services(tmp_path):
    hass,c,vehicle = await setup(tmp_path)
    source_before = {s.entity_id:s.as_dict() for s in hass.states.async_all()}
    try:
        with patch.object(type(hass.services),"async_call",side_effect=AssertionError("Forbidden outgoing service")), patch.object(type(hass.states),"async_set",side_effect=AssertionError("Forbidden source/helper write")):
            result = await c._async_update_data()
            assert result["control_enabled"] is False
            assert result["vehicles"][vehicle]["demand"]["source_fresh"]
            c.async_request_refresh = AsyncMock()
            await c.async_preview_action("start",vehicle,{"target_range_km":325,"departure":"now"})
            await c.async_preview_action("set_target",vehicle,{"target_range_km":310,"droop_pp":2})
            await c.async_preview_action("stop",vehicle,{})
            await c._async_update_data()
            await c.async_shutdown()
        assert {s.entity_id:s.as_dict() for s in hass.states.async_all()} == source_before
        assert all(s.terminal for s in c.sessions.values())
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_zero_power_keeps_native_mirror_and_meter_credit(tmp_path):
    hass,c,vehicle = await setup(tmp_path)
    try:
        a = await c._async_update_data()
        assert a["active_vehicle"] == vehicle
        request = c.sessions[vehicle]
        initial = request.remaining_kwh
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        b = await c._async_update_data()
        assert not request.terminal
        assert request.remaining_kwh == pytest.approx(initial-2)
        assert b["physical"]["positive"] is False
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_fast_swap_ends_old_preview_without_writes(tmp_path):
    hass,c,vehicle = await setup(tmp_path)
    try:
        await c._async_update_data()
        hass.states.async_set("sensor.connection","connection-b")
        hass.states.async_set("binary_sensor.tesla_cable","off")
        with patch.object(type(hass.services),"async_call",side_effect=AssertionError("Forbidden outgoing service")):
            await c._async_update_data()
        assert c.sessions[vehicle].terminal
        assert hass.states.get("binary_sensor.evse_plug").state == "on"
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_last_reported_is_not_vehicle_measurement(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.soc",80)
    m = measurement(hass,"sensor.soc")
    assert m.measured_at is None and m.received_at is not None
    assert not m.fresh(datetime.now(timezone.utc).timestamp())
    await hass.async_stop()


def test_dst_ambiguity_is_not_silently_guessed():
    assert local_time("2026-10-25 02:15:00","Europe/Amsterdam") is None
    assert local_time("2026-03-29 02:15:00","Europe/Amsterdam") is None
    assert local_time("2026-10-25T02:15:00+01:00","Europe/Amsterdam") is not None


@pytest.mark.asyncio
async def test_price_duration_gaps_duplicates_and_conflicts(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    cfg = {"price_entity":"sensor.price","price_interval_min":15}
    rows = [{"time":"2026-11-12T13:00:00+01:00","price":0.2},{"time":"2026-11-12T14:00:00+01:00","price":0.3}]
    hass.states.async_set("sensor.price",0.2,{"data":rows})
    result,warnings = price_rows(hass,cfg,1794484800)
    assert result[0].end-result[0].start == 900
    assert result[0].end < result[1].start
    hass.states.async_set("sensor.price",0.2,{"data":rows+[rows[0]]})
    assert len(price_rows(hass,cfg,1794484800)[0]) == 2
    hass.states.async_set("sensor.price",0.2,{"data":rows+[{**rows[0],"price":0.4}]})
    assert price_rows(hass,cfg,1794484800) == ([],["conflicting_price_rows"])
    await hass.async_stop()


@pytest.mark.asyncio
async def test_combined_today_tomorrow_and_connection_attribute(tmp_path):
    from custom_components.adaptive_charge_preview.sources import vehicle_snapshot
    hass=HomeAssistant(str(tmp_path))
    today=[{"time":"2026-11-12T13:00:00+01:00","price":0.2}]
    tomorrow=[{"time":"2026-11-13T13:00:00+01:00","price":0.3}]
    hass.states.async_set("sensor.price",0.2,{"prices_today":today,"prices_tomorrow":tomorrow})
    rows,_=price_rows(hass,{"price_entity":"sensor.price","price_attribute":"prices_today,prices_tomorrow"},1794484800)
    assert len(rows)==2
    hass.states.async_set("binary_sensor.plug","on",{"plugged_in_at":"2026-11-12T13:00:00+01:00"})
    value=vehicle_snapshot(hass,{}, {"plug_entity":"binary_sensor.plug","connection_attribute":"plugged_in_at"})
    assert value.connection_id=="2026-11-12T13:00:00+01:00"
    await hass.async_stop()


@pytest.mark.asyncio
async def test_preview_overrides_can_follow_helpers_again_without_start(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        c.async_request_refresh=AsyncMock()
        await c.async_preview_action("set_target",vehicle,{"target_range_km":325,"departure":"now"})
        assert c.target(c.profile(vehicle))==325 and not c.sessions
        with patch.object(type(hass.services),"async_call",side_effect=AssertionError("Forbidden service")):
            await c.async_preview_action("follow_helpers",vehicle,{})
        assert c.target(c.profile(vehicle))==300
        assert "target_km" not in c.values[vehicle] and "departure" not in c.values[vehicle]
        assert not c.sessions
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_real_store_restores_budget_and_flushes_deferred_observations(tmp_path):
    from homeassistant.helpers.storage import Store
    hass,c,vehicle=await setup(tmp_path)
    restored=None
    try:
        c.store=Store(hass,1,f"{DOMAIN}.{c.entry.entry_id}")
        c._last_saved=None
        with patch.object(c.store,"async_save",wraps=c.store.async_save) as immediate:
            await c._async_update_data()
            immediate.assert_not_called()
            hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
            await c._async_update_data()
            balance=c.sessions[vehicle].remaining_kwh
            await c.async_shutdown()
            immediate.assert_called_once()
        restored=PreviewCoordinator(hass,c.entry)
        await restored.async_initialize()
        assert restored.sessions[vehicle].remaining_kwh==pytest.approx(balance)
        result=await restored._async_update_data()
        assert result["vehicles"][vehicle]["session"]["anchor_meter"]==100
        assert restored.sessions[vehicle].remaining_kwh==pytest.approx(balance)
    finally:
        if restored:
            await restored.async_shutdown()
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_removed_profile_cannot_keep_active_slot_or_override(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        c.values[vehicle]={"target_km":400}
        object.__setattr__(c.entry,"subentries",MappingProxyType({}))
        result=await c._async_update_data()
        assert result["active_vehicle"] is None and not result["vehicles"]
        assert not c.sessions and not c.values
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_disconnect_retires_request_even_when_vehicle_data_and_target_fail(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        original=c.sessions[vehicle]
        hass.states.async_set("sensor.tesla_energy","unavailable",{"unit_of_measurement":"kWh"})
        hass.states.async_set("input_number.live_goal",350)
        hass.states.async_set("binary_sensor.tesla_cable","off")
        result=await c._async_update_data()
        assert original.terminal and original.reason=="vehicle_disconnected"
        assert result["active_vehicle"] is None
        assert result["vehicles"][vehicle]["phase"]=="idle"
        assert result["vehicles"][vehicle]["reason"]=="vehicle_disconnected"
        assert result["vehicles"][vehicle]["eligible_for_boost"] is False
        assert result["vehicles"][vehicle]["would_control_mode"]=="Green"
        assert result["vehicles"][vehicle]["plan_provisional"] is True
        # A still-active old native helper cannot grant that command to the
        # same car replugged on a new attachment, even after data recovers.
        hass.states.async_set("binary_sensor.tesla_cable","on")
        hass.states.async_set("sensor.connection","connection-b")
        hass.states.async_set("sensor.tesla_energy",35,{"unit_of_measurement":"kWh"})
        result=await c._async_update_data()
        assert c.sessions[vehicle] is original and result["active_vehicle"] is None
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_continuous_observations_do_not_starve_real_store_write(tmp_path):
    from homeassistant.helpers.storage import Store
    hass,c,vehicle=await setup(tmp_path)
    try:
        c._unsub()
        c._unsub=None
        c.store=Store(hass,1,f"{DOMAIN}.{c.entry.entry_id}")
        real_delay=c.store.async_delay_save
        def accelerated(callback,seconds):
            assert seconds==60
            real_delay(callback,0.01)
        with patch.object(c.store,"async_delay_save",side_effect=accelerated) as delayed:
            await c._async_update_data()
            for energy in (101,102,103):
                hass.states.async_set("sensor.iq_energy",energy,{"unit_of_measurement":"kWh"})
                await c._async_update_data()
            assert delayed.call_count==1
            # Latest state, not a mixed schedule-time snapshot, must be saved.
            c.values[vehicle]={"target_km":350}
            c.values={}
            await asyncio.sleep(0.03)
            await hass.async_block_till_done()
        stored=json.loads(Path(c.store.path).read_text())["data"]
        assert stored["sessions"][vehicle]["last_meter"]==103
        assert stored["sessions"][vehicle]["anchor_meter"]==100
        assert stored["values"]=={} and not c._delay_pending
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_follow_helpers_without_bound_target_is_atomic(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        sub=c.entry.subentries[vehicle]
        object.__setattr__(sub,"data",MappingProxyType({k:v for k,v in sub.data.items() if k!="target_entity"}))
        c.values[vehicle]={"target_km":325,"departure":None}
        with pytest.raises(ValueError,match="bound target"):
            await c.async_preview_action("follow_helpers",vehicle,{})
        assert c.values[vehicle]=={"target_km":325,"departure":None}
        assert not c.sessions
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_departure_changes_while_vehicle_energy_is_unavailable(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        request=c.sessions[vehicle]
        before=(request.anchor_meter,request.anchor_need)
        hass.states.async_set("sensor.tesla_energy","unavailable",{"unit_of_measurement":"kWh"})
        hass.states.async_set("input_datetime.live_departure","2026-11-13 06:00:00")
        result=await c._async_update_data()
        assert request.departure==local_time("2026-11-13 06:00:00","Europe/Amsterdam")
        assert result["vehicles"][vehicle]["departure"]==request.departure
        assert result["vehicles"][vehicle]["target_km"]==300
        assert "prices_missing" in result["vehicles"][vehicle]["warnings"]
        assert (request.anchor_meter,request.anchor_need)==before and not request.terminal
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_new_native_token_before_unplug_is_not_suppressed(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        old=c.sessions[vehicle]
        hass.states.async_set("input_text.live_session",json.dumps({"v":"tesla","u":124,"t":300}))
        hass.states.async_set("counter.live_gen",28)
        hass.states.async_set("binary_sensor.tesla_cable","off")
        await c._async_update_data()
        assert old.terminal
        hass.states.async_set("binary_sensor.tesla_cable","on")
        hass.states.async_set("sensor.connection","connection-b")
        await c._async_update_data()
        assert c.sessions[vehicle] is not old
        assert c.sessions[vehicle].source_token=="tesla:124:28"
        assert not c.sessions[vehicle].terminal
    finally:
        await c.async_shutdown()
        await hass.async_stop()
