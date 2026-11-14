"""Real HA services around the new executor; simulated devices, no real I/O."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.core import Context, Event, State

from custom_components.adaptive_charge_preview.control import FEEDBACK_TIMEOUT
from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
from custom_components.adaptive_charge_preview.core.models import demand
from custom_components.adaptive_charge_preview.sources import vehicle_snapshot
from .test_ha import setup


@pytest.fixture(autouse=True)
def fast_feedback(monkeypatch):
    """Keep device handshakes fast; the real settle guard has a separate test."""
    monkeypatch.setattr("custom_components.adaptive_charge_preview.control.START_SETTLE_SECONDS", 0)


async def controlled(tmp_path, *, stale=False, require_prices=False):
    """A disabled native controller, explicit new request and actual HA bus."""
    hass, c, vehicle = await setup(tmp_path)
    from homeassistant.helpers import entity_registry as er, device_registry as dr, area_registry as ar
    from homeassistant import loader
    from homeassistant.helpers import frame
    from homeassistant.config_entries import ConfigEntries
    loader.async_setup(hass)
    frame.async_setup(hass)
    dr.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    await ar.async_load(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    cfg = {**c.entry.data, "control_mode_entity": "select.ev_mode", "charger_switch_entity": "switch.ev_charger", "control_feedback_entity": "sensor.ev_feedback", "control_report_entity": "sensor.ev_report", "legacy_master_entity": "input_boolean.legacy_master", "pending_entity": "input_text.live_pending", "charging_entity": "binary_sensor.ev_charging", "require_prices_for_control": require_prices}
    object.__setattr__(c.entry, "data", MappingProxyType(cfg))
    c.async_request_refresh = AsyncMock()
    for e, value, attrs in [
        ("input_boolean.legacy_master", "off", {}),
        ("input_boolean.live_active", "off", {}),
        ("input_text.live_session", "{}", {}),
        ("input_text.live_pending", "{}", {}),
        ("select.ev_mode", "Green", {"options": ["Green", "Manual"]}),
        ("switch.ev_charger", "off", {}),
        ("binary_sensor.ev_charging", "off", {}),
        ("sensor.ev_feedback", "Green", {"requested_mode": None, "preferred_mode": "GREEN_CHARGING", "effective_mode": "GREEN_CHARGING"}),
        ("sensor.ev_report", datetime.now(timezone.utc).isoformat(), {}),
    ]:
        hass.states.async_set(e, value, attrs)
    if stale:
        hass.states.async_set("sensor.car_capture", "2020-01-01T00:00:00Z")
    calls = []

    async def mode(call):
        calls.append(("mode", call.data["option"]))
        option = call.data["option"]
        code = "MANUAL_CHARGING" if option == "Manual" else "GREEN_CHARGING"
        hass.states.async_set("select.ev_mode", option, {"options": ["Green", "Manual"]}, context=call.context)
        hass.states.async_set("sensor.ev_feedback", option, {"requested_mode": None, "preferred_mode": code, "effective_mode": code})
        hass.states.async_set("sensor.ev_report", datetime.now(timezone.utc).isoformat())

    async def on(call):
        calls.append(("switch", "on"))
        hass.states.async_set("switch.ev_charger", "on", context=call.context)
        hass.states.async_set("binary_sensor.ev_charging", "on")
        hass.states.async_set("sensor.hw_power", 11000, {"unit_of_measurement": "W"})

    async def off(call):
        calls.append(("switch", "off"))
        hass.states.async_set("switch.ev_charger", "off", context=call.context)
        hass.states.async_set("binary_sensor.ev_charging", "off")
        hass.states.async_set("sensor.hw_power", 0, {"unit_of_measurement": "W"})

    async def limit(call):
        calls.append(("limit", call.data["value"]))
        source = hass.states.get(call.data["entity_id"])
        hass.states.async_set(source.entity_id, call.data["value"], dict(source.attributes), context=call.context)

    for domain, service, func in (("select", "select_option", mode), ("switch", "turn_on", on), ("switch", "turn_off", off), ("number", "set_value", limit)):
        hass.services.async_register(domain, service, func)
    await c.control.set_enabled(True)
    await c.async_preview_action("start", vehicle, {"target_range_km": 250, "departure": "now"})
    return hass, c, vehicle, calls


async def tick(c, times=1):
    for _ in range(times):
        result = await c._async_update_data()
    return result


async def close(hass, c):
    await c.async_shutdown()
    await hass.async_stop()


@pytest.mark.asyncio
async def test_explicit_start_executes_once_after_real_mode_feedback(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        assert calls==[("mode","Manual"),("switch","on")]
        assert not c.sessions[v].terminal and c.control.owned_manual
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("event",["zero_power","anonymous_off","old_connection_edge","missing_capture","missing_soc"])
async def test_pauses_and_delayed_app_do_not_cancel_or_retry_or_green(tmp_path,event):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        before=deepcopy(c.sessions[v].as_dict())
        if event=="zero_power":
            hass.states.async_set("sensor.hw_power",0,{"unit_of_measurement":"W"})
        elif event=="anonymous_off":
            hass.states.async_set("switch.ev_charger","off")
        elif event=="old_connection_edge":
            # The ID remains stable even when its physical edge is many hours old.
            old=datetime.now(timezone.utc)-timedelta(days=2)
            for e in ("binary_sensor.tesla_cable","binary_sensor.evse_plug","sensor.connection"):
                s=hass.states.get(e)
                object.__setattr__(s,"last_updated",old)
        elif event=="missing_capture":
            hass.states.async_set("sensor.car_capture","unavailable")
        else:
            hass.states.async_set("sensor.tesla_soc","unavailable",{"unit_of_measurement":"%"})
        await tick(c,3)
        assert calls==[("mode","Manual"),("switch","on")]
        assert not c.sessions[v].terminal
        assert c.sessions[v].anchor_meter==before["anchor_meter"]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_initial_stale_but_valid_estimate_gets_one_bounded_allowance(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,stale=True)
    try:
        result=await tick(c,2)
        request=c.sessions[v]
        anchor=request.anchor_need
        assert result["vehicles"][v]["plan_provisional"] and not result["vehicles"][v]["demand"]["confirmed"]
        assert calls==[("mode","Manual"),("switch","on")]
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await tick(c)
        assert request.remaining_kwh==pytest.approx(anchor-2)
        hass.states.async_set("sensor.tesla_soc",51,{"unit_of_measurement":"%"})
        await tick(c)
        assert request.anchor_need==anchor and request.anchor_meter==100
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("physical_after_restart",["continues","off"])
async def test_restart_preserves_master_request_budget_and_does_not_require_new_capture(tmp_path,physical_after_restart):
    hass,c,v,calls=await controlled(tmp_path)
    restored=None
    try:
        await tick(c,2)
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await tick(c)
        future=datetime.now(timezone.utc)+timedelta(minutes=10)
        c.sessions[v].set_departure(future.timestamp())
        c.values[v]["departure"]=future.timestamp()
        stored=c._store_data()
        await c.async_shutdown()
        restored=PreviewCoordinator(hass,c.entry)
        restored.store.async_load=AsyncMock(return_value=stored)
        restored.store.async_save=AsyncMock()
        restored.store.async_delay_save=Mock()
        restored.async_request_refresh=AsyncMock()
        await restored.async_initialize()
        hass.states.async_set("sensor.car_capture","unavailable")
        if physical_after_restart=="off":
            hass.states.async_set("switch.ev_charger","off")
            hass.states.async_set("binary_sensor.ev_charging","off")
            hass.states.async_set("sensor.hw_power",0,{"unit_of_measurement":"W"})
        await tick(restored,2)
        assert restored.control.enabled and not restored.sessions[v].terminal
        assert restored.sessions[v].anchor_meter==100
        assert restored.sessions[v].remaining_kwh==pytest.approx(stored["sessions"][v]["remaining_kwh"])
        assert restored.control.fault is None
        assert calls.count(("switch","on"))==(2 if physical_after_restart=="off" else 1)
        assert calls.count(("mode","Manual"))==2
    finally:
        if restored:
            await restored.async_shutdown()
        await close(hass,c)


@pytest.mark.asyncio
async def test_stop_retires_before_physical_stop_and_old_timers_cannot_resume(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        await c.async_control_stop(v)
        assert c.sessions[v].terminal
        await tick(c,4)
        assert calls==[("mode","Manual"),("switch","on"),("switch","off"),("mode","Green")]
        assert not c.control.owned_manual
        c.control._wake(datetime.now(timezone.utc))
        await tick(c,2)
        assert calls.count(("switch","on"))==1
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_master_off_yields_own_manual_but_preserves_command_and_credit(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await tick(c)
        balance=c.sessions[v].remaining_kwh
        await c.async_set_control_enabled(False)
        await tick(c,4)
        assert not c.sessions[v].terminal and c.sessions[v].remaining_kwh==pytest.approx(balance)
        assert calls[-2:]==[("switch","off"),("mode","Green")]
        await c.async_set_control_enabled(True)
        await tick(c,2)
        assert calls[-2:]==[("mode","Manual"),("switch","on")]
        assert c.sessions[v].anchor_meter==100
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_provisional_energy_guard_stops_without_false_goal_confirmation(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,stale=True)
    try:
        await tick(c,2)
        allowance=c.sessions[v].anchor_need
        hass.states.async_set("sensor.iq_energy",100+allowance+0.1,{"unit_of_measurement":"kWh"})
        result=await tick(c,4)
        assert c.sessions[v].phase=="verifying" and c.sessions[v].confirmed_soc is None
        assert not result["vehicles"][v]["demand"]["confirmed"]
        assert calls[-2:]==[("switch","off"),("mode","Green")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_fresh_goal_stops_and_preserves_watching_command(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set("sensor.tesla_range",251,{"unit_of_measurement":"km"})
        hass.states.async_set("sensor.car_capture",datetime.now(timezone.utc).isoformat())
        await tick(c,4)
        assert not c.sessions[v].terminal and c.sessions[v].phase=="watching"
        assert c.sessions[v].confirmed_soc==50
        assert calls[-2:]==[("switch","off"),("mode","Green")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("entity,value",[("input_boolean.legacy_master","on"),("input_text.live_session",'{"v":"id3"}'),("input_text.live_pending",'{"request":"new"}')])
async def test_foreign_controller_yields_without_cleanup_and_never_blindly_resumes(tmp_path,entity,value):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set(entity,value)
        await tick(c)
        assert calls==[("mode","Manual"),("switch","on")]
        hass.states.async_set(entity,"off" if entity.startswith("input_boolean") else "{}")
        await tick(c,2)
        assert c.control.fault=="foreign_controller_requires_new_start"
        assert calls==[("mode","Manual"),("switch","on")]
        assert not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_missing_prices_does_not_block_direct_request(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        await tick(c,3)
        assert calls==[("mode","Manual"),("switch","on")]
        assert not c.sessions[v].terminal and c.control.fault is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_no_new_switch_on_if_manual_feedback_never_arrives(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        # Keep the actual-mode feedback Green despite an optimistic selector.
        async def refuse(call):
            calls.append(("mode",call.data["option"]))
            hass.states.async_set("select.ev_mode",call.data["option"])
        hass.services.async_register("select","select_option",refuse)
        await tick(c,2)
        assert calls==[("mode","Manual")]
        c.control.pending["sent"]-=FEEDBACK_TIMEOUT+1
        await tick(c)
        assert c.control.fault=="actuator_feedback_timeout"
        assert ("switch","on") not in calls
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_manual_selection_can_start_itself_no_redundant_switch_on(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        async def autonomous(call):
            calls.append(("mode",call.data["option"]))
            hass.states.async_set("select.ev_mode","Manual")
            hass.states.async_set("sensor.ev_feedback","Manual",{"requested_mode":None,"preferred_mode":"MANUAL_CHARGING","effective_mode":"MANUAL_CHARGING"})
            hass.states.async_set("sensor.ev_report",datetime.now(timezone.utc).isoformat())
            hass.states.async_set("switch.ev_charger","on")
            hass.states.async_set("binary_sensor.ev_charging","on")
            hass.states.async_set("sensor.hw_power",3600,{"unit_of_measurement":"W"})
        hass.services.async_register("select","select_option",autonomous)
        result=await tick(c,3)
        assert calls==[("mode","Manual")]
        assert result["physical"]["positive"]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_settle_guard_waits_before_single_active_override(tmp_path,monkeypatch):
    monkeypatch.setattr("custom_components.adaptive_charge_preview.control.START_SETTLE_SECONDS",120)
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        assert calls==[("mode","Manual")]
        c.control.pending["mode_ack_at"]-=120
        await tick(c,2)
        assert calls==[("mode","Manual"),("switch","on")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_state",["off","unavailable"])
async def test_saved_physical_attachment_survives_stale_vehicle_cable(tmp_path,app_state):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set("binary_sensor.tesla_cable",app_state)
        await tick(c,3)
        assert not c.sessions[v].terminal
        assert calls==[("mode","Manual"),("switch","on")]
        hass.states.async_set("binary_sensor.evse_plug","off")
        await tick(c,4)
        assert c.sessions[v].terminal
        assert calls[-2:]==[("switch","off"),("mode","Green")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_fast_swap_retires_old_request_and_clears_own_lingering_manual(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set("sensor.connection","connection-b")
        await tick(c,4)
        assert c.sessions[v].terminal
        assert c.control.grant is None
        assert calls[-2:]==[("switch","off"),("mode","Green")]
        await tick(c,2)
        assert calls.count(("switch","on"))==1
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_legacy_stop_generation_retires_new_command_without_adoption(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set("counter.live_gen",28)
        await tick(c,4)
        assert c.sessions[v].terminal and c.sessions[v].reason=="external_native_generation_changed"
        assert calls[-2:]==[("switch","off"),("mode","Green")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_changed_writer_binding_cannot_redirect_an_old_grant(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_switch_entity":"switch.other"}))
        hass.states.async_set("switch.other","on")
        await tick(c,3)
        assert c.control.status=="control_binding_changed_requires_new_start"
        assert calls==[("mode","Manual"),("switch","on"),("switch","off"),("mode","Green")]
        assert hass.states.get("switch.other").state=="on" and not c.control.owned_manual
        assert c.control.fault=="control_binding_changed_requires_new_start"
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_executor_rejects_unconfigured_targets_and_id3_limit_writes(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        with pytest.raises(ValueError,match="allowlist"):
            await c.control._call("switch","turn_on","switch.unrelated")
        with pytest.raises(ValueError,match="allowlist"):
            await c.control._call("number","set_value","number.unrelated",value=90)
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_target_changes_during_delayed_app_preserve_used_meter_energy(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,stale=True)
    try:
        await tick(c,2)
        request=c.sessions[v]
        generation=request.generation
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await tick(c)
        original=request.anchor_need
        await c.async_preview_action("set_target",v,{"target_range_km":260})
        await tick(c)
        raised=request.anchor_need
        assert raised>original and request.generation==generation and request.anchor_meter==100
        assert request.remaining_kwh==pytest.approx(raised-2)
        await c.async_preview_action("set_target",v,{"target_range_km":245})
        await tick(c)
        assert request.anchor_need<original and request.remaining_kwh==pytest.approx(request.anchor_need-2)
        assert calls.count(("switch","on"))==1
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_valid_current_price_row_releases_waiting_explicit_request(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        now=datetime.now(timezone.utc).timestamp()
        await c.async_preview_action("set_target",v,{"departure":datetime.fromtimestamp(now+10800,timezone.utc).isoformat()})
        await tick(c)
        assert calls==[]
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"price_entity":"sensor.prices"}))
        hass.states.async_set("sensor.prices",0.25,{"data":[{"time":now-60,"end":now+14400,"price":0.25}]})
        result=await tick(c,3)
        assert result["price_feed_valid"]
        assert calls==[("mode","Manual"),("switch","on")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_absolute_energy_cap_works_even_with_zero_meter_origin(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        hass.states.async_set("sensor.iq_energy",0,{"unit_of_measurement":"kWh"})
        c.control.grant["max_kwh"]=0.1
        await tick(c,2)
        hass.states.async_set("sensor.iq_energy",0.11,{"unit_of_measurement":"kWh"})
        await tick(c,4)
        assert c.control.fault=="command_energy_or_manual_time_cap"
        assert calls[-2:]==[("switch","off"),("mode","Green")]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,bad",[("manual_seconds","5"),("manual_seconds",float('nan')),("max_kwh",True),("max_kwh",-1),("generation",True),("binding",None)])
async def test_malformed_persisted_authority_never_enables_control(tmp_path,field,bad):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        stored=c.control.as_dict()
        stored["grant"][field]=bad
        from custom_components.adaptive_charge_preview.control import ChargerControl
        restored=ChargerControl(c)
        restored.restore(stored)
        assert not restored.enabled and restored.grant is None and restored.fault
        assert calls==[]
    finally:
        await close(hass,c)


def change_profile(c,v,**values):
    sub=c.entry.subentries[v]
    object.__setattr__(sub,"data",MappingProxyType({**sub.data,**values}))


@pytest.mark.asyncio
async def test_optional_tesla_limit_raise_and_owned_restore_are_read_back(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action("set_target",v,{"target_range_km":330})
        await tick(c,3)
        assert calls[:3]==[("limit",84.0),("mode","Manual"),("switch","on")]
        assert hass.states.get("number.tesla_limit").state=="84.0"
        assert c.sessions[v].anchor_meter==100 and c.sessions[v].anchor_need>24.15
        await c.async_control_stop(v)
        await tick(c,5)
        assert calls[-3:]==[("switch","off"),("mode","Green"),("limit",80.0)]
        assert hass.states.get("number.tesla_limit").state=="80.0"
        assert c.control.limit is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum",[80,95])
async def test_limit_ceiling_and_user_override_are_not_overwritten(tmp_path,maximum):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=maximum)
        await c.async_preview_action("set_target",v,{"target_range_km":330})
        await tick(c,3)
        if maximum==80:
            assert not any(call[0]=="limit" for call in calls)
        else:
            hass.states.async_set("number.tesla_limit",82,{"unit_of_measurement":"%"})
            await tick(c)
            assert c.control.limit is None and c.control.grant["limit_overridden"]
        await c.async_control_stop(v)
        await tick(c,4)
        assert ("limit",80.0) not in calls
        assert hass.states.get("number.tesla_limit").state==("80" if maximum==80 else "82")
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_no_manual_before_limit_raise_readback(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action("set_target",v,{"target_range_km":330})
        async def ignored(call):
            calls.append(("limit",call.data["value"]))
        hass.services.async_register("number","set_value",ignored)
        await tick(c,3)
        assert calls==[("limit",84.0)]
        c.control.limit["sent"]-=FEEDBACK_TIMEOUT+1
        await tick(c,2)
        assert c.control.fault=="limit_readback_timeout" and calls==[("limit",84.0)]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_id3_cannot_write_a_limit_even_when_a_writer_was_configured(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,kind="id3",allow_limit_raise=True)
        await c.control.authorize(v)
        with pytest.raises(ValueError,match="allowlist"):
            await c.control._call("number","set_value","number.tesla_limit",value=90)
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_tesla_vehicle_start_is_only_sent_for_explicit_new_block(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,vehicle_charge_switch_entity="switch.tesla_charge")
        hass.states.async_set("switch.tesla_charge","off")
        await c.control.authorize(v)
        vehicle_calls=[]
        async def route(call):
            if call.data["entity_id"]=="switch.tesla_charge":
                vehicle_calls.append("start")
                hass.states.async_set("switch.tesla_charge","on",context=call.context)
            else:
                calls.append(("switch","on"))
                hass.states.async_set("switch.ev_charger","on",context=call.context)
                hass.states.async_set("binary_sensor.ev_charging","on")
                hass.states.async_set("sensor.hw_power",11000,{"unit_of_measurement":"W"})
        hass.services.async_register("switch","turn_on",route)
        await tick(c,3)
        assert vehicle_calls==["start"]
        hass.states.async_set("switch.tesla_charge","off")
        hass.states.async_set("sensor.hw_power",0,{"unit_of_measurement":"W"})
        await tick(c,3)
        assert vehicle_calls==["start"] and not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_timer_boundary_uses_current_plan_and_old_callback_has_no_positive_payload(tmp_path):
    from custom_components.adaptive_charge_preview.core.models import Tariff
    from custom_components.adaptive_charge_preview.core.planner import plan
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await c.async_preview_action("set_target",v,{"target_range_km":205})
        await c._observe()
        now=datetime.now(timezone.utc).timestamp()
        end=now+3600
        tariffs=[Tariff(now-1,now+900,0.6),Tariff(now+900,now+1800,0.1),Tariff(now+1800,end,0.4)]
        need=c.sessions[v].remaining_kwh
        planned=plan(now,end,need,0,tariffs,reserve_min=0)
        assert planned.mode=="Green" and planned.windows[0].start==now+900
        row={"plan":planned.as_dict(),"eligible_for_boost":True,"would_control_mode":planned.mode}
        result={"vehicles":{v:row}}
        timers=[]
        def capture(hass,callback,when):
            timers.append((callback,when))
            return Mock()
        with patch("custom_components.adaptive_charge_preview.control.async_track_point_in_utc_time",side_effect=capture):
            await c.control.step(result,now)
            assert calls==[] and timers[-1][1].timestamp()==pytest.approx(now+900)
            callback=timers[-1][0]
            callback(datetime.now(timezone.utc))
            assert calls==[]
            arrived=plan(now+901,end,need,0,tariffs,reserve_min=0)
            assert arrived.mode=="Manual"
            row.update(plan=arrived.as_dict(),would_control_mode=arrived.mode)
            await c.control.step(result,now+901)
            assert calls==[("mode","Manual")]
            await c.async_control_stop(v)
            callback(datetime.now(timezone.utc))
            await c.control.step(result,now+902)
            assert ("switch","on") not in calls
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('charging',['unset','unavailable','on'])
async def test_optional_charging_binary_does_not_block_independent_physical_proof(tmp_path,charging):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        if charging=='unset':
            data=dict(c.entry.data); data.pop('charging_entity')
            object.__setattr__(c.entry,'data',MappingProxyType(data))
        else:
            hass.states.async_set('binary_sensor.ev_charging',charging)
        await tick(c,2)
        assert calls==[('mode','Manual'),('switch','on')]
        await c.async_control_stop(v)
        await tick(c,3)
        assert not c.control.owned_manual and calls[-2:]==[('switch','off'),('mode','Green')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_missing_native_helper_stops_owned_manual_then_resumes_saved_allowance(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        anchor=c.sessions[v].anchor_meter
        hass.states.async_set('input_boolean.live_active','unavailable')
        await tick(c)
        assert calls==[('mode','Manual'),('switch','on')]  # A single missing report never cycles the contactor.
        c.control._interlock_since-=FEEDBACK_TIMEOUT+1
        await tick(c,3)
        assert not c.control.owned_manual and not c.sessions[v].terminal
        assert c.control.fault=='waiting_legacy_request_state'
        assert calls[-2:]==[('switch','off'),('mode','Green')]
        hass.states.async_set('input_boolean.live_active','off')
        await tick(c,4)
        assert c.control.fault is None and c.control.owned_manual
        assert c.sessions[v].anchor_meter==anchor
        assert calls[-2:]==[('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_caps_still_run_when_binding_verification_is_unavailable(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        c.control.grant['manual_since']-=8*3600+1
        hass.states.async_set('sensor.ev_feedback','unavailable')
        await tick(c)
        assert c.control.fault=='command_energy_or_manual_time_cap'
        assert calls[-1]==('switch','off')
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect',['unplug','swap'])
async def test_limit_restore_targets_original_tesla_after_connection_ends(tmp_path,disconnect):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        assert calls[0]==('limit',84.0)
        if disconnect=='unplug':
            hass.states.async_set('binary_sensor.evse_plug','off')
        else:
            hass.states.async_set('sensor.connection','different-car-connection')
        await tick(c,6)
        assert c.sessions[v].terminal and c.control.limit is None
        assert calls[-1]==('limit',80.0) and c.control.limit_audit['restored']
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_failed_start_keeps_existing_generation_session_and_authority_atomic(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        before=deepcopy(c._store_data())
        with patch.object(c.control,'validate_start',side_effect=ValueError('unavailable')):
            with pytest.raises(ValueError,match='unavailable'):
                await c.async_preview_action('start',v,{'target_range_km':280})
        assert c._store_data()==before and c.control.owns(v)
        assert calls==[('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_connection_attribute_on_plug_is_shared_by_snapshot_and_executor(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        cfg=dict(c.entry.data); cfg.pop('connection_entity'); cfg['connection_attribute']='connection_id'
        object.__setattr__(c.entry,'data',MappingProxyType(cfg))
        hass.states.async_set('binary_sensor.evse_plug','on',{'connection_id':'plug-attribute-a'})
        await c.async_preview_action('start',v,{'target_range_km':250,'departure':'now'})
        await tick(c,2)
        assert c.control.grant['connection_id']=='plug-attribute-a' and c.control.owned_manual
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_missing_vehicle_values_can_use_saved_reference_for_goal_change(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        anchor=c.sessions[v].anchor_need
        hass.states.async_set('sensor.iq_energy',102,{'unit_of_measurement':'kWh'})
        for e in ['sensor.tesla_soc','sensor.tesla_energy','sensor.tesla_range']:
            s=hass.states.get(e); hass.states.async_set(e,'unavailable',dict(s.attributes))
        await c.async_preview_action('set_target',v,{'target_range_km':260})
        result=await tick(c)
        row=result['vehicles'][v]
        assert row['calculation_from_saved_reference'] and row['plan_provisional']
        assert c.sessions[v].anchor_need>anchor and c.sessions[v].remaining_kwh==pytest.approx(c.sessions[v].anchor_need-2)
        assert calls==[('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_external_mode_interrupt_during_save_prevents_the_pending_positive_call(tmp_path):
    import asyncio
    hass,c,v,calls=await controlled(tmp_path)
    try:
        fired=False
        async def interrupt(_data):
            nonlocal fired
            if c.control.owned_manual and not fired:
                fired=True
                c.control._service_seen(Event('call_service',{'domain':'select','service':'select_option','service_data':{'entity_id':'select.ev_mode','option':'Green'}},context=Context()))
                await asyncio.sleep(0)
        c.store.async_save=AsyncMock(side_effect=interrupt)
        await tick(c,2)
        await hass.async_block_till_done()
        assert calls==[] and c.control.fault=='external_mode_command_requires_new_start'
        assert not c.sessions[v].terminal and not c.control.owned_manual
        assert c.store.async_save.call_args.args[0]['control']['fault']=='external_mode_command_requires_new_start'
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_user_stop_all_target_is_not_mistaken_for_a_phase_pause(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        await hass.services.async_call('switch','turn_off',{'entity_id':'all'},blocking=True,context=Context(user_id='user-test'))
        await hass.async_block_till_done()
        await tick(c,3)
        assert c.sessions[v].terminal and not c.control.owned_manual
        assert sum(item==('switch','on') for item in calls)==1
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_unload_timeout_persists_cleanup_instead_of_failed_unload(tmp_path,monkeypatch):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        async def ignored(call):
            calls.append(('switch','ignored-off'))
        hass.services.async_register('switch','turn_off',ignored)
        monkeypatch.setattr('custom_components.adaptive_charge_preview.control.FEEDBACK_TIMEOUT',-5)
        await c.control.prepare_unload()
        assert c.control.fault=='unload_stop_unconfirmed' and c.control.owned_manual
        assert c._store_data()['control']['cleanup_required']
        await c.async_shutdown()
        assert c.control._closed
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_reconfigure_writers_is_blocked_but_price_source_can_change(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        assert c.control.binding_change_blocked({**c.entry.data,'charger_switch_entity':'switch.other'})
        assert not c.control.binding_change_blocked({**c.entry.data,'price_entity':'sensor.new_prices'})
        assert c.control.binding_change_blocked({**c.profiles[v],'limit_writer_entity':'number.other'},v)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_unplug_with_cleared_connection_token_finishes_negative_obligations(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        hass.states.async_set('binary_sensor.evse_plug','off')
        hass.states.async_set('sensor.connection','unavailable')
        await tick(c,7)
        assert c.sessions[v].terminal and not c.control.owned_manual and c.control.limit is None
        assert calls[-3:]==[('switch','off'),('mode','Green'),('limit',80.0)]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_single_missing_legacy_report_recovers_without_service_cycle(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set('input_text.live_pending','unavailable')
        await tick(c)
        assert c.control.fault is None
        with pytest.raises(ValueError,match='waiting_legacy_request_state'):
            await c.control._call('switch','turn_on','switch.ev_charger')
        hass.states.async_set('input_text.live_pending','{}')
        await tick(c,2)
        assert calls==[('mode','Manual'),('switch','on')] and not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_external_charger_mode_still_restores_own_vehicle_limit(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await hass.services.async_call('select','select_option',{'entity_id':'select.ev_mode','option':'Green'},blocking=True,context=Context(user_id='user'))
        await hass.async_block_till_done()
        before=len(calls)
        await tick(c,3)
        assert calls[before:]==[('limit',80.0)] and c.control.limit is None
        assert not c.control.owned_manual and c.control.fault=='external_mode_command_requires_new_start'
        assert not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_closed_or_replaced_session_cannot_retrigger_an_old_cap(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c)
        c.control.owned_manual,c.control.pending=False,None
        request=c.sessions[v]
        request.stop()
        request.last_meter=request.origin_meter+c.control.grant['max_kwh']
        await tick(c)
        assert c.control.fault is None
        request.terminal=False
        request.generation+=1
        await tick(c)
        assert c.control.fault is None and c.control.status=='attachment_or_command_replaced'
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_changed_limit_writer_identity_is_audited_not_a_permanent_lock(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        c.control.limit['writer_identity']=['former-registration']
        notices=[]
        async def notify(call): notices.append(call.data['message'])
        hass.services.async_register('persistent_notification','create',notify)
        await c.async_control_stop(v)
        await tick(c,4)
        assert not c.control.owned_manual and c.control.limit is None
        assert c.control.limit_audit['retired_reason']=='writer_identity_changed'
        assert any('number.tesla_limit' in message and '80.0%' in message and '84.0%' in message for message in notices)
        assert not c.control.binding_change_blocked({**c.entry.data,'charger_switch_entity':'switch.other'})
        assert c.control.validate_start(v) is not None
        assert ('limit',80.0) not in calls
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_sleeping_tesla_limit_obligation_is_retained_without_writes(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await c.async_control_stop(v)
        hass.states.async_set('number.tesla_limit','unavailable',{'unit_of_measurement':'%'})
        await tick(c,4)
        obligation=deepcopy(c.control.limit)
        before=c.store.async_save.call_count
        await tick(c,5)
        assert c.control.limit==obligation and c.store.async_save.call_count==before
        hass.states.async_set('number.tesla_limit',84,{'unit_of_measurement':'%'})
        await tick(c,2)
        assert c.control.limit is None and calls[-1]==('limit',80.0)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('target',['entity','all','device','entity_area','device_area','entity_label','device_label','area_label'])
async def test_user_stop_target_routes_are_resolved_for_pinned_charger(tmp_path,target):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        from homeassistant.helpers import entity_registry as er,device_registry as dr,area_registry as ar
        item=SimpleNamespace(device_id='charger-device',area_id='charger-area' if target!='device_area' else None,labels={'charger-label'} if target=='entity_label' else set())
        device=SimpleNamespace(area_id='charger-area',labels={'charger-label'} if target=='device_label' else set())
        area=SimpleNamespace(labels={'charger-label'} if target=='area_label' else set())
        registries={er.DATA_REGISTRY:SimpleNamespace(entities={},async_get=lambda e:item if e=='switch.ev_charger' else None),dr.DATA_REGISTRY:SimpleNamespace(async_get=lambda _:device),ar.DATA_REGISTRY:SimpleNamespace(async_get_area=lambda _:area)}
        data={'entity_id':'switch.ev_charger'} if target=='entity' else {'entity_id':'all'} if target=='all' else {'device_id':'charger-device'} if target=='device' else {'area_id':'charger-area'} if target.endswith('area') else {'label_id':'charger-label'}
        with patch.dict(hass.data,registries):
            c.control._service_seen(Event('call_service',{'domain':'switch','service':'turn_off','service_data':data},context=Context(user_id='user')))
        await hass.async_block_till_done()
        assert c.sessions[v].terminal and c.control._stop_requested
        await tick(c,3)
        assert calls[-2:]==[('switch','off'),('mode','Green')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_completed_unplug_cleanup_does_not_keep_writing_the_store(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        hass.states.async_set('binary_sensor.evse_plug','off')
        hass.states.async_set('sensor.connection','unavailable')
        await tick(c,4)
        before=c.store.async_save.call_count
        await tick(c,10)
        assert c.store.async_save.call_count==before and not c.control.owned_manual
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_missing',[False,True])
async def test_missing_restored_session_still_cleans_its_pinned_owned_manual(tmp_path,legacy_missing):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        c.sessions.pop(v)
        if legacy_missing:
            hass.states.async_set('input_text.live_pending','unavailable')
        await tick(c,4)
        assert not c.control.owned_manual and c.control.fault
        assert calls[-2:]==[('switch','off'),('mode','Green')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('return_path',['wakeup','user_override'])
async def test_dormant_tesla_limit_does_not_block_id3_and_keeps_writer_identity(tmp_path,return_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await c.async_control_stop(v)
        hass.states.async_set('number.tesla_limit','unavailable',{'unit_of_measurement':'%'})
        await tick(c,4)
        other='id3-test-profile'
        data={**c.profiles[v],'kind':'id3','limit_writer_entity':None,'allow_limit_raise':False,'range_entity':'sensor.id3_full_range','limit_entity':'sensor.id3_limit','cable_entity':'binary_sensor.id3_cable'}
        sub=SimpleNamespace(subentry_id=other,subentry_type='vehicle',title='ID3',data=MappingProxyType(data))
        object.__setattr__(c.entry,'subentries',MappingProxyType({**c.entry.subentries,other:sub}))
        hass.states.async_set('sensor.id3_full_range',400,{'unit_of_measurement':'km'})
        hass.states.async_set('sensor.id3_limit',80,{'unit_of_measurement':'%'})
        hass.states.async_set('binary_sensor.id3_cable','on')
        hass.states.async_set('sensor.connection','new-id3-attachment')
        await c.async_preview_action('start',other,{'target_range_km':250,'departure':'now'})
        await tick(c,2)
        assert c.control.owns(other) and c.control.owned_manual and c.control.limit is None
        assert len(c.control.dormant_limits)==1
        from custom_components.adaptive_charge_preview.control import ChargerControl
        restored=ChargerControl(c); restored.restore(c.control.as_dict())
        assert restored.enabled and restored.dormant_limits[0]['vehicle_id']==v
        before=len(calls)
        if return_path=='wakeup':
            hass.states.async_set('number.tesla_limit',84,{'unit_of_measurement':'%'})
        else:
            await hass.services.async_call('number','set_value',{'entity_id':'number.tesla_limit','value':82},blocking=True,context=Context(user_id='user'))
            await hass.async_block_till_done()
        await tick(c,3)
        assert not c.control.dormant_limits and c.control.owns(other) and c.control.owned_manual
        assert calls[before:]==[('limit',80.0 if return_path=='wakeup' else 82)]
    finally:
        await close(hass,c)


def live_prices(hass,c):
    now=datetime.now(timezone.utc).timestamp()
    object.__setattr__(c.entry,'data',MappingProxyType({**c.entry.data,'price_entity':'sensor.prices','require_prices_for_control':True}))
    attrs={'data':[{'time':now-60,'end':now+3600,'price':0.25}]}
    hass.states.async_set('sensor.prices',0.25,attrs)
    return attrs


@pytest.mark.asyncio
async def test_enever_loss_yields_owned_manual_but_permits_independent_green(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        now=datetime.now(timezone.utc).timestamp()
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(now+10800,timezone.utc).isoformat()})
        attrs=live_prices(hass,c)
        attrs['data'][0]['end']=now+14400
        hass.states.async_set('sensor.prices',0.25,attrs)
        await tick(c,2)
        anchor=c.sessions[v].anchor_meter
        hass.states.async_set('sensor.prices','unavailable',attrs)
        result=await tick(c,4)
        assert not result['price_feed_valid'] and not c.control.owned_manual and not c.sessions[v].terminal
        assert calls[-2:]==[('switch','off'),('mode','Green')]
        before=list(calls)
        hass.states.async_set('sensor.hw_power',2000,{'unit_of_measurement':'W'})
        hass.states.async_set('switch.ev_charger','on')  # Enphase's own solar control is allowed.
        hass.states.async_set('sensor.iq_energy',101,{'unit_of_measurement':'kWh'})
        await tick(c,3)
        assert calls==before and c.control.fault is None
        assert result['vehicles'][v]['plan']['reason']=='unpriced_deadline_fallback'
        hass.states.async_set('sensor.prices',0.25,attrs)
        await tick(c,3)
        assert c.control.owned_manual and c.control.fault is None
        assert c.sessions[v].anchor_meter==anchor and c.sessions[v].last_meter==101
        assert calls[len(before):]==[('mode','Manual')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_enever_is_rechecked_before_the_actual_manual_dispatch(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        now=datetime.now(timezone.utc).timestamp()
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(now+10800,timezone.utc).isoformat()})
        attrs=live_prices(hass,c)
        attrs['data'][0]['end']=now+14400
        hass.states.async_set('sensor.prices',0.25,attrs)
        fired=False
        async def interrupt(_data):
            nonlocal fired
            if c.control.owned_manual and not fired:
                fired=True
                hass.states.async_set('sensor.prices','unavailable',attrs)
        c.store.async_save=AsyncMock(side_effect=interrupt)
        await tick(c)
        assert calls==[] and not c.control.owned_manual and c.control.fault is None
        assert c.control.grant['manual_since'] is None
        hass.states.async_set('sensor.prices',0.25,attrs)
        await tick(c,2)
        assert calls==[('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_long_source_reload_does_not_exhaust_cleanup_or_require_new_start(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,2)
        for e in ['select.ev_mode','switch.ev_charger']:
            hass.states.async_set(e,'unavailable')
        await tick(c)
        c.control._interlock_since-=FEEDBACK_TIMEOUT+1
        await tick(c)
        c.control.pending['sent']-=300
        c.control.pending['last_attempt']-=300
        await tick(c,4)
        assert c.control.pending['attempts']==1
        hass.states.async_set('select.ev_mode','Manual',{'options':['Green','Manual']})
        hass.states.async_set('switch.ev_charger','on')
        await tick(c,9)
        assert c.control.fault is None and c.control.owned_manual and not c.sessions[v].terminal
        assert calls[-4:]==[('switch','off'),('mode','Green'),('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_limit_retirement_notice_is_not_consumed_by_a_sleeping_reload(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await c.async_control_stop(v)
        hass.states.async_set('number.tesla_limit','unavailable',{'unit_of_measurement':'%'})
        await tick(c,4)
        notices=[]
        async def notify(call): notices.append(call.data['message'])
        hass.services.async_register('persistent_notification','create',notify)
        await c.control.prepare_unload()
        assert notices==[] and c.control.limit is not None
        c.control.limit['writer_identity']=['changed-registration']
        await c.control.restore_limit()
        assert len(notices)==1 and '84.0%' in notices[0] and '80.0%' in notices[0]
        assert c.control.limit is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_price_outage_never_stops_an_urgent_request(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        attrs=live_prices(hass,c)
        await tick(c,2)
        before=list(calls)
        hass.states.async_set('sensor.prices','unavailable',attrs)
        await tick(c,4)
        assert calls==before and c.control.owned_manual
        assert c.control.fault is None and not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('target',['vehicle','charger'])
async def test_undispatched_switch_start_is_retried_after_a_transient_refusal(tmp_path,target):
    from custom_components.adaptive_charge_preview.control import DispatchRefused
    hass,c,v,calls=await controlled(tmp_path)
    try:
        if target=='vehicle':
            change_profile(c,v,vehicle_charge_switch_entity='switch.tesla_charge')
            hass.states.async_set('switch.tesla_charge','off')
            await c.control.authorize(v)
        expected='switch.tesla_charge' if target=='vehicle' else 'switch.ev_charger'
        real=c.control._call
        refused=False; dispatched=[]
        async def guard(domain,service,entity_id,**values):
            nonlocal refused
            if entity_id==expected and service=='turn_on' and not refused:
                refused=True
                raise DispatchRefused('waiting_legacy_request_state')
            dispatched.append((service,entity_id))
            await real(domain,service,entity_id,**values)
        with patch.object(c.control,'_call',side_effect=guard):
            await tick(c,2)
            assert refused and c.control.pending is not None and c.control.fault is None
            assert not c.control.pending.get('vehicle_start_sent',False)
            await tick(c,4)
        assert dispatched.count(('turn_on',expected))==1 and c.control.pending is None
        assert c.control.owned_manual and not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('malformation',['dormant_not_list','too_many','binding_kind','duplicate_vehicle','wrong_active_vehicle'])
async def test_invalid_limit_authority_never_restores_a_writer(tmp_path,malformation):
    from custom_components.adaptive_charge_preview.control import ChargerControl
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c)
        data=c.control.as_dict()
        if malformation=='dormant_not_list': data['dormant_limits']={}
        elif malformation=='too_many': data['dormant_limits']=[deepcopy(data['limit']) for _ in range(33)]
        elif malformation=='binding_kind': data['limit']['vehicle_binding']['kind']='id3'
        elif malformation=='duplicate_vehicle': data['dormant_limits']=[deepcopy(data['limit'])]
        else: data['limit']['vehicle_id']='wrong-car'
        restored=ChargerControl(c); restored.restore(data)
        assert not restored.enabled and not restored.grant and restored.fault
        assert calls==[('limit',84.0)]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_dormant_limit_wakeup_runs_through_real_source_listener_and_refresh(tmp_path):
    import asyncio
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await c.async_control_stop(v)
        hass.states.async_set('number.tesla_limit','unavailable',{'unit_of_measurement':'%'})
        await tick(c,4)
        c.control.dormant_limits.append(c.control.limit)
        c.control.limit,c.control.grant,c.control.enabled=None,None,False
        c.async_request_refresh=PreviewCoordinator.async_request_refresh.__get__(c)
        hass.states.async_set('number.tesla_limit',84,{'unit_of_measurement':'%'})
        await hass.async_block_till_done()
        await asyncio.sleep(10.2)  # Exercise the real HA debouncer, not a manually ticked executor.
        await hass.async_block_till_done()
        assert c.data is not None and not c.control.dormant_limits
        assert calls[-1]==('limit',80.0) and not c.control.owned_manual
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_external_mode_then_reload_restores_vehicle_limit_without_charger_cleanup(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        change_profile(c,v,allow_limit_raise=True,maximum_soc=95)
        await c.async_preview_action('set_target',v,{'target_range_km':330})
        await tick(c,3)
        await hass.services.async_call('select','select_option',{'entity_id':'select.ev_mode','option':'Green'},blocking=True,context=Context(user_id='user'))
        await hass.async_block_till_done()
        before=len(calls)
        await c.control.prepare_unload()
        assert c.control.limit is None and calls[before:]==[('limit',80.0)]
        assert c.control.limit_audit.get('restored') is True
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_ignored_actual_start_warns_without_retry_or_cancel(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        notices=[]
        async def notify(call): notices.append(call.data['message'])
        hass.services.async_register('persistent_notification','create',notify)
        async def ignored(call): calls.append(('switch','ignored-on'))
        hass.services.async_register('switch','turn_on',ignored)
        await tick(c,2)
        c.control.grant['start_watch_since']-=FEEDBACK_TIMEOUT+1
        await tick(c,4)
        assert calls==[('mode','Manual'),('switch','ignored-on')]
        assert len(notices)==1 and c.control.fault is None and not c.sessions[v].terminal
        assert c.control.status=='physical_start_unconfirmed'
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_a_phase_pause_after_proven_start_is_not_a_new_start_warning(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        assert c.control.grant['start_watch_since'] is None
        notices=[]
        async def notify(call): notices.append(call.data['message'])
        hass.services.async_register('persistent_notification','create',notify)
        hass.states.async_set('sensor.hw_power',0,{'unit_of_measurement':'W'})
        await tick(c,4)
        assert notices==[] and not c.sessions[v].terminal and c.control.fault is None
        assert calls==[('mode','Manual'),('switch','on')]
    finally:
        await close(hass,c)
