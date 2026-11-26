"""Restore real HA controller state while the device report clock lags."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from types import MappingProxyType

import pytest

from custom_components.adaptive_charge_preview import control
from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
from .test_control import close, controlled, fast_feedback, tick


async def reload_running(hass, previous, vehicle, monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    previous.sessions[vehicle].set_departure(future.timestamp())
    previous.values[vehicle]["departure"] = future.timestamp()
    stored = deepcopy(previous._store_data())
    await previous.async_shutdown()
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(control.dt_util, "utcnow", lambda: clock["now"])
    current = PreviewCoordinator(hass, previous.entry)
    current.store.async_load = AsyncMock(return_value=stored)
    current.store.async_save = AsyncMock()
    current.store.async_delay_save = Mock()
    current.async_request_refresh = AsyncMock()
    await current.async_initialize()
    return current, stored, clock


def positive_power(hass, clock, seconds):
    clock["now"] += timedelta(seconds=seconds)
    hass.states.async_set("sensor.hw_power", 5500 + seconds / 1000,
                          {"unit_of_measurement": "W"}, force_update=True)
    hass.states.get("sensor.hw_power").last_reported = clock["now"]


@pytest.mark.asyncio
@pytest.mark.parametrize("report_age", [0, 180, 900])
async def test_continuing_charge_after_restart_needs_no_new_mode_write_or_report(
    tmp_path, monkeypatch, caplog, report_age
):
    caplog.set_level("INFO", logger=control.__name__)
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        hass.states.async_set("sensor.iq_energy", 102, {"unit_of_measurement": "kWh"})
        await tick(before)
        current, stored, clock = await reload_running(hass, before, vehicle, monkeypatch)
        old_report = clock["now"] - timedelta(seconds=report_age)
        hass.states.async_set("sensor.ev_report", old_report.isoformat())
        hass.states.async_set("sensor.car_capture", "unavailable")

        async def delayed_mode(call):
            calls.append(("mode", call.data["option"]))
            # The device keeps charging in Manual but publishes no new report.
            hass.states.async_set("select.ev_mode", call.data["option"],
                                  {"options": ["Green", "Manual"]}, context=call.context)

        hass.services.async_register("select", "select_option", delayed_mode)
        for seconds in [0, 130, 180]:
            positive_power(hass, clock, seconds)
            await tick(current)
            assert current.control.fault is None and current.control.pending is None
            assert current.control.owned_manual and not current.sessions[vehicle].terminal
        assert calls == [("mode", "Manual"), ("switch", "on")]
        assert caplog.text.count("Restored owned Manual without a new actuator write") == 1
        request = current.sessions[vehicle]
        for key in ["generation", "started_at", "connection_id", "origin_meter", "anchor_meter", "anchor_need"]:
            assert getattr(request, key) == stored["sessions"][vehicle][key]
        assert request.remaining_kwh == pytest.approx(stored["sessions"][vehicle]["remaining_kwh"])
        assert current.control.grant["manual_since"] == stored["control"]["grant"]["manual_since"]
        assert current.control.grant["manual_seconds"] == stored["control"]["grant"]["manual_seconds"]
        assert current.control.grant["start_watch_since"] is None
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("incoherent", ["old_power", "selected_green", "feedback_unavailable", "requested_mode"])
async def test_restore_does_not_adopt_incoherent_or_stale_physical_readback(
    tmp_path, monkeypatch, caplog, incoherent
):
    caplog.set_level("INFO", logger=control.__name__)
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        current, _, clock = await reload_running(hass, before, vehicle, monkeypatch)
        positive_power(hass, clock, 0)
        if incoherent == "old_power":
            hass.states.get("sensor.hw_power").last_reported = clock["now"] - timedelta(minutes=10)
        elif incoherent == "selected_green":
            hass.states.async_set("select.ev_mode", "Green", {"options": ["Green", "Manual"]})
        elif incoherent == "feedback_unavailable":
            hass.states.async_set("sensor.ev_feedback", "unavailable")
        else:
            hass.states.async_set("sensor.ev_feedback", "Manual", {
                "requested_mode": "MANUAL_CHARGING", "preferred_mode": "MANUAL_CHARGING",
                "effective_mode": "MANUAL_CHARGING"})
        await tick(current)
        if incoherent == "feedback_unavailable":
            assert current.control.status == "control_bindings_incomplete"
            assert current.control.pending is None
        else:
            assert current.control.pending and current.control.pending["recovery"] is True
        assert calls.count(("switch", "on")) == 1
        assert "Restored owned Manual without a new actuator write" not in caplog.text
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [15, 180])
@pytest.mark.parametrize("second_reload", [False, True])
async def test_late_consistent_recovery_readback_precedes_timeout_without_positive_retry(
    tmp_path, monkeypatch, delay, second_reload
):
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        current, stored, clock = await reload_running(hass, before, vehicle, monkeypatch)
        hass.states.async_set("sensor.ev_feedback", "Green", {
            "requested_mode": None, "preferred_mode": "GREEN_CHARGING",
            "effective_mode": "GREEN_CHARGING"})
        hass.states.async_set("sensor.ev_report", (clock["now"] - timedelta(minutes=3)).isoformat())

        async def delayed_mode(call):
            calls.append(("mode", call.data["option"]))
            hass.states.async_set("select.ev_mode", call.data["option"],
                                  {"options": ["Green", "Manual"]}, context=call.context)

        hass.services.async_register("select", "select_option", delayed_mode)
        positive_power(hass, clock, 0)
        await tick(current)
        assert current.control.pending["recovery"] is True
        if second_reload:
            pending_store = deepcopy(current._store_data())
            await current.async_shutdown()
            current = PreviewCoordinator(hass, before.entry)
            current.store.async_load = AsyncMock(return_value=pending_store)
            current.store.async_save = AsyncMock()
            current.store.async_delay_save = Mock()
            current.async_request_refresh = AsyncMock()
            await current.async_initialize()
        positive_power(hass, clock, delay)
        hass.states.async_set("sensor.ev_feedback", "Manual", {
            "requested_mode": None, "preferred_mode": "MANUAL_CHARGING",
            "effective_mode": "MANUAL_CHARGING"})
        await tick(current)
        assert current.control.fault is None and current.control.pending is None
        assert calls.count(("mode", "Manual")) == 2
        assert calls.count(("switch", "on")) == 1
        assert current.sessions[vehicle].origin_meter == stored["sessions"][vehicle]["origin_meter"]
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["disabled", "stop", "cleanup", "fault", "foreign", "attachment", "cap", "expired", "pending_stop", "manual_time", "external_mode", "binding"])
async def test_positive_power_cannot_override_restore_boundaries(tmp_path, monkeypatch, blocker):
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        current, _, clock = await reload_running(hass, before, vehicle, monkeypatch)
        positive_power(hass, clock, 0)
        if blocker == "disabled": current.control.enabled = False
        elif blocker == "stop": current.sessions[vehicle].stop("explicit_stop")
        elif blocker == "cleanup": current.control._cleanup_required = True
        elif blocker == "fault": current.control.fault = "existing_fault"
        elif blocker == "foreign": hass.states.async_set("input_boolean.legacy_master", "on")
        elif blocker == "attachment": hass.states.async_set("sensor.connection", "connection-b")
        elif blocker == "manual_time": current.control.grant["manual_seconds"] = control.MAX_MANUAL_SECONDS
        elif blocker == "external_mode": current.control._external_mode = True
        elif blocker == "binding": object.__setattr__(current.entry, "data", MappingProxyType({**current.entry.data, "power_entity": "sensor.changed"}))
        elif blocker == "cap":
            current.control.grant["max_kwh"] = .1
            hass.states.async_set("sensor.iq_energy", 101, {"unit_of_measurement": "kWh"})
        elif blocker == "expired":
            current.sessions[vehicle].set_departure(clock["now"].timestamp() - 1)
            current.values[vehicle]["departure"] = clock["now"].timestamp() - 1
        else:
            await current.control._begin("stop", clock["now"].timestamp())
        await tick(current)
        assert calls.count(("mode", "Manual")) == 1
        assert calls.count(("switch", "on")) == 1
        if blocker == "fault": assert current.control.fault == "existing_fault"
        if blocker in {"cap", "manual_time"}: assert current.control.fault == "command_energy_or_manual_time_cap"
        if blocker in {"stop", "disabled", "cleanup"}: assert calls[-1] == ("switch", "off")
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,marker", [("manual", "yes"), ("green", True)])
async def test_invalid_persisted_recovery_marker_cannot_grant_authority(tmp_path, kind, marker):
    hass, c, vehicle, _ = await controlled(tmp_path)
    try:
        await tick(c, 2)
        stored = c.control.as_dict()
        now = datetime.now(timezone.utc).timestamp()
        stored["pending"] = {"kind": kind, "sent": now, "attempts": 1, "last_attempt": now, "recovery": marker}
        restored = control.ChargerControl(c)
        restored.restore(stored)
        assert restored.fault == "invalid_stored_control_operation"
        assert not restored.enabled and restored.grant is None
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_restored_cached_power_keeps_watchdog_until_a_later_observation(tmp_path, monkeypatch):
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        current, _, clock = await reload_running(hass, before, vehicle, monkeypatch)
        positive_power(hass, clock, 0)
        await tick(current)
        first = clock["now"].timestamp()
        assert current.control.grant["start_watch_since"] == first
        # No later device observation: the startup value must not satisfy its own watch.
        clock["now"] += timedelta(seconds=130)
        await tick(current)
        assert current.control.grant["start_watch_since"] == first
        assert current.control.status == "physical_start_unconfirmed"
        assert current.control.fault is None and not current.sessions[vehicle].terminal
        assert calls == [("mode", "Manual"), ("switch", "on")]
        positive_power(hass, clock, 1)
        await tick(current)
        assert current.control.grant["start_watch_since"] is None
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_pending", [False, True])
async def test_new_start_ack_is_not_upgraded_to_recovery_after_reload(tmp_path, monkeypatch, reload_pending):
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before)
        assert before.control.pending and not before.control.pending.get("recovery")
        if reload_pending:
            current, _, clock = await reload_running(hass, before, vehicle, monkeypatch)
        else:
            current = before
            clock = {"now": datetime.now(timezone.utc)}
            monkeypatch.setattr(control.dt_util, "utcnow", lambda: clock["now"])
        positive_power(hass, clock, 0)
        hass.states.async_set("sensor.ev_report", (clock["now"] - timedelta(minutes=3)).isoformat())
        await tick(current)
        assert current.control.pending and not current.control.pending.get("recovery")
        assert current.control.status == "waiting_manual_feedback"
        assert calls == [("mode", "Manual")]
    finally:
        if current and current is not before:
            await current.async_shutdown()
        await close(hass, before)


@pytest.mark.asyncio
async def test_stop_arriving_during_limit_restore_still_uses_owned_green_cleanup(tmp_path, monkeypatch):
    hass, before, vehicle, calls = await controlled(tmp_path)
    current = None
    try:
        await tick(before, 2)
        current, _, clock = await reload_running(hass, before, vehicle, monkeypatch)
        positive_power(hass, clock, 0)
        current.control.limit = {"restore_pending": True}
        async def stop_during_restore():
            current.control.limit = None
            current.control._stop_requested = True
        monkeypatch.setattr(current.control, "restore_limit", stop_during_restore)
        await tick(current)
        assert calls[-1] == ("switch", "off")
        assert current.control.pending["kind"] == "stop"
        assert current.control.status == "waiting_physical_stop"
        assert current.control.fault is None
        assert calls.count(("mode", "Manual")) == 1
    finally:
        if current:
            await current.async_shutdown()
        await close(hass, before)
