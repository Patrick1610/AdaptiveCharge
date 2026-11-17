"""Cloud timeouts and completed cleanup must not strand an explicit Start."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from custom_components.adaptive_charge_preview.control import FEEDBACK_TIMEOUT
from .test_control import change_profile, close, controlled, tick


@pytest.fixture(autouse=True)
def fast_feedback(monkeypatch):
    monkeypatch.setattr("custom_components.adaptive_charge_preview.control.START_SETTLE_SECONDS", 0)


@pytest.mark.asyncio
async def test_completed_fault_cleanup_allows_only_a_new_explicit_start(tmp_path):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        await tick(c, 3)
        c.control.fault = "actuator_error:TimeoutError"
        c.control._cleanup_required = True
        await c.async_preview_action("stop", v, {})
        await tick(c, 4)
        assert not c.control.owned_manual and c.control.pending is None
        assert not c.control._cleanup_required
        assert c.control.fault == "actuator_error:TimeoutError"
        assert c.sessions[v].terminal
        before = deepcopy(c.sessions[v].as_dict())
        negative_calls = list(calls)
        await tick(c, 3)
        assert calls == negative_calls and c.sessions[v].as_dict() == before
        await c.async_preview_action("start", v, {"target_range_km": 250, "departure": "now"})
        assert c.sessions[v].generation > before["generation"]
        assert c.control.fault is None
        await tick(c, 3)
        assert calls[-2:] == [("mode", "Manual"), ("switch", "on")]
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_negative_green_timeout_keeps_cleanup_and_blocks_new_start(tmp_path):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        await tick(c, 3)
        async def timed_out_green(call):
            calls.append(("mode", call.data["option"]))
            raise TimeoutError
        hass.services.async_register("select", "select_option", timed_out_green)
        c.control._cleanup_required = True
        await c.async_preview_action("stop", v, {})
        await tick(c, 3)
        assert c.control.owned_manual and c.control._cleanup_required
        assert c.control.pending["kind"] == "green"
        assert c.control.status in {"waiting_green_feedback", "owned_stop_unconfirmed"}
        with pytest.raises(ValueError, match="finish_previous_cleanup_first"):
            c.control.validate_start(v)
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_negative_timeout_keeps_cleanup_and_blocks_new_start(tmp_path):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        await tick(c, 3)

        async def timed_out_off(call):
            calls.append(("switch", "off"))
            raise TimeoutError

        hass.services.async_register("switch", "turn_off", timed_out_off)
        c.control._cleanup_required = True
        await c.async_preview_action("stop", v, {})
        await tick(c, 3)
        assert c.control.owned_manual and c.control._cleanup_required
        assert c.control.pending["kind"] == "stop"
        with pytest.raises(ValueError, match="finish_previous_cleanup_first"):
            c.control.validate_start(v)
    finally:
        await close(hass, c)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", [None, "power", "stale_power", "fallback_power", "stale_report", "switch", "mode", "feedback", "binding", "connection", "generation", "foreign"])
@pytest.mark.parametrize("recovery", [False, True])
async def test_persisted_leftover_requires_confirmed_same_owned_idle_charger(tmp_path, blocker, recovery):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        object.__setattr__(c.entry, "data", MappingProxyType({**c.entry.data, "charger_fault_recovery": recovery}))
        c.control.grant["binding"] = c.control._binding(v)
        await c.async_preview_action("stop", v, {})
        saved = c.control.as_dict()
        saved.update(cleanup_required=True, fault="actuator_error:TimeoutError")
        saved["grant"]["start_watch_since"] = datetime.now(timezone.utc).timestamp() - 500
        c.control.restore(saved)
        if blocker == "power":
            hass.states.async_set("sensor.hw_power", 3000, {"unit_of_measurement": "W"})
        elif blocker == "stale_power":
            object.__setattr__(hass.states.get("sensor.hw_power"), "last_reported", datetime.now(timezone.utc) - timedelta(hours=1))
        elif blocker == "fallback_power":
            object.__setattr__(c.entry, "data", MappingProxyType({**c.entry.data, "fallback_power_entity": "sensor.evse_power"}))
            c.control.grant["binding"] = c.control._binding(v)
            object.__setattr__(hass.states.get("sensor.hw_power"), "last_reported", datetime.now(timezone.utc) - timedelta(hours=1))
            hass.states.async_set("sensor.evse_power", 0, {"unit_of_measurement": "W"})
        elif blocker == "stale_report":
            hass.states.async_set("sensor.ev_report", (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        elif blocker == "switch":
            hass.states.async_set("switch.ev_charger", "on")
        elif blocker == "mode":
            hass.states.async_set("select.ev_mode", "Manual", {"options": ["Green", "Manual"]})
        elif blocker == "feedback":
            hass.states.async_set("sensor.ev_feedback", "unavailable")
        elif blocker == "binding":
            object.__setattr__(c.entry, "data", MappingProxyType({**c.entry.data, "charger_switch_entity": "switch.changed"}))
        elif blocker == "connection":
            hass.states.async_set("sensor.connection", "new-attachment")
        elif blocker == "generation":
            c.control.grant["generation"] += 1
        elif blocker == "foreign":
            hass.states.async_set("input_boolean.legacy_master", "on")
        before = deepcopy(c.sessions[v].as_dict())
        await tick(c, 2)
        assert c.control._cleanup_required is (blocker is not None)
        assert c.sessions[v].as_dict() == before and not calls
        assert c.control.fault is not None
        if blocker is None:
            assert c.control.grant["start_watch_since"] is None
            assert c._store_data()["control"]["cleanup_required"] is False
            c.control.restore(c.control.as_dict())
            assert c.control._cleanup_required is False
            await c.async_preview_action("start", v, {"target_range_km": 250, "departure": "now"})
            assert not c.sessions[v].terminal
        else:
            with pytest.raises(ValueError):
                c.control.validate_start(v)
    finally:
        await close(hass, c)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["late_ack", "no_ack", "stop", "disconnect"])
async def test_timed_out_manual_waits_once_for_feedback(tmp_path, outcome):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        async def timed_out_mode(call):
            calls.append(("mode", call.data["option"]))
            if call.data["option"] == "Manual":
                raise TimeoutError
            hass.states.async_set("select.ev_mode", "Green", {"options": ["Green", "Manual"]}, context=call.context)
            hass.states.async_set("sensor.ev_feedback", "Green", {"requested_mode": None, "preferred_mode": "GREEN_CHARGING", "effective_mode": "GREEN_CHARGING"})
            hass.states.async_set("sensor.ev_report", datetime.now(timezone.utc).isoformat())

        hass.services.async_register("select", "select_option", timed_out_mode)
        await tick(c, 2)
        before = deepcopy(c.sessions[v].as_dict())
        assert c.control.fault is None and c.control.pending["kind"] == "manual"
        assert calls == [("mode", "Manual")]
        if outcome == "late_ack":
            hass.states.async_set("select.ev_mode", "Manual", {"options": ["Green", "Manual"]})
            hass.states.async_set("sensor.ev_feedback", "Manual", {"requested_mode": None, "preferred_mode": "MANUAL_CHARGING", "effective_mode": "MANUAL_CHARGING"})
            hass.states.async_set("sensor.ev_report", datetime.now(timezone.utc).isoformat())
            await tick(c, 3)
            assert c.control.fault is None and c.control.pending is None
            assert calls == [("mode", "Manual"), ("switch", "on")]
            assert c.sessions[v].anchor_meter == before["anchor_meter"]
            assert c.sessions[v].anchor_need == before["anchor_need"]
        else:
            if outcome == "no_ack":
                c.control.pending["sent"] -= FEEDBACK_TIMEOUT + 1
            elif outcome == "stop":
                await c.async_preview_action("stop", v, {})
            else:
                hass.states.async_set("binary_sensor.evse_plug", "off")
            await tick(c, 5)
            assert ("switch", "on") not in calls
            assert calls.count(("mode", "Manual")) == 1
            if outcome == "no_ack":
                assert c.control.fault == "actuator_feedback_timeout"
            else:
                assert c.sessions[v].terminal
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_timed_out_charger_on_still_faults_and_cleans_up_without_resend(tmp_path):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        async def timed_out_on(call):
            calls.append(("switch", "on"))
            raise TimeoutError

        hass.services.async_register("switch", "turn_on", timed_out_on)
        await tick(c, 3)
        assert c.control.fault == "actuator_error:TimeoutError"
        before = deepcopy(c.sessions[v].as_dict())
        await tick(c, 5)
        assert calls.count(("mode", "Manual")) == 1
        assert calls.count(("switch", "on")) == 1
        assert c.sessions[v].anchor_meter == before["anchor_meter"]
        assert c.sessions[v].anchor_need == before["anchor_need"]
        assert c.control.fault == "actuator_error:TimeoutError"
        assert not c.control.owned_manual and c.control.pending is None
        assert c.control.grant["start_watch_since"] is None
    finally:
        await close(hass, c)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_real_service_timeout_with_late_manual_completion(tmp_path, monkeypatch, stop):
    hass, c, v, calls = await controlled(tmp_path)
    released = asyncio.Event()
    monkeypatch.setattr("custom_components.adaptive_charge_preview.control.SERVICE_TIMEOUT", .01)
    try:
        def report_mode(call):
            option = call.data["option"]
            code = "MANUAL_CHARGING" if option == "Manual" else "GREEN_CHARGING"
            hass.states.async_set("select.ev_mode", option, {"options": ["Green", "Manual"]}, context=call.context)
            hass.states.async_set("sensor.ev_feedback", option, {"requested_mode": None, "preferred_mode": code, "effective_mode": code})
            hass.states.async_set("sensor.ev_report", datetime.now(timezone.utc).isoformat())
        async def late_device_report(call):
            await released.wait()
            report_mode(call)
        async def delayed_mode(call):
            option = call.data["option"]
            calls.append(("mode", option))
            if option == "Manual":
                # Hardware may finish after HA cancels its waiting service task.
                hass.async_create_task(late_device_report(call), "test late charger feedback")
                await asyncio.Event().wait()
            else:
                report_mode(call)
        hass.services.async_register("select", "select_option", delayed_mode)
        await tick(c)
        assert c.control.fault is None
        assert c._store_data()["control"]["pending"]["kind"] == "manual"
        if stop:
            await c.async_preview_action("stop", v, {})
        released.set()
        await hass.async_block_till_done()
        await tick(c, 5)
        assert calls.count(("mode", "Manual")) == 1
        assert c.control.fault is None
        if stop:
            assert ("switch", "on") not in calls and c.sessions[v].terminal
        else:
            assert calls.count(("switch", "on")) == 1 and c.control.owned_manual
    finally:
        released.set()
        await close(hass, c)


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_pending", [False, True])
async def test_completed_green_keeps_unresolved_vehicle_limit_obligation(tmp_path, restore_pending):
    hass, c, v, calls = await controlled(tmp_path)
    try:
        change_profile(c, v, allow_limit_raise=True, maximum_soc=95)
        await c.async_preview_action("set_target", v, {"target_range_km": 330})
        await tick(c, 4)
        assert c.control.limit is not None and c.control.owned_manual
        c.control.fault = "actuator_error:TimeoutError"
        c.control._cleanup_required = True
        await c.async_preview_action("stop", v, {})
        hass.states.async_set("number.tesla_limit", "unavailable", {"unit_of_measurement": "%"})
        if restore_pending:
            c.control.limit.update(restore_pending=True, restore_sent=datetime.now(timezone.utc).timestamp())
        await tick(c, 5)
        assert not c.control.owned_manual and c.control.pending is None
        assert c._store_data()["control"]["cleanup_required"] is True
        assert c.control.limit is not None
        with pytest.raises(ValueError, match="finish_previous_cleanup_first"):
            c.control.validate_start(v)
    finally:
        await close(hass, c)
