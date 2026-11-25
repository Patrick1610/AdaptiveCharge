"""Enphase AI uses one measured energy ledger across both charger modes."""

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest
from homeassistant.core import Context, Event

from custom_components.adaptive_charge_preview.const import AI_OPTIMISATION
from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
from custom_components.adaptive_charge_preview.core.models import Tariff
from .test_control import close, controlled, fast_feedback, tick


def set_meter(hass, kwh):
    hass.states.async_set("sensor.iq_energy", kwh, {"unit_of_measurement": "kWh"})


def narrow_request(c, vehicle):
    """Plan a future fallback block without replacing the order or its meter anchor."""
    departure = (datetime.now(timezone.utc) + timedelta(hours=5)).timestamp()
    c.values[vehicle]["departure"] = departure
    c.sessions[vehicle].set_departure(departure)


@pytest.mark.asyncio
@pytest.mark.parametrize("economic", [False, True])
async def test_ai_plans_full_remaining_energy_despite_solar_forecast(tmp_path, economic):
    hass, c, vehicle, _ = await controlled(tmp_path, ai=True)
    try:
        object.__setattr__(c.entry, "data", MappingProxyType({**c.entry.data, "economic_enabled": economic}))
        now = datetime.now(timezone.utc).timestamp()
        tariffs = [Tariff(now, now + 3600, 0.35, 0.10, 10), Tariff(now + 3600, now + 7200, 0.15, 0.10, 10)]
        result = c._plan(now, now + 7200, 5, 0, tariffs, 11, c.sessions[vehicle], True)
        assert result.mode == "Smart"
        assert result.windows[0].start >= now + 3600
        assert sum(window.kwh for window in result.windows) == pytest.approx(5)
        assert result.expected_solar_kwh == 0
        assert result.green_windows == result.pause_windows == ()
        assert result.cost == pytest.approx(0.75)
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_credits_smart_before_first_tick_and_preserves_credit_across_handoff(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        set_meter(hass, 102)
        await tick(c)
        request = c.sessions[vehicle]
        initial = request.anchor_need
        assert request.origin_meter == request.anchor_meter == 100
        assert request.remaining_kwh == pytest.approx(initial - 2)
        assert calls == []
        c.values[vehicle]["departure"] = None
        await tick(c, 3)
        assert calls == [("mode", "Manual"), ("switch", "on")]
        set_meter(hass, 103)
        narrow_request(c, vehicle)
        await tick(c, 3)
        assert calls[-2:] == [("switch", "off"), ("mode", "Smart")]
        set_meter(hass, 105)
        result = await tick(c, 3)
        assert request.remaining_kwh == pytest.approx(initial - 5)
        row = result["vehicles"][vehicle]
        assert sum(w["kwh"] for w in row["plan"]["windows"]) == pytest.approx(initial - 5)
        assert calls.count(("mode", "Smart")) == 1
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_goal_allows_smart_but_stop_blocks_it_even_after_reload(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    reloaded = None
    try:
        await tick(c, 3)
        hass.states.async_set("sensor.tesla_soc", 64, {"unit_of_measurement": "%"})
        hass.states.async_set("sensor.tesla_range", 256, {"unit_of_measurement": "km"})
        hass.states.async_set("sensor.tesla_energy", 44.8, {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.car_capture", datetime.now(timezone.utc).isoformat())
        set_meter(hass, 112)
        await tick(c, 4)
        assert c.sessions[vehicle].phase == "watching"
        assert calls[-2:] == [("switch", "off"), ("mode", "Smart")]
        set_meter(hass, 113)
        hass.states.async_set("sensor.hw_power", 4100, {"unit_of_measurement": "W"})
        hass.states.async_set("switch.ev_charger", "on")
        await tick(c, 2)
        assert calls[-1] == ("mode", "Smart")
        await c.async_preview_action("stop", vehicle, {})
        await tick(c, 3)
        assert calls[-1] == ("switch", "off")
        assert c.control.status == "ai_stop_confirmed"

        saved = c._store_data()
        await c.async_shutdown()
        reloaded = PreviewCoordinator(hass, c.entry)
        reloaded.store.async_load = AsyncMock(return_value=saved)
        reloaded.store.async_save = AsyncMock()
        reloaded.store.async_delay_save = Mock()
        reloaded.async_request_refresh = AsyncMock()
        await reloaded.async_initialize()
        hass.states.async_set("sensor.hw_power", 4100, {"unit_of_measurement": "W"})
        hass.states.async_set("switch.ev_charger", "on")
        await tick(reloaded, 3)
        assert calls[-2:] == [("switch", "off"), ("switch", "off")]
        assert calls.count(("mode", "Smart")) == 1
        assert reloaded.sessions[vehicle].terminal
    finally:
        if reloaded:
            await reloaded.async_shutdown()
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_hard_energy_cap_also_stops_smart_with_unknown_selector(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True, unknown_selector=True)
    try:
        narrow_request(c, vehicle)
        c.control.grant["max_kwh"] = 3
        await tick(c)
        assert calls == []
        hass.states.async_set("sensor.hw_power", 4200, {"unit_of_measurement": "W"})
        hass.states.async_set("switch.ev_charger", "on")
        set_meter(hass, 103)
        await tick(c, 3)
        assert calls == [("switch", "off")]
        assert c.control.fault == "command_energy_or_manual_time_cap"
        assert c.control.status == "ai_stop_confirmed"
        assert not c.control.owned_manual
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_can_start_using_smart_preference_when_selector_reads_unknown(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True, unknown_selector=True)
    try:
        await tick(c, 3)
        assert calls == [("mode", "Manual"), ("switch", "on")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_never_recredits_energy_when_a_delayed_soc_update_arrives(tmp_path):
    hass, c, vehicle, _ = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        await tick(c)
        request = c.sessions[vehicle]
        allowance = request.anchor_need
        capture = datetime.now(timezone.utc)
        set_meter(hass, 106)
        await tick(c)
        hass.states.async_set("sensor.tesla_soc", 53, {"unit_of_measurement": "%"})
        hass.states.async_set("sensor.tesla_range", 212, {"unit_of_measurement": "km"})
        hass.states.async_set("sensor.tesla_energy", 37.1, {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.car_capture", capture.isoformat())
        await tick(c, 2)
        assert request.remaining_kwh == pytest.approx(allowance - 6)
        assert request.origin_meter == 100
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_first_meter_reset_cannot_increase_the_budget(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        set_meter(hass, 5)
        await tick(c, 3)
        assert c.sessions[vehicle].terminal
        assert c.sessions[vehicle].reason == "energy_meter_regressed"
        assert calls == [("switch", "off")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_unadvertised_smart_does_not_repeat_stop_while_idle(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        await tick(c, 3)
        hass.states.async_set("select.ev_mode", "Manual", {"options": ["Green", "Manual"]})
        narrow_request(c, vehicle)
        result = await tick(c, 8)
        assert result["control_status"] == "smart_mode_unavailable"
        assert calls == [("mode", "Manual"), ("switch", "on"), ("switch", "off")]
        hass.states.async_set("select.ev_mode", "Manual", {"options": ["Smart", "Manual"]})
        await tick(c, 4)
        assert calls[-1] == ("mode", "Smart")
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_stop_reasserted_when_switch_reports_on_without_independent_power(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        await c.async_preview_action("stop", vehicle, {})
        await tick(c, 3)
        assert c.control.status == "ai_stop_confirmed"
        hass.states.async_set("sensor.hw_power", "unavailable")
        hass.states.async_set("switch.ev_charger", "on")
        await tick(c, 3)
        assert calls == [("switch", "off"), ("switch", "off")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_stop_without_attachment_waits_for_safe_negative_control(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        hass.states.async_set("sensor.connection", "unavailable")
        await c.async_preview_action("stop", vehicle, {})
        await tick(c)
        assert c.control.status == "waiting_connection_to_confirm_ai_stop"
        assert calls == []
        hass.states.async_set("sensor.connection", "connection-a")
        await tick(c, 3)
        assert calls == [("switch", "off")]
        assert c.control.status == "ai_stop_confirmed"
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_native_takeover_survives_reload_after_stop(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    reloaded = None
    try:
        narrow_request(c, vehicle)
        await c.async_preview_action("stop", vehicle, {})
        await tick(c, 3)
        c.control._service_seen(Event("call_service", {
            "domain": "enphase_ev", "service": "start_charging",
            "service_data": {"entity_id": "switch.ev_charger"},
        }, context=Context(user_id="user")))
        await tick(c, 2)
        assert c.control.fault == "external_mode_command_requires_new_start"
        saved = c._store_data()
        await c.async_shutdown()
        reloaded = PreviewCoordinator(hass, c.entry)
        reloaded.store.async_load = AsyncMock(return_value=saved)
        reloaded.store.async_save = AsyncMock()
        reloaded.store.async_delay_save = Mock()
        await reloaded.async_initialize()
        hass.states.async_set("sensor.hw_power", 4100, {"unit_of_measurement": "W"})
        hass.states.async_set("switch.ev_charger", "on")
        await tick(reloaded, 2)
        assert calls == [("switch", "off")]
    finally:
        if reloaded:
            await reloaded.async_shutdown()
        await close(hass, c)


@pytest.mark.asyncio
async def test_profile_change_requires_no_active_green_or_ai_request(tmp_path):
    hass, c, _, _ = await controlled(tmp_path)
    try:
        assert c.control.binding_change_blocked({**c.entry.data, "enphase_profile": AI_OPTIMISATION})
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_unplug_retires_latched_stop_even_if_charger_token_is_cached(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        await c.async_preview_action("stop", vehicle, {})
        await tick(c, 3)
        hass.states.async_set("binary_sensor.evse_plug", "off")
        c._unplug_edges.append(datetime.now(timezone.utc).timestamp())
        await tick(c)
        assert c.control.grant["ai_detached"]
        assert "ai_stop_latched" not in c.control.grant
        hass.states.async_set("binary_sensor.evse_plug", "on")
        hass.states.async_set("switch.ev_charger", "on")
        hass.states.async_set("sensor.hw_power", 4100, {"unit_of_measurement": "W"})
        await tick(c, 3)
        assert calls == [("switch", "off")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_stop_cannot_latch_a_new_order_while_waiting_on_lock(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, ai=True)
    try:
        async with c._lock:
            stop = asyncio.create_task(c.async_control_stop(vehicle))
            await asyncio.sleep(0)
            assert c.control._stop_requested
            c.control.assign_authority(vehicle, c.control.validate_start(vehicle))
        await stop
        assert not c.sessions[vehicle].terminal
        assert "ai_stop_latched" not in c.control.grant
        await tick(c, 3)
        assert calls == [("mode", "Manual"), ("switch", "on")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_loss_of_meter_uses_physical_upper_bound_not_planning_power(tmp_path):
    hass, c, vehicle, _ = await controlled(tmp_path, ai=True)
    try:
        object.__setattr__(c.entry, "data", MappingProxyType({**c.entry.data, "power_kw": 2}))
        grant = c.control.grant
        grant["max_kwh"] = 1
        c.control._assess_caps({"meter_kwh": None}, grant["cap_checked_at"] + 180)
        assert c.control.fault == "unmetered_energy_exposure_cap"
        assert grant["unmetered_exposure_kwh"] == pytest.approx(1.1)
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_ai_first_vehicle_capture_after_start_keeps_later_meter_credit(tmp_path):
    hass, c, vehicle, _ = await controlled(tmp_path, ai=True)
    try:
        narrow_request(c, vehicle)
        request = c.sessions[vehicle]
        captured = datetime.now(timezone.utc)
        assert captured.timestamp() > request.started_at
        hass.states.async_set("sensor.car_capture", captured.isoformat())
        set_meter(hass, 101)
        await tick(c)
        assert request.anchor_meter == 100
        assert request.remaining_kwh == pytest.approx(request.anchor_need - 1)
    finally:
        await close(hass, c)
