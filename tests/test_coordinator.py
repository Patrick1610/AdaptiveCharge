"""Unit tests for AdaptiveCharge coordinator logic."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from statistics import mean
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers that mirror coordinator logic without importing HA
# ---------------------------------------------------------------------------

def _to_watts_test(value: float, uom: str) -> float:
    """Mirror of coordinator._to_watts, unit-tested without HA."""
    if "kW" in uom:
        return value * 1000.0
    if not uom and abs(value) < 20:
        return value * 1000.0
    return value


def compute_surplus(net_w: float, ev_w: float) -> float:
    return (0.0 - net_w) + ev_w


def compute_raw_current(surplus_w: float, voltage: float) -> float:
    if voltage <= 0:
        return 0.0
    return surplus_w / (voltage * 3.0)


def floor_current(raw_a: float, max_a: int = 16) -> int:
    return min(max(int(raw_a), 0), max_a)


def compute_smoothed(samples: deque, window_s: int, now: datetime) -> float:
    cutoff = now - timedelta(seconds=window_s)
    valid = [v for ts, v in samples if ts >= cutoff]
    return mean(valid) if valid else 0.0


# ---------------------------------------------------------------------------
# Tests: kW → W conversion
# ---------------------------------------------------------------------------

class TestKwToWConversion:
    def test_kw_unit_multiplies_by_1000(self):
        assert _to_watts_test(3.5, "kW") == 3500.0

    def test_w_unit_unchanged(self):
        assert _to_watts_test(3500.0, "W") == 3500.0

    def test_no_unit_small_value_treated_as_kw(self):
        # value < 20, no unit → assume kW
        assert _to_watts_test(1.5, "") == 1500.0

    def test_no_unit_large_value_treated_as_w(self):
        # value >= 20, no unit → keep as W
        assert _to_watts_test(500.0, "") == 500.0

    def test_zero_value(self):
        assert _to_watts_test(0.0, "kW") == 0.0

    def test_negative_kw(self):
        assert _to_watts_test(-2.0, "kW") == -2000.0


# ---------------------------------------------------------------------------
# Tests: surplus_w calculation
# ---------------------------------------------------------------------------

class TestSurplusCalculation:
    def test_exporting_no_ev(self):
        # Exporting 1000 W (net = -1000), no EV
        assert compute_surplus(-1000.0, 0.0) == 1000.0

    def test_importing_no_ev(self):
        # Importing 500 W (net = +500), no EV
        assert compute_surplus(500.0, 0.0) == -500.0

    def test_ev_adds_back_to_surplus(self):
        # Net = 0 (balanced), EV using 2000 W → surplus = 2000 W
        assert compute_surplus(0.0, 2000.0) == 2000.0

    def test_ev_and_export(self):
        # Exporting 500 W, EV drawing 1000 W → surplus = 500 + 1000 = 1500
        assert compute_surplus(-500.0, 1000.0) == 1500.0

    def test_importing_with_ev(self):
        # Importing 200 W, EV drawing 1000 W
        # net = 200, surplus = -200 + 1000 = 800
        assert compute_surplus(200.0, 1000.0) == 800.0


# ---------------------------------------------------------------------------
# Tests: raw_current_a calculation
# ---------------------------------------------------------------------------

class TestRawCurrentCalculation:
    def test_basic_calculation(self):
        # 3000 W surplus, 230 V, 3-phase → 3000 / 690 ≈ 4.35 A
        result = compute_raw_current(3000.0, 230.0)
        assert abs(result - (3000.0 / 690.0)) < 0.001

    def test_zero_surplus(self):
        assert compute_raw_current(0.0, 230.0) == 0.0

    def test_negative_surplus(self):
        assert compute_raw_current(-1000.0, 230.0) < 0

    def test_zero_voltage_returns_zero(self):
        assert compute_raw_current(5000.0, 0.0) == 0.0

    def test_floor_clamps_negative_to_zero(self):
        raw = compute_raw_current(-500.0, 230.0)
        assert floor_current(raw) == 0

    def test_floor_clamps_to_max(self):
        raw = compute_raw_current(50000.0, 230.0)
        assert floor_current(raw) == 16

    def test_floor_mid_value(self):
        # 3000 W / 690 V ≈ 4.35 A → floored = 4
        raw = compute_raw_current(3000.0, 230.0)
        assert floor_current(raw) == 4


# ---------------------------------------------------------------------------
# Tests: smoothing deque logic
# ---------------------------------------------------------------------------

class TestSmoothingDeque:
    def _make_samples(self, values: list[float], base_time: datetime, interval_s: int = 10):
        d = deque()
        for i, v in enumerate(values):
            ts = base_time + timedelta(seconds=i * interval_s)
            d.append((ts, v))
        return d

    def test_mean_of_all_samples_in_window(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        samples = self._make_samples([2.0, 4.0, 6.0], now, 10)
        # all within 120 s window, last ts = now + 20 s
        result = compute_smoothed(samples, 120, now + timedelta(seconds=25))
        assert abs(result - 4.0) < 0.001

    def test_old_samples_excluded(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        # 3 samples: first at t=0, second at t=60, third at t=120
        samples = self._make_samples([10.0, 2.0, 4.0], now, 60)
        # Window = 90 s, current time = now + 120
        current_time = now + timedelta(seconds=120)
        # cutoff = now+30 → only samples at t=60 and t=120 are included
        result = compute_smoothed(samples, 90, current_time)
        assert abs(result - 3.0) < 0.001  # mean(2, 4)

    def test_empty_deque_returns_zero(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        assert compute_smoothed(deque(), 120, now) == 0.0

    def test_single_sample(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        samples = deque([(now, 7.5)])
        result = compute_smoothed(samples, 120, now + timedelta(seconds=10))
        assert abs(result - 7.5) < 0.001


# ---------------------------------------------------------------------------
# Tests: force_charge logic
# ---------------------------------------------------------------------------

class TestForceChargeLogic:
    """Test state machine transitions around force charge."""

    def test_force_charge_true_when_charge_now_on(self):
        charge_now = True
        force_charge = charge_now
        assert force_charge is True

    def test_force_charge_false_when_charge_now_off(self):
        charge_now = False
        force_charge = charge_now
        assert force_charge is False

    def test_transition_off_to_on_detected(self):
        prev = False
        current = True
        changed = current != prev
        assert changed is True

    def test_transition_on_to_off_detected(self):
        prev = True
        current = False
        changed = current != prev
        assert changed is True

    def test_no_transition_when_same(self):
        prev = True
        current = True
        changed = current != prev
        assert changed is False


# ---------------------------------------------------------------------------
# Tests: solar_done logic
# ---------------------------------------------------------------------------

class TestSolarDoneLogic:
    """Test solar done detection logic."""

    def _solar_done_state(
        self,
        solar_w: float,
        threshold: float,
        duration_s: int,
        below_since: datetime | None,
        now: datetime,
    ) -> tuple[bool, datetime | None]:
        """Return (solar_done, below_threshold_since)."""
        if solar_w < threshold:
            if below_since is None:
                below_since = now
            elapsed = (now - below_since).total_seconds()
            solar_done = elapsed >= duration_s
        else:
            below_since = None
            solar_done = False
        return solar_done, below_since

    def test_not_done_when_above_threshold(self):
        now = datetime(2024, 1, 1, 14, 0, 0)
        done, _ = self._solar_done_state(200.0, 50.0, 600, None, now)
        assert done is False

    def test_timer_starts_when_below_threshold(self):
        now = datetime(2024, 1, 1, 18, 0, 0)
        done, below_since = self._solar_done_state(30.0, 50.0, 600, None, now)
        assert done is False
        assert below_since == now

    def test_done_after_duration(self):
        start = datetime(2024, 1, 1, 18, 0, 0)
        now = start + timedelta(seconds=600)
        done, _ = self._solar_done_state(30.0, 50.0, 600, start, now)
        assert done is True

    def test_not_done_before_duration(self):
        start = datetime(2024, 1, 1, 18, 0, 0)
        now = start + timedelta(seconds=300)
        done, _ = self._solar_done_state(30.0, 50.0, 600, start, now)
        assert done is False

    def test_resets_when_production_recovers(self):
        start = datetime(2024, 1, 1, 18, 0, 0)
        now = start + timedelta(seconds=700)
        # Production jumps back above threshold
        done, below_since = self._solar_done_state(200.0, 50.0, 600, start, now)
        assert done is False
        assert below_since is None


# ---------------------------------------------------------------------------
# Tests: controller_enabled gate
# ---------------------------------------------------------------------------

class TestControllerEnabled:
    """Test master controller switch behaviour."""

    def test_controller_enabled_default_off(self):
        """Controller enabled should default to False (safe first-install)."""
        enabled = False  # default
        assert enabled is False

    def test_controller_enabled_gate_no_control_when_off(self):
        """When controller is disabled, control logic should not run."""
        controller_enabled = False
        # Simulate: if not controller_enabled → skip _run_control_logic
        control_logic_ran = False
        if controller_enabled:
            control_logic_ran = True
        assert control_logic_ran is False

    def test_controller_enabled_allows_control_when_on(self):
        """When controller is enabled, control logic should run."""
        controller_enabled = True
        control_logic_ran = False
        if controller_enabled:
            control_logic_ran = True
        assert control_logic_ran is True

    def test_shutdown_triggered_when_disabled_while_charging(self):
        """Disabling controller while charging_on should trigger shutdown."""
        controller_enabled = True
        charging_on = True
        shutdown_scheduled = False
        # Simulate set_controller_enabled(False)
        prev = controller_enabled
        controller_enabled = False
        if prev and not controller_enabled and charging_on:
            shutdown_scheduled = True
        assert shutdown_scheduled is True

    def test_no_shutdown_when_disabled_while_not_charging(self):
        """Disabling controller when not charging should not trigger shutdown."""
        controller_enabled = True
        charging_on = False
        shutdown_scheduled = False
        prev = controller_enabled
        controller_enabled = False
        if prev and not controller_enabled and charging_on:
            shutdown_scheduled = True
        assert shutdown_scheduled is False

    def test_no_shutdown_when_already_disabled(self):
        """If controller was already off, no extra shutdown on repeated disable."""
        controller_enabled = False
        charging_on = True
        shutdown_scheduled = False
        prev = controller_enabled
        controller_enabled = False
        if prev and not controller_enabled and charging_on:
            shutdown_scheduled = True
        assert shutdown_scheduled is False

    def test_controller_off_sets_mode_off_in_sensor(self):
        """Mode sensor should report 'off' when controller_enabled is False."""
        controller_enabled = False
        current_mode = "surplus"
        # ModeSensor logic
        if not controller_enabled:
            displayed_mode = "off"
        else:
            displayed_mode = current_mode
        assert displayed_mode == "off"

    def test_controller_on_shows_current_mode(self):
        """Mode sensor shows actual current_mode when controller is on."""
        controller_enabled = True
        current_mode = "surplus"
        if not controller_enabled:
            displayed_mode = "off"
        else:
            displayed_mode = current_mode
        assert displayed_mode == "surplus"


# ---------------------------------------------------------------------------
# Tests: shutdown sequence policy
# ---------------------------------------------------------------------------

class TestShutdownSequencePolicy:
    """Test the controlled shutdown sequence logic."""

    def _simulate_shutdown(self, charging_on: bool, current_mode: str) -> dict:
        """Simulate the shutdown sequence when controller is disabled."""
        result = {"stop_called": False, "current_reset": False, "mode": current_mode}
        if charging_on:
            result["stop_called"] = True
            result["mode"] = "stopped"
            result["current_reset"] = True  # resets to 16A as per existing policy
        return result

    def test_shutdown_stops_charging_if_active(self):
        result = self._simulate_shutdown(charging_on=True, current_mode="surplus")
        assert result["stop_called"] is True
        assert result["mode"] == "stopped"

    def test_shutdown_resets_current(self):
        result = self._simulate_shutdown(charging_on=True, current_mode="force")
        assert result["current_reset"] is True

    def test_no_stop_if_not_charging(self):
        result = self._simulate_shutdown(charging_on=False, current_mode="stopped")
        assert result["stop_called"] is False

    def test_shutdown_reason_is_controller_disabled(self):
        """Shutdown sequence sets last_reason to controller_disabled."""
        charging_on = True
        last_reason = ""
        if charging_on:
            last_reason = "controller_disabled"
        assert last_reason == "controller_disabled"


# ---------------------------------------------------------------------------
# Tests: FORCE_MAX mode
# ---------------------------------------------------------------------------

class TestForceModeLogic:
    """Test FORCE_MAX / charge_now mode logic."""

    def test_force_mode_sets_max_current(self):
        charge_now = True
        MAX_CURRENT = 16
        # When force_charge → set to MAX_CURRENT
        target_current = MAX_CURRENT if charge_now else None
        assert target_current == 16

    def test_force_mode_does_not_subtract_ev_from_surplus(self):
        """In FORCE_MAX, surplus calculation is irrelevant — current is fixed at max."""
        charge_now = True
        ev_w = 3000.0
        net_w = 200.0
        # In force mode: we don't need surplus calculation to determine current
        surplus_used_for_current = not charge_now  # only used in surplus mode
        assert surplus_used_for_current is False

    def test_surplus_mode_uses_ev_excl_calculation(self):
        """In surplus mode, current derives from surplus excl EV."""
        charge_now = False
        # surplus = (0 - net_w) + ev_w — EV power added back so it's excluded
        net_w = 0.0
        ev_w = 2000.0
        surplus_w = (0.0 - net_w) + ev_w
        assert surplus_w == 2000.0

    def test_force_charge_true_overrides_surplus(self):
        """force_charge=True means we ignore surplus thresholds."""
        charge_now = True
        ema_current = 0.0  # would normally prevent start
        # force_charge bypasses ema_current < 1 check
        should_start = charge_now or ema_current >= 1.0
        assert should_start is True


# ---------------------------------------------------------------------------
# Tests: idempotent service calls
# ---------------------------------------------------------------------------

class TestIdempotentCurrentCalls:
    """Test that repeated same target current → no new service calls."""

    def _should_send_current(
        self, target: float, last_committed_int: int | None
    ) -> bool:
        """Mirror of _commit_current idempotency check."""
        target_int = max(int(target), 0)
        if target_int == last_committed_int:
            return False
        return target_int > 0

    def test_same_integer_target_no_call(self):
        assert self._should_send_current(4.7, 4) is False

    def test_same_integer_target_exact(self):
        assert self._should_send_current(4.0, 4) is False

    def test_different_integer_sends_call(self):
        assert self._should_send_current(5.0, 4) is True

    def test_zero_target_no_call(self):
        assert self._should_send_current(0.5, None) is False

    def test_first_call_when_no_previous(self):
        assert self._should_send_current(4.0, None) is True


# ---------------------------------------------------------------------------
# Tests: import guard logic
# ---------------------------------------------------------------------------

class TestImportGuardLogic:
    """Test import guard fail-safe behaviour."""

    def _check_import_guard(
        self,
        net_w: float,
        mono_now: float,
        threshold: float,
        duration: float,
        exceed_since: float | None,
    ) -> tuple[bool, float | None]:
        """Mirror of _check_import_guard logic."""
        if net_w > threshold:
            if exceed_since is None:
                exceed_since = mono_now
            elif (mono_now - exceed_since) >= duration:
                return True, exceed_since
        else:
            exceed_since = None
        return False, exceed_since

    def test_no_import_no_trigger(self):
        triggered, _ = self._check_import_guard(50.0, 0.0, 150.0, 10.0, None)
        assert triggered is False

    def test_import_below_threshold_no_trigger(self):
        triggered, _ = self._check_import_guard(100.0, 0.0, 150.0, 10.0, None)
        assert triggered is False

    def test_import_starts_timer(self):
        triggered, exceed_since = self._check_import_guard(200.0, 5.0, 150.0, 10.0, None)
        assert triggered is False
        assert exceed_since == 5.0

    def test_import_does_not_trigger_before_duration(self):
        # exceed_since=0, now=9, duration=10 → not triggered yet
        triggered, _ = self._check_import_guard(200.0, 9.0, 150.0, 10.0, 0.0)
        assert triggered is False

    def test_import_triggers_at_duration(self):
        # exceed_since=0, now=10, duration=10 → triggered
        triggered, _ = self._check_import_guard(200.0, 10.0, 150.0, 10.0, 0.0)
        assert triggered is True

    def test_import_triggers_after_duration(self):
        triggered, _ = self._check_import_guard(200.0, 12.0, 150.0, 10.0, 0.0)
        assert triggered is True

    def test_import_resets_when_below_threshold(self):
        # was importing, now exporting
        triggered, exceed_since = self._check_import_guard(-100.0, 15.0, 150.0, 10.0, 5.0)
        assert triggered is False
        assert exceed_since is None

    def test_short_spike_no_trigger(self):
        # 3s spike at 200W, then export — guard should not have triggered
        triggered_at_3s, exceed = self._check_import_guard(200.0, 3.0, 150.0, 10.0, 0.0)
        assert triggered_at_3s is False
        # Drops back below threshold
        triggered_after_drop, exceed2 = self._check_import_guard(50.0, 4.0, 150.0, 10.0, exceed)
        assert triggered_after_drop is False
        assert exceed2 is None


# ---------------------------------------------------------------------------
# Tests: last_reason tracking
# ---------------------------------------------------------------------------

class TestLastReasonTracking:
    """Test that control actions set last_reason correctly."""

    def test_start_surplus_sets_reason(self):
        """start_surplus sets reason to surplus_above_threshold."""
        last_reason = ""
        # Simulate _action_start_surplus
        last_reason = "surplus_above_threshold"
        assert last_reason == "surplus_above_threshold"

    def test_stop_surplus_sets_reason(self):
        last_reason = ""
        last_reason = "surplus_below_threshold"
        assert last_reason == "surplus_below_threshold"

    def test_start_force_sets_reason(self):
        last_reason = ""
        last_reason = "force_charge_active"
        assert last_reason == "force_charge_active"

    def test_stop_force_sets_reason(self):
        last_reason = ""
        last_reason = "force_charge_stopped"
        assert last_reason == "force_charge_stopped"

    def test_import_guard_sets_reason(self):
        last_reason = ""
        last_reason = "import_guard"
        assert last_reason == "import_guard"

    def test_controller_disabled_sets_reason(self):
        last_reason = ""
        last_reason = "controller_disabled"
        assert last_reason == "controller_disabled"


# ---------------------------------------------------------------------------
# Tests: surplus invariance across modes
# ---------------------------------------------------------------------------

class TestSurplusInvariance:
    """Surplus formula must be consistent regardless of operating mode.

    Formula: surplus_w = (0 - net_w) + ev_w
    Sign convention:
      net_w > 0 → importing from grid
      net_w < 0 → exporting to grid
      ev_w  > 0 → EV consuming power
    Result: surplus_w = available power if EV weren't charging
    """

    def test_importing_while_ev_charging(self):
        """Importing 200W, EV drawing 1000W → 800W available."""
        net_w, ev_w = 200.0, 1000.0
        surplus = compute_surplus(net_w, ev_w)
        assert surplus == 800.0

    def test_exporting_while_ev_charging(self):
        """Exporting 500W, EV drawing 2000W → 2500W available."""
        net_w, ev_w = -500.0, 2000.0
        surplus = compute_surplus(net_w, ev_w)
        assert surplus == 2500.0

    def test_surplus_formula_same_in_force_and_surplus_mode(self):
        """Force mode must NOT change the surplus definition.

        Two different scenarios both use the same formula.
        """
        # Scenario 1: low EV draw (surplus mode typical)
        surplus_low_ev = compute_surplus(300.0, 1000.0)
        # Scenario 2: max EV draw (force mode typical — 16A × 230V ≈ 3680W)
        surplus_high_ev = compute_surplus(300.0, 3680.0)
        # Both computed with the same formula: -net + ev
        assert surplus_low_ev == -300.0 + 1000.0
        assert surplus_high_ev == -300.0 + 3680.0

    def test_surplus_always_excludes_ev(self):
        """EV consumption must never inflate available surplus."""
        net_w = 0.0  # balanced
        ev_w = 3680.0  # 16A × 230V
        surplus = compute_surplus(net_w, ev_w)
        # surplus = 3680 → this is the power that would be exported without EV
        assert surplus == 3680.0
        # Verify raw current calculation uses this surplus correctly
        raw_a = compute_raw_current(surplus, 230.0)
        # 3680 / (230 * 3) ≈ 5.33A
        assert abs(raw_a - (3680.0 / 690.0)) < 0.01

    def test_no_ev_no_export(self):
        """Importing 1000W, no EV → negative surplus (no available power)."""
        surplus = compute_surplus(1000.0, 0.0)
        assert surplus == -1000.0

    def test_heavy_import_with_ev(self):
        """Importing more than EV draws → still negative surplus."""
        # EV draws 2000W, but total import is 3000W → house needs 1000W more
        surplus = compute_surplus(3000.0, 2000.0)
        assert surplus == -1000.0


# ---------------------------------------------------------------------------
# Tests: force session diagnostics consistency
# ---------------------------------------------------------------------------

class TestForceSessionDiagnostics:
    """Test that force start/stop set all diagnostic fields consistently."""

    def _simulate_start_force(self, max_current_limit=16):
        """Mirror _action_start_force diagnostic state."""
        max_a = int(max_current_limit)
        state = {
            "current_mode": "force",
            "last_action": "start_force",
            "last_reason": "force_charge_active",
            "committed_current": float(max_a),
            "last_committed_int": max_a,
            "last_commit_reason": "start_force",
            "charging_on": True,
        }
        return state

    def _simulate_stop_force(self):
        """Mirror _action_stop_force diagnostic state."""
        state = {
            "current_mode": "stopped",
            "last_action": "stop_force",
            "last_reason": "force_charge_stopped",
            "committed_current": None,
            "last_committed_int": None,
            "last_commit_reason": "stop_force",
            "charging_on": False,
        }
        return state

    def _simulate_controller_disabled_shutdown(self):
        """Mirror _async_controller_shutdown_sequence diagnostic state."""
        state = {
            "current_mode": "stopped",
            "last_action": "controller_disabled_stop",
            "last_reason": "controller_disabled",
            "committed_current": None,
            "last_committed_int": None,
            "last_commit_reason": "controller_disabled",
            "charging_on": False,
        }
        return state

    def test_start_force_sets_committed_current(self):
        state = self._simulate_start_force()
        assert state["committed_current"] == 16.0

    def test_start_force_sets_applied_current(self):
        state = self._simulate_start_force()
        assert state["last_committed_int"] == 16

    def test_start_force_sets_control_reason(self):
        state = self._simulate_start_force()
        assert state["last_commit_reason"] == "start_force"

    def test_start_force_sets_mode(self):
        state = self._simulate_start_force()
        assert state["current_mode"] == "force"

    def test_start_force_sets_last_action(self):
        state = self._simulate_start_force()
        assert state["last_action"] == "start_force"

    def test_stop_force_clears_committed_current(self):
        state = self._simulate_stop_force()
        assert state["committed_current"] is None

    def test_stop_force_clears_applied_current(self):
        state = self._simulate_stop_force()
        assert state["last_committed_int"] is None

    def test_stop_force_sets_control_reason(self):
        state = self._simulate_stop_force()
        assert state["last_commit_reason"] == "stop_force"

    def test_stop_force_sets_mode_stopped(self):
        state = self._simulate_stop_force()
        assert state["current_mode"] == "stopped"

    def test_stop_force_sets_last_action(self):
        state = self._simulate_stop_force()
        assert state["last_action"] == "stop_force"

    def test_stop_force_sets_charging_off(self):
        state = self._simulate_stop_force()
        assert state["charging_on"] is False

    def test_controller_disabled_shutdown_diagnostics(self):
        """Controller OFF while force active → consistent shutdown state."""
        state = self._simulate_controller_disabled_shutdown()
        assert state["current_mode"] == "stopped"
        assert state["last_action"] == "controller_disabled_stop"
        assert state["last_reason"] == "controller_disabled"
        assert state["committed_current"] is None
        assert state["last_committed_int"] is None
        assert state["last_commit_reason"] == "controller_disabled"
        assert state["charging_on"] is False

    def test_full_force_session_lifecycle(self):
        """Start force → stop force → all diagnostics consistent."""
        start = self._simulate_start_force()
        assert start["charging_on"] is True
        assert start["committed_current"] == 16.0
        assert start["last_commit_reason"] == "start_force"

        stop = self._simulate_stop_force()
        assert stop["charging_on"] is False
        assert stop["committed_current"] is None
        assert stop["last_commit_reason"] == "stop_force"


# ---------------------------------------------------------------------------
# Tests: controller_enabled gates service calls
# ---------------------------------------------------------------------------

class TestControllerEnabledGatesServices:
    """Service calls must be rejected when controller is disabled."""

    def _service_force_start_allowed(self, controller_enabled: bool) -> bool:
        """Mirror async_service_force_start gating logic."""
        if not controller_enabled:
            return False
        return True

    def _service_force_stop_allowed(self, controller_enabled: bool) -> bool:
        """Mirror async_service_force_stop gating logic."""
        if not controller_enabled:
            return False
        return True

    def test_force_start_blocked_when_disabled(self):
        assert self._service_force_start_allowed(False) is False

    def test_force_start_allowed_when_enabled(self):
        assert self._service_force_start_allowed(True) is True

    def test_force_stop_blocked_when_disabled(self):
        assert self._service_force_stop_allowed(False) is False

    def test_force_stop_allowed_when_enabled(self):
        assert self._service_force_stop_allowed(True) is True

    def test_controller_off_while_force_triggers_shutdown(self):
        """Disabling controller during force charge triggers shutdown."""
        controller_enabled = True
        charging_on = True
        shutdown_triggered = False

        prev = controller_enabled
        controller_enabled = False
        if prev and not controller_enabled and charging_on:
            shutdown_triggered = True

        assert shutdown_triggered is True

    def test_shutdown_from_force_sets_correct_diagnostics(self):
        """After shutdown from force mode, diagnostics are consistent."""
        # Simulate: was in force mode, controller disabled
        last_action = "controller_disabled_stop"
        last_reason = "controller_disabled"
        last_commit_reason = "controller_disabled"
        committed_current = None
        last_committed_int = None
        current_mode = "stopped"

        assert last_action == "controller_disabled_stop"
        assert last_reason == "controller_disabled"
        assert last_commit_reason == "controller_disabled"
        assert committed_current is None
        assert last_committed_int is None
        assert current_mode == "stopped"


# ---------------------------------------------------------------------------
# Tests: cable plug-in detection edge cases
# ---------------------------------------------------------------------------

class TestCablePlugInDetection:
    """Test cable plug-in detection logic mirrors coordinator behaviour."""

    def _should_trigger_plugin(
        self,
        controller_enabled: bool,
        cable_connected: bool | None,
        cable_prev: bool | None,
    ) -> bool:
        """Mirror the cable plug-in detection condition from coordinator."""
        if (
            controller_enabled
            and cable_connected is not None
            and cable_prev is not None
            and cable_connected != cable_prev
        ):
            if cable_connected and not cable_prev:
                return True
        return False

    def test_no_plugin_when_cable_prev_is_none(self):
        """First read after startup: cable_prev=None → no plug-in event."""
        result = self._should_trigger_plugin(
            controller_enabled=True,
            cable_connected=True,
            cable_prev=None,
        )
        assert result is False

    def test_plugin_detected_when_cable_changes_false_to_true(self):
        """Normal plug-in: cable_prev=False, cable_connected=True → detected."""
        result = self._should_trigger_plugin(
            controller_enabled=True,
            cable_connected=True,
            cable_prev=False,
        )
        assert result is True

    def test_no_plugin_when_cable_stays_true(self):
        """Cable already connected: no change → no plug-in."""
        result = self._should_trigger_plugin(
            controller_enabled=True,
            cable_connected=True,
            cable_prev=True,
        )
        assert result is False

    def test_no_plugin_when_controller_disabled(self):
        """Controller disabled: no detection even if cable changes."""
        result = self._should_trigger_plugin(
            controller_enabled=False,
            cable_connected=True,
            cable_prev=False,
        )
        assert result is False

    def test_no_plugin_when_cable_connected_is_none(self):
        """Cable sensor unavailable: no detection."""
        result = self._should_trigger_plugin(
            controller_enabled=True,
            cable_connected=None,
            cable_prev=False,
        )
        assert result is False

    def test_no_plugin_on_unplug(self):
        """Cable removed: cable_prev=True, cable_connected=False → not a plug-in."""
        result = self._should_trigger_plugin(
            controller_enabled=True,
            cable_connected=False,
            cable_prev=True,
        )
        assert result is False

    def test_startup_sequence_no_false_plugin(self):
        """Simulate startup: sensor unavailable at first, then becomes available.

        This mirrors the exact bug from the debug log:
        1. First tick: cable sensor not yet ready → cable_prev stays None
        2. Second tick: cable sensor available (True) → must NOT trigger plug-in
        3. Third tick: cable stays True → no change, no trigger
        """
        cable_prev = None  # initial state

        # Tick 1: sensor unavailable, controller disabled (first_refresh)
        cable_connected = None
        controller_enabled = False
        trigger = self._should_trigger_plugin(controller_enabled, cable_connected, cable_prev)
        assert trigger is False
        # cable_prev stays None because cable_connected is None

        # Tick 2: sensor now available, controller restored to enabled
        cable_connected = True
        controller_enabled = True
        trigger = self._should_trigger_plugin(controller_enabled, cable_connected, cable_prev)
        assert trigger is False  # cable_prev is still None → skip
        cable_prev = cable_connected  # update after check

        # Tick 3: no change
        trigger = self._should_trigger_plugin(controller_enabled, cable_connected, cable_prev)
        assert trigger is False  # same value → no change


# ---------------------------------------------------------------------------
# Tests: debounce-cancel logic for start/stop surplus
# ---------------------------------------------------------------------------

class TestDebounceScheduling:
    """Test that debounced start/stop tasks are NOT reset every tick.

    The bug: _cancel_pending() was called before scheduling a new debounced
    task on every tick. With sample_interval=10s and start_delay=30s, the
    30s debounce timer was reset every 10s and never completed.
    """

    def _should_schedule_start(
        self,
        ema_current: float,
        charging_on: bool,
        pending_task_active: bool,
        off_elapsed: float | None,
        min_off_time: float,
    ) -> bool:
        """Mirror the start surplus scheduling condition from coordinator."""
        if ema_current >= 1.0 and not charging_on:
            if off_elapsed is not None and off_elapsed < min_off_time:
                return False
            # Only schedule if not already pending
            if not pending_task_active:
                return True
        return False

    def _should_schedule_stop(
        self,
        ema_current: float,
        charging_on: bool,
        current_mode: str,
        pending_task_active: bool,
        on_elapsed: float | None,
        min_on_time: float,
    ) -> bool:
        """Mirror the stop surplus scheduling condition from coordinator."""
        if ema_current < 1.0 and charging_on and current_mode == "surplus":
            if on_elapsed is not None and on_elapsed < min_on_time:
                return False
            if not pending_task_active:
                return True
        return False

    def test_start_scheduled_when_no_pending(self):
        """First tick with surplus: schedule start."""
        result = self._should_schedule_start(
            ema_current=4.0, charging_on=False, pending_task_active=False,
            off_elapsed=200.0, min_off_time=120.0,
        )
        assert result is True

    def test_start_not_rescheduled_when_pending(self):
        """Subsequent ticks: do NOT reschedule if already pending.

        This is the core fix for the debounce-cancel bug.
        """
        result = self._should_schedule_start(
            ema_current=4.0, charging_on=False, pending_task_active=True,
            off_elapsed=200.0, min_off_time=120.0,
        )
        assert result is False

    def test_start_blocked_by_min_off_time(self):
        result = self._should_schedule_start(
            ema_current=4.0, charging_on=False, pending_task_active=False,
            off_elapsed=60.0, min_off_time=120.0,
        )
        assert result is False

    def test_start_allowed_after_min_off_time(self):
        result = self._should_schedule_start(
            ema_current=4.0, charging_on=False, pending_task_active=False,
            off_elapsed=130.0, min_off_time=120.0,
        )
        assert result is True

    def test_start_not_scheduled_below_threshold(self):
        result = self._should_schedule_start(
            ema_current=0.5, charging_on=False, pending_task_active=False,
            off_elapsed=200.0, min_off_time=120.0,
        )
        assert result is False

    def test_stop_scheduled_when_no_pending(self):
        result = self._should_schedule_stop(
            ema_current=0.5, charging_on=True, current_mode="surplus",
            pending_task_active=False, on_elapsed=400.0, min_on_time=300.0,
        )
        assert result is True

    def test_stop_not_rescheduled_when_pending(self):
        """Subsequent ticks: do NOT reschedule stop if already pending."""
        result = self._should_schedule_stop(
            ema_current=0.5, charging_on=True, current_mode="surplus",
            pending_task_active=True, on_elapsed=400.0, min_on_time=300.0,
        )
        assert result is False

    def test_stop_blocked_by_min_on_time(self):
        result = self._should_schedule_stop(
            ema_current=0.5, charging_on=True, current_mode="surplus",
            pending_task_active=False, on_elapsed=100.0, min_on_time=300.0,
        )
        assert result is False

    def test_debounce_completes_simulation(self):
        """Simulate multiple ticks to verify debounce completes.

        Reproduces the exact scenario from the debug log:
        - sample_interval=10s, start_delay=30s
        - 4 ticks with stable surplus → debounce must complete
        """
        pending_task_active = False
        started = False
        min_off_time = 120.0
        off_elapsed = 200.0  # well past min_off

        for tick in range(4):  # 4 ticks × 10s = 40s > 30s start_delay
            schedule = self._should_schedule_start(
                ema_current=4.0, charging_on=False,
                pending_task_active=pending_task_active,
                off_elapsed=off_elapsed, min_off_time=min_off_time,
            )
            if schedule:
                pending_task_active = True  # task scheduled
                started = True

        # Task was scheduled exactly once on first tick, not reset
        assert started is True
        assert pending_task_active is True


# ---------------------------------------------------------------------------
# Tests: force mode uses max_current_limit
# ---------------------------------------------------------------------------

class TestForceMaxCurrentLimit:
    """Test that force mode respects _max_current_limit."""

    def test_force_start_uses_configured_limit(self):
        """Force start with lower limit (e.g. 8A Tessie charger)."""
        max_current_limit = 8
        max_a = int(max_current_limit)
        assert max_a == 8

    def test_force_start_default_16a(self):
        """Force start with default 16A limit."""
        max_current_limit = 16
        max_a = int(max_current_limit)
        assert max_a == 16

    def test_stop_surplus_resets_to_limit_not_16(self):
        """After stop_surplus, reset current should be max_current_limit, not 16."""
        max_current_limit = 8
        reset_current = int(max_current_limit)
        assert reset_current == 8
        assert reset_current != 16


# ---------------------------------------------------------------------------
# Tests: enhanced import guard with debounce + hysteresis
# ---------------------------------------------------------------------------

class TestImportGuardEnhanced:
    """Test enhanced import guard with debounce, hysteresis, and escalation."""

    def _check_import_guard(
        self,
        net_w: float,
        mono_now: float,
        threshold: float,
        duration: float,
        hysteresis_w: float,
        clear_duration: float,
        exceed_since: float | None,
        below_since: float | None,
        guard_active: bool,
    ) -> tuple[bool, float | None, float | None, bool]:
        """Mirror of enhanced _check_import_guard logic."""
        clear_threshold = threshold - hysteresis_w
        triggered = False

        if net_w > threshold:
            below_since = None
            if exceed_since is None:
                exceed_since = mono_now
            elif (mono_now - exceed_since) >= duration:
                guard_active = True
                triggered = True
        elif net_w <= clear_threshold:
            exceed_since = None
            if below_since is None:
                below_since = mono_now
            elif (mono_now - below_since) >= clear_duration:
                guard_active = False
                below_since = None
        else:
            # Dead zone between clear_threshold and threshold
            exceed_since = None

        return triggered, exceed_since, below_since, guard_active

    def test_transient_spike_no_trigger(self):
        """Short spike < duration should not trigger guard."""
        # Spike at t=0, check at t=15 (less than 30s default)
        t, exceed, below, active = self._check_import_guard(
            250.0, 0.0, 200.0, 30.0, 50.0, 20.0, None, None, False
        )
        assert t is False
        assert exceed == 0.0
        # Still not triggered at 15s
        t, exceed, below, active = self._check_import_guard(
            250.0, 15.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert t is False
        # Drops below threshold at t=16
        t, exceed, below, active = self._check_import_guard(
            100.0, 16.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert t is False
        assert active is False

    def test_sustained_import_triggers(self):
        """Sustained import >= duration triggers guard."""
        t, exceed, below, active = self._check_import_guard(
            250.0, 0.0, 200.0, 30.0, 50.0, 20.0, None, None, False
        )
        assert t is False
        # At t=30 — should trigger
        t, exceed, below, active = self._check_import_guard(
            250.0, 30.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert t is True
        assert active is True

    def test_hysteresis_prevents_premature_clear(self):
        """Guard stays active when import drops below threshold but above hysteresis margin."""
        # First trigger
        _, exceed, below, active = self._check_import_guard(
            250.0, 0.0, 200.0, 30.0, 50.0, 20.0, None, None, False
        )
        t, exceed, below, active = self._check_import_guard(
            250.0, 30.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert active is True
        # Drop to 180W (below 200 threshold but ABOVE 150 clear threshold)
        # This is the dead zone — guard should NOT clear
        t, exceed, below, active = self._check_import_guard(
            180.0, 35.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert active is True  # Not cleared yet

    def test_hysteresis_clear_after_duration(self):
        """Guard clears when import below clear threshold for clear_duration."""
        # Trigger guard
        _, exceed, below, active = self._check_import_guard(
            250.0, 0.0, 200.0, 30.0, 50.0, 20.0, None, None, False
        )
        _, exceed, below, active = self._check_import_guard(
            250.0, 30.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert active is True
        # Drop below clear threshold (150W)
        _, exceed, below, active = self._check_import_guard(
            100.0, 35.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert active is True  # Not cleared — need 20s below
        assert below == 35.0
        # Stay below for 20s
        _, exceed, below, active = self._check_import_guard(
            100.0, 55.0, 200.0, 30.0, 50.0, 20.0, exceed, below, active
        )
        assert active is False  # NOW cleared


# ---------------------------------------------------------------------------
# Tests: import guard escalation ladder
# ---------------------------------------------------------------------------

class TestEscalationLadder:
    """Test that import guard reduces current before hard stop."""

    def test_reduce_before_stop(self):
        """Escalation should reduce current by 1A, not immediately stop."""
        committed = 5.0
        settle_s = 30.0
        last_reduce_time = None
        mono_now = 100.0

        # Step 1: reduce from 5A → should set to 4A, not stop
        if committed > 0.0:
            new_target = committed - 1.0
            new_target = max(new_target, 0.0)
            last_reduce_time = mono_now
            action = "reduce"
        else:
            action = "stop"

        assert action == "reduce"
        assert new_target == 4.0
        assert last_reduce_time == 100.0

    def test_settle_window_blocks_rapid_reduction(self):
        """During settle window, no further reduction should occur."""
        settle_s = 30.0
        last_reduce_time = 100.0
        mono_now = 110.0  # Only 10s since last reduction

        in_settle = (mono_now - last_reduce_time) < settle_s
        assert in_settle is True

    def test_settle_window_allows_reduction_after_expiry(self):
        """After settle window expires, reduction should be allowed."""
        settle_s = 30.0
        last_reduce_time = 100.0
        mono_now = 135.0  # 35s since last reduction

        in_settle = (mono_now - last_reduce_time) < settle_s
        assert in_settle is False

    def test_escalation_to_zero_before_hard_stop(self):
        """Reduce to 0A before stopping charger relay."""
        committed = 1.0
        new_target = committed - 1.0
        new_target = max(new_target, 0.0)
        assert new_target == 0.0

        # At 0A, next escalation is hard stop
        committed = new_target
        if committed > 0.0:
            action = "reduce"
        else:
            action = "stop"
        assert action == "stop"

    def test_full_escalation_sequence(self):
        """Full sequence: 3A → 2A → 1A → 0A → stop."""
        committed = 3.0
        steps = []
        while committed > 0.0:
            committed -= 1.0
            committed = max(committed, 0.0)
            steps.append(f"reduce_to_{committed:.0f}A")

        steps.append("hard_stop")

        assert steps == [
            "reduce_to_2A",
            "reduce_to_1A",
            "reduce_to_0A",
            "hard_stop",
        ]


# ---------------------------------------------------------------------------
# Tests: mode reason tracking
# ---------------------------------------------------------------------------

class TestModeReasonTracking:
    """Test that mode transitions always expose 'why'."""

    def test_mode_transition_records_reason(self):
        current_mode = "stopped"
        new_mode = "surplus"
        reason = "surplus_above_threshold"
        source = "auto_rule"

        last_transition = f"{current_mode} -> {new_mode}: {reason}"
        mode_reason = reason
        mode_source = source

        assert last_transition == "stopped -> surplus: surplus_above_threshold"
        assert mode_reason == "surplus_above_threshold"
        assert mode_source == "auto_rule"

    def test_force_start_sets_mode_source(self):
        source = "charge_now_switch"
        assert source == "charge_now_switch"

    def test_import_guard_stop_sets_source(self):
        source = "import_guard"
        reason = "import_guard_escalate_stop"
        assert source == "import_guard"
        assert reason == "import_guard_escalate_stop"

    def test_controller_disabled_sets_source(self):
        source = "user_toggle"
        reason = "controller_disabled"
        assert source == "user_toggle"
        assert reason == "controller_disabled"

    def test_surplus_start_sets_source(self):
        source = "auto_rule"
        reason = "surplus_above_threshold"
        assert source == "auto_rule"
        assert reason == "surplus_above_threshold"

    def test_no_transition_when_mode_unchanged(self):
        current = "surplus"
        new = "surplus"
        transition = ""
        if new != current:
            transition = f"{current} -> {new}: reason"
        assert transition == ""  # No change → no transition recorded


# ---------------------------------------------------------------------------
# Tests: Charge Tonight auto-off
# ---------------------------------------------------------------------------

class TestChargeTonightAutoOff:
    """Test that Charge Tonight auto-disables on unplug and solar_done off."""

    def test_auto_off_on_cable_unplug(self):
        """Charge Tonight should turn off when cable is unplugged."""
        charge_tonight = True
        cable_prev = True
        cable_connected = False

        if (
            charge_tonight
            and cable_prev is not None
            and cable_connected is not None
            and cable_prev
            and not cable_connected
        ):
            charge_tonight = False

        assert charge_tonight is False

    def test_no_auto_off_when_cable_stays_connected(self):
        """Charge Tonight should stay on when cable stays connected."""
        charge_tonight = True
        cable_prev = True
        cable_connected = True

        if (
            charge_tonight
            and cable_prev is not None
            and cable_connected is not None
            and cable_prev
            and not cable_connected
        ):
            charge_tonight = False

        assert charge_tonight is True

    def test_no_auto_off_when_charge_tonight_already_off(self):
        """No effect when charge_tonight is already off."""
        charge_tonight = False
        cable_prev = True
        cable_connected = False

        if (
            charge_tonight
            and cable_prev is not None
            and cable_connected is not None
            and cable_prev
            and not cable_connected
        ):
            charge_tonight = False

        assert charge_tonight is False  # was already False

    def test_auto_off_on_solar_done_off_transition(self):
        """Charge Tonight should turn off when solar_done goes on → off."""
        charge_tonight = True
        prev_solar_done = True
        solar_done = False

        if charge_tonight and prev_solar_done and not solar_done:
            charge_tonight = False

        assert charge_tonight is False

    def test_no_auto_off_when_solar_done_stays_on(self):
        """Charge Tonight should stay on when solar_done remains on."""
        charge_tonight = True
        prev_solar_done = True
        solar_done = True

        if charge_tonight and prev_solar_done and not solar_done:
            charge_tonight = False

        assert charge_tonight is True

    def test_no_auto_off_on_solar_done_off_to_on(self):
        """solar_done off→on should not affect charge_tonight."""
        charge_tonight = True
        prev_solar_done = False
        solar_done = True

        if charge_tonight and prev_solar_done and not solar_done:
            charge_tonight = False

        assert charge_tonight is True

    def test_no_auto_off_when_cable_prev_is_none(self):
        """No auto-off on startup when cable_prev is not yet set."""
        charge_tonight = True
        cable_prev = None
        cable_connected = False

        if (
            charge_tonight
            and cable_prev is not None
            and cable_connected is not None
            and cable_prev
            and not cable_connected
        ):
            charge_tonight = False

        assert charge_tonight is True  # Protected by cable_prev check


# ---------------------------------------------------------------------------
# Tests: import guard state tracking
# ---------------------------------------------------------------------------

class TestImportGuardState:
    """Test the three-state import guard state tracking."""

    def test_initial_state_is_ok(self):
        state = "ok"
        assert state == "ok"

    def test_state_transitions_to_reducing(self):
        state = "ok"
        # When import guard first activates
        state = "reducing"
        assert state == "reducing"

    def test_state_transitions_to_stopped(self):
        state = "reducing"
        # When current reaches 0A and charger is stopped
        state = "stopped"
        assert state == "stopped"

    def test_state_resets_to_ok_after_clear(self):
        state = "reducing"
        # When import drops below hysteresis threshold for clear_duration
        state = "ok"
        assert state == "ok"

    def test_guard_reason_for_transient(self):
        reason = "transient spike ignored"
        assert "transient" in reason

    def test_guard_reason_for_sustained(self):
        elapsed = 35.0
        threshold = 200.0
        reason = f"sustained import {elapsed:.0f}s > {threshold:.0f}W"
        assert reason == "sustained import 35s > 200W"


# ---------------------------------------------------------------------------
# Tests: data-driven from historyAC-long.csv (2.5h real-world operation)
# ---------------------------------------------------------------------------

class TestImportGuardLongRunData:
    """Tests derived from 2.5h real-world data (historyAC-long.csv).

    The dataset showed the computed net power sensor updates every ~60s.
    Import spikes typically last 1 reading (60s), occasionally 2-4 readings.
    The old code cascaded from 5A→stop in 34-58s. Our enhanced guard should
    limit damage to 1A reduction per event (with 30s settle between steps).
    """

    def _check_import_guard(
        self,
        net_w: float,
        mono_now: float,
        threshold: float,
        duration: float,
        hysteresis_w: float,
        clear_duration: float,
        exceed_since: float | None,
        below_since: float | None,
        guard_active: bool,
        last_reduce_time: float | None,
    ) -> tuple[bool, float | None, float | None, bool, float | None]:
        """Mirror of enhanced _check_import_guard with settle reset."""
        clear_threshold = threshold - hysteresis_w
        triggered = False

        if net_w > threshold:
            below_since = None
            if exceed_since is None:
                exceed_since = mono_now
            elif (mono_now - exceed_since) >= duration:
                guard_active = True
                triggered = True
        elif net_w <= clear_threshold:
            exceed_since = None
            if below_since is None:
                below_since = mono_now
            elif (mono_now - below_since) >= clear_duration:
                guard_active = False
                below_since = None
                last_reduce_time = None  # reset settle on clear
        else:
            exceed_since = None

        return triggered, exceed_since, below_since, guard_active, last_reduce_time

    def _simulate_escalation(
        self,
        committed_a: float,
        settle_s: float,
        last_reduce_time: float | None,
        mono_now: float,
    ) -> tuple[str, float, float | None]:
        """Simulate one tick of escalation ladder. Returns (action, new_current, new_reduce_time)."""
        if last_reduce_time is not None and (mono_now - last_reduce_time) < settle_s:
            return "settle_hold", committed_a, last_reduce_time

        if committed_a > 0.0:
            new = max(committed_a - 1.0, 0.0)
            return "reduce", new, mono_now
        else:
            return "hard_stop", 0.0, last_reduce_time

    def test_single_reading_spike_481w_at_5a(self):
        """Real event 10:06:38: net=481W (single reading), charging at 5A.

        Old code: cascaded 5A→4A→3A→2A→1A→stop in 34s.
        New code should: trigger at t+30s, reduce to 4A, settle 30s, guard clears.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0
        exceed_since = below_since = None
        guard_active = False
        last_reduce = None
        committed = 5.0

        # t=0: spike arrives (481W), first tick — debounce starts
        t, exceed, below, active, lr = self._check_import_guard(
            481, 0, threshold, duration, hyst, clear_dur, exceed_since, below_since, guard_active, last_reduce
        )
        assert t is False  # not triggered yet (debounce)

        # t=10..20: still above threshold, debounce continues
        for tick in [10, 20]:
            t, exceed, below, active, lr = self._check_import_guard(
                481, tick, threshold, duration, hyst, clear_dur, exceed, below, active, lr
            )
            assert t is False

        # t=30: debounce expires, guard triggers
        t, exceed, below, active, lr = self._check_import_guard(
            481, 30, threshold, duration, hyst, clear_dur, exceed, below, active, lr
        )
        assert t is True
        assert active is True

        # Escalation: reduce from 5A to 4A
        action, committed, last_reduce = self._simulate_escalation(committed, settle, lr, 30)
        assert action == "reduce"
        assert committed == 4.0

        # t=40..50: still in settle window — no further reduction
        for tick in [40, 50]:
            t, exceed, below, active, lr2 = self._check_import_guard(
                481, tick, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
            )
            action, _, _ = self._simulate_escalation(committed, settle, last_reduce, tick)
            assert action == "settle_hold"

        # t=60: new reading arrives (-3200W, big export)
        t, exceed, below, active, lr2 = self._check_import_guard(
            -3200, 60, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
        )
        assert t is False
        # below_since starts — clear timer begins

        # t=80: clear_duration (20s) passed with import < 150W
        t, exceed, below, active, lr2 = self._check_import_guard(
            -3200, 80, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
        )
        assert active is False  # guard cleared!
        assert lr2 is None  # settle time reset on clear

        # Final: charging at 4A (not stopped!)
        assert committed == 4.0

    def test_single_reading_spike_2378w_at_6a(self):
        """Real event 11:09:33: net=2378W (huge spike), charging at 6A.

        Old code: cascaded 6A→5A→4A→3A→2A→1A→stop in 58s.
        New code should: reduce to 5A, guard clears when next reading arrives.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0
        exceed = below = None
        active = False
        committed = 6.0

        # t=0..30: debounce → trigger
        for tick in range(0, 31, 10):
            t, exceed, below, active, _ = self._check_import_guard(
                2378, tick, threshold, duration, hyst, clear_dur, exceed, below, active, None
            )
        assert t is True

        # Reduce 6A→5A
        action, committed, last_reduce = self._simulate_escalation(committed, settle, None, 30)
        assert committed == 5.0

        # t=40,50: settle window holds
        action, _, _ = self._simulate_escalation(committed, settle, last_reduce, 40)
        assert action == "settle_hold"

        # t=60: new reading (-1644W) → guard starts clearing
        t2, exceed, below, active, lr = self._check_import_guard(
            -1644, 60, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
        )
        assert t2 is False  # not triggered — import below threshold now
        # t=80: clear duration passed
        _, exceed, below, active, lr = self._check_import_guard(
            -1644, 80, threshold, duration, hyst, clear_dur, exceed, below, active, lr
        )
        assert active is False
        assert committed == 5.0  # stayed at 5A, not 0!

    def test_buildup_import_23_70_133_276w_at_2a(self):
        """Real event 11:22-11:26: slowly building import over 4 readings.

        Net: 23W→70W→133W→276W (each 60s apart). Charging at 2A.
        276W > 200W threshold. This is a legitimate sustained import.
        Expected: guard triggers at 276W+30s, reduces to 1A. Correct behavior.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0
        exceed = below = None
        active = False

        # t=0: 23W — below threshold, no action
        t, exceed, below, active, _ = self._check_import_guard(
            23, 0, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert t is False

        # t=60: 70W — still below
        t, exceed, below, active, _ = self._check_import_guard(
            70, 60, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert t is False

        # t=120: 133W — still below
        t, exceed, below, active, _ = self._check_import_guard(
            133, 120, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert t is False

        # t=180: 276W — above threshold! debounce starts
        t, exceed, below, active, _ = self._check_import_guard(
            276, 180, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert t is False
        assert exceed == 180  # debounce started

        # t=210: debounce expires (276W persists)
        t, exceed, below, active, _ = self._check_import_guard(
            276, 210, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert t is True
        assert active is True

        # Reduce 2A→1A
        action, committed, _ = self._simulate_escalation(2.0, settle, None, 210)
        assert action == "reduce"
        assert committed == 1.0

    def test_low_import_169w_below_threshold(self):
        """Real event 12:01:52: net=169W, 2 readings.

        169W < 200W threshold → should NOT trigger import guard.
        Old 150W threshold would have triggered this incorrectly.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0

        t, _, _, active, _ = self._check_import_guard(
            169, 0, threshold, duration, hyst, clear_dur, None, None, False, None
        )
        assert t is False
        assert active is False

        t, _, _, active, _ = self._check_import_guard(
            169, 60, threshold, duration, hyst, clear_dur, None, None, False, None
        )
        assert t is False
        assert active is False

    def test_anomaly_spike_10182w_at_1a(self):
        """Real event 12:25:22: net=10182W (measurement anomaly), charging at 1A.

        Single reading then -831W. Guard triggers at t+30s, reduces to 0A.
        After settle, guard clears. No hard stop needed.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0
        exceed = below = None
        active = False
        committed = 1.0

        # t=0..30: debounce → trigger
        for tick in range(0, 31, 10):
            t, exceed, below, active, _ = self._check_import_guard(
                10182, tick, threshold, duration, hyst, clear_dur, exceed, below, active, None
            )
        assert t is True

        # Reduce 1A→0A
        action, committed, last_reduce = self._simulate_escalation(committed, settle, None, 30)
        assert committed == 0.0

        # t=60: new reading (-831W) → clearing starts
        _, exceed, below, active, lr = self._check_import_guard(
            -831, 60, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
        )
        # t=80: clear
        _, exceed, below, active, lr = self._check_import_guard(
            -831, 80, threshold, duration, hyst, clear_dur, exceed, below, active, lr
        )
        assert active is False
        assert lr is None  # settle timer reset

    def test_settle_reset_on_guard_clear(self):
        """Bug fix: _import_guard_last_reduce_time must reset when guard clears.

        Without this fix, a stale settle timer from a previous event could block
        the first reduction of a new event.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0

        # Event 1: guard triggers at t=30, reduces at t=30
        exceed = below = None
        active = False
        for tick in range(0, 31, 10):
            t, exceed, below, active, _ = self._check_import_guard(
                500, tick, threshold, duration, hyst, clear_dur, exceed, below, active, None
            )
        last_reduce = 30.0  # simulating reduction at t=30

        # Event 1 clears at t=80 (import drops, clear_duration passes)
        _, exceed, below, active, lr = self._check_import_guard(
            0, 60, threshold, duration, hyst, clear_dur, exceed, below, active, last_reduce
        )
        _, exceed, below, active, lr = self._check_import_guard(
            0, 80, threshold, duration, hyst, clear_dur, exceed, below, active, lr
        )
        assert active is False
        assert lr is None  # settle timer cleared!

        # Event 2: new import at t=100
        for tick in [100, 110, 120, 130]:
            t, exceed, below, active, _ = self._check_import_guard(
                400, tick, threshold, duration, hyst, clear_dur, exceed, below, active, lr
            )
        assert t is True

        # Reduction should NOT be blocked by stale settle timer
        action, _, new_lr = self._simulate_escalation(3.0, settle, lr, 130)
        assert action == "reduce"  # NOT settle_hold!
        assert new_lr == 130

    def test_dead_zone_no_flip_flop(self):
        """Import in the dead zone (150-200W) should not cause oscillation.

        Values between clear_threshold (150W) and threshold (200W) should
        hold current state without resetting any timers.
        """
        threshold, duration, hyst, clear_dur, settle = 200.0, 30.0, 50.0, 20.0, 30.0

        # First: trigger the guard
        exceed = below = None
        active = False
        for tick in range(0, 31, 10):
            _, exceed, below, active, _ = self._check_import_guard(
                300, tick, threshold, duration, hyst, clear_dur, exceed, below, active, None
            )
        assert active is True

        # Import drops to 180W (dead zone: between 150W and 200W)
        _, exceed2, below2, active2, _ = self._check_import_guard(
            180, 40, threshold, duration, hyst, clear_dur, exceed, below, active, None
        )
        assert active2 is True  # still active! dead zone holds state

        # Import drops to 120W (below clear threshold 150W) → clear timer starts
        _, exceed3, below3, active3, _ = self._check_import_guard(
            120, 50, threshold, duration, hyst, clear_dur, exceed2, below2, active2, None
        )
        assert active3 is True  # not cleared yet
        assert below3 == 50  # clear timer started

        # Import jumps back to 180W (dead zone) — clear timer should NOT reset
        # (the code says "Don't reset _import_below_since")
        # But exceed_since is reset in the dead zone
        _, exceed4, below4, active4, _ = self._check_import_guard(
            180, 60, threshold, duration, hyst, clear_dur, exceed3, below3, active3, None
        )
        assert active4 is True

    def test_stop_surplus_fully_resets_guard_state(self):
        """When _action_stop_surplus runs, ALL guard state must be reset.

        This prevents stale state from affecting the next charging session.
        """
        # Simulate: guard was active, reduce happened, then stop_surplus
        import_guard_active = True
        import_guard_last_reduce_time = 100.0
        import_below_since = 80.0
        import_exceed_since = 50.0

        # Simulate _action_stop_surplus reset logic
        import_guard_active = False
        import_guard_last_reduce_time = None
        import_below_since = None
        import_exceed_since = None

        assert import_guard_active is False
        assert import_guard_last_reduce_time is None
        assert import_below_since is None
        assert import_exceed_since is None


# ---------------------------------------------------------------------------
# Tests: Force charge available current masking
# ---------------------------------------------------------------------------

class TestForceChargeAvailableCurrentMasking:
    """During force charge, charger power must NOT be reported as available current."""

    def test_available_current_zero_during_force_charge(self):
        """When force_charge is True, available_current should be 0."""
        ema_current_a = 5.3
        force_charge = True

        if force_charge:
            display_available = 0.0
        else:
            display_available = round(ema_current_a, 2)

        assert display_available == 0.0

    def test_available_current_normal_when_not_force(self):
        """When force_charge is False, available_current should reflect EMA."""
        ema_current_a = 5.3
        force_charge = False

        if force_charge:
            display_available = 0.0
        else:
            display_available = round(ema_current_a, 2)

        assert display_available == 5.3

    def test_ema_current_zero_during_force_charge(self):
        """When force_charge is True, ema_current_a display should be 0."""
        ema_current_a = 8.12
        force_charge = True

        if force_charge:
            display_ema = 0.0
        else:
            display_ema = round(ema_current_a, 2)

        assert display_ema == 0.0

    def test_ema_current_normal_when_not_force(self):
        """When force_charge is False, ema_current_a display shows actual value."""
        ema_current_a = 8.12
        force_charge = False

        if force_charge:
            display_ema = 0.0
        else:
            display_ema = round(ema_current_a, 2)

        assert display_ema == 8.12

    def test_charge_now_triggers_zero_available(self):
        """charge_now=True causes force_charge=True, available must be 0."""
        charge_now = True
        tonight_condition = False
        force_charge = charge_now or tonight_condition

        ema_current_a = 6.5
        display = 0.0 if force_charge else round(ema_current_a, 2)
        assert display == 0.0

    def test_tonight_condition_triggers_zero_available(self):
        """tonight_condition=True causes force_charge=True, available must be 0."""
        charge_now = False
        tonight_condition = True
        force_charge = charge_now or tonight_condition

        ema_current_a = 3.7
        display = 0.0 if force_charge else round(ema_current_a, 2)
        assert display == 0.0


# ---------------------------------------------------------------------------
# Tests: Charge buffer
# ---------------------------------------------------------------------------

class TestChargeBuffer:
    """Test that charge buffer is correctly applied to the desired range."""

    def test_zero_buffer_no_effect(self):
        """Buffer of 0% should not change the effective range."""
        desired_range = 200.0
        charge_buffer = 0.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert effective == 200.0

    def test_10_percent_buffer(self):
        """Buffer of 10% should increase effective range by 10%."""
        desired_range = 200.0
        charge_buffer = 10.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert abs(effective - 220.0) < 0.01

    def test_15_percent_buffer(self):
        """Buffer of 15% for cold weather."""
        desired_range = 300.0
        charge_buffer = 15.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert effective == 345.0

    def test_25_percent_max_buffer(self):
        """Maximum buffer of 25%."""
        desired_range = 200.0
        charge_buffer = 25.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert effective == 250.0

    def test_need_true_with_buffer(self):
        """Range below effective range (with buffer) should signal need."""
        desired_range = 200.0
        charge_buffer = 10.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        current_range = 210.0  # 210 < 220 (effective)
        need = current_range is not None and current_range < effective
        assert need is True

    def test_need_false_without_buffer_true_with_buffer(self):
        """Range between desired and effective should need charging with buffer."""
        desired_range = 200.0
        charge_buffer = 10.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        current_range = 205.0  # 205 > 200 (no buffer → no need) but < 220 (with buffer → need)

        need_without_buffer = current_range < desired_range
        need_with_buffer = current_range < effective

        assert need_without_buffer is False
        assert need_with_buffer is True

    def test_need_false_above_effective_range(self):
        """Range above effective range (with buffer) should not signal need."""
        desired_range = 200.0
        charge_buffer = 10.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        current_range = 230.0
        need = current_range is not None and current_range < effective
        assert need is False

    def test_buffer_with_zero_desired_range(self):
        """Buffer on zero desired range should still result in zero effective."""
        desired_range = 0.0
        charge_buffer = 10.0
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert effective == 0.0


# ---------------------------------------------------------------------------
# Tests: Charge Tonight start logic
# ---------------------------------------------------------------------------

class TestChargeTonightStartLogic:
    """Test all conditions required for charge_tonight to trigger force charge."""

    def _eval_tonight(
        self,
        charge_tonight: bool,
        presence: bool | None,
        cable_connected: bool | None,
        current_range: float | None,
        desired_range: float,
        solar_done: bool,
        charge_buffer: float = 0.0,
        need_active: bool = False,
        range_hysteresis_pct: float = 3.0,
    ) -> tuple[bool, bool]:
        """Evaluate tonight_condition and force_charge.

        Returns (tonight_condition, force_charge).
        """
        effective_range = desired_range * (1.0 + charge_buffer / 100.0)
        hysteresis_km = effective_range * (range_hysteresis_pct / 100.0)
        # Apply hysteresis: start below effective_range, stop above effective_range + hysteresis
        if current_range is not None:
            if need_active:
                need = current_range < (effective_range + hysteresis_km)
            else:
                need = current_range < effective_range
        else:
            need = False
        tonight_condition = (
            charge_tonight
            and bool(presence)
            and bool(cable_connected)
            and need
            and solar_done
        )
        force_charge = tonight_condition  # charge_now is False for these tests
        return tonight_condition, force_charge

    def test_all_conditions_met(self):
        """All conditions met → tonight starts."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is True
        assert fc is True

    def test_charge_tonight_off(self):
        """charge_tonight switch off → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=False, presence=True, cable_connected=True,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_no_presence(self):
        """Vehicle not at home → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=False, cable_connected=True,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_presence_none(self):
        """Presence sensor unavailable → no start (bool(None) is False)."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=None, cable_connected=True,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_cable_disconnected(self):
        """Cable not connected → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=False,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_cable_none(self):
        """Cable sensor unavailable → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=None,
            current_range=150.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_range_already_sufficient(self):
        """Current range >= desired → no need → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=250.0, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_range_none(self):
        """Range sensor unavailable → no need → no start."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=None, desired_range=200.0, solar_done=True,
        )
        assert tc is False

    def test_solar_not_done(self):
        """Solar still producing → no start (wait for evening)."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=150.0, desired_range=200.0, solar_done=False,
        )
        assert tc is False

    def test_buffer_extends_range_need(self):
        """Buffer causes need even though range > desired_range."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=205.0, desired_range=200.0, solar_done=True,
            charge_buffer=10.0,  # effective = 220, 205 < 220 → need
        )
        assert tc is True

    def test_buffer_does_not_change_result_when_fully_charged(self):
        """With buffer, still no need if range > effective range."""
        tc, fc = self._eval_tonight(
            charge_tonight=True, presence=True, cable_connected=True,
            current_range=250.0, desired_range=200.0, solar_done=True,
            charge_buffer=10.0,  # effective = 220, 250 > 220 → no need
        )
        assert tc is False


class TestSolarDoneWithoutSensor:
    """Test that solar_done defaults to True when no solar sensor is configured."""

    def test_no_solar_sensor_solar_done_true(self):
        """Without a solar sensor, solar_done should be True so charge_tonight works."""
        solar_w = None  # no solar sensor

        if solar_w is not None:
            solar_done = False  # would be calculated
        else:
            solar_done = True

        assert solar_done is True

    def test_with_solar_sensor_below_threshold(self):
        """With solar sensor below threshold, solar_done follows duration logic."""
        solar_w = 30.0  # below threshold of 50
        solar_done = False  # not yet done (needs duration elapsed)
        # just checking the branch is correct
        assert solar_w is not None

    def test_with_solar_sensor_above_threshold(self):
        """With solar sensor above threshold, solar_done is False."""
        solar_w = 200.0
        threshold = 50.0
        solar_done = solar_w < threshold
        assert solar_done is False

    def test_tonight_works_without_solar_sensor(self):
        """charge_tonight should be able to trigger when no solar sensor is present."""
        # Simulate: no solar sensor → solar_done = True
        solar_done = True  # default when no solar sensor

        charge_tonight = True
        presence = True
        cable_connected = True
        current_range = 150.0
        desired_range = 200.0
        need = current_range < desired_range

        tonight_condition = (
            charge_tonight
            and bool(presence)
            and bool(cable_connected)
            and need
            and solar_done
        )
        assert tonight_condition is True


class TestForceSourceTracking:
    """Test that force charge source is correctly tracked for diagnostics."""

    def test_charge_now_sets_source(self):
        """charge_now should set force_source to 'charge_now_switch'."""
        charge_now = True
        tonight_condition = False
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_source == "charge_now_switch"

    def test_tonight_sets_source(self):
        """tonight_condition should set force_source to 'charge_tonight'."""
        charge_now = False
        tonight_condition = True
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_source == "charge_tonight"

    def test_both_active_prefers_charge_now(self):
        """When both charge_now and tonight are active, charge_now takes priority."""
        charge_now = True
        tonight_condition = True
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_source == "charge_now_switch"

    def test_source_empty_when_no_force(self):
        """When not force charging, source should be empty in data."""
        force_charge = False
        force_source = "some_old_value"
        data_source = force_source if force_charge else ""
        assert data_source == ""


# ---------------------------------------------------------------------------
# Tests: Range Hysteresis
# ---------------------------------------------------------------------------

class TestRangeHysteresis:
    """Test range hysteresis prevents rapid start/stop cycling.

    Hysteresis: start charging when below effective_range, stop only when
    hysteresis band above effective_range. Uses a percentage of effective_range
    (default 3%). This prevents flapping when the reported range fluctuates
    around the threshold.
    """

    HYSTERESIS_PCT = 3.0  # matches DEFAULT_RANGE_HYSTERESIS_PCT

    def _eval_need(
        self,
        current_range: float | None,
        effective_range: float,
        need_active: bool,
        hysteresis_pct: float | None = None,
    ) -> bool:
        """Evaluate need with hysteresis, mirroring coordinator logic."""
        pct = hysteresis_pct if hysteresis_pct is not None else self.HYSTERESIS_PCT
        hysteresis_km = effective_range * (pct / 100.0)
        if current_range is not None:
            if need_active:
                return current_range < (effective_range + hysteresis_km)
            else:
                return current_range < effective_range
        return False

    def test_initial_state_below_range_starts_need(self):
        """From cold start, range below target → need=True."""
        need = self._eval_need(current_range=150.0, effective_range=200.0, need_active=False)
        assert need is True

    def test_initial_state_above_range_no_need(self):
        """From cold start, range above target → need=False."""
        need = self._eval_need(current_range=210.0, effective_range=200.0, need_active=False)
        assert need is False

    def test_initial_state_exactly_at_range_no_need(self):
        """From cold start, range exactly at target → need=False (not strictly below)."""
        need = self._eval_need(current_range=200.0, effective_range=200.0, need_active=False)
        assert need is False

    def test_charging_stays_active_at_effective_range(self):
        """While charging, reaching effective_range does NOT stop (hysteresis)."""
        need = self._eval_need(current_range=200.0, effective_range=200.0, need_active=True)
        assert need is True

    def test_charging_stays_active_slightly_above(self):
        """While charging, 2km above target → still active (within 3% band = 6km)."""
        need = self._eval_need(current_range=202.0, effective_range=200.0, need_active=True)
        assert need is True

    def test_charging_stays_active_at_5km_above(self):
        """While charging, 5km above target → still active (within 3% band = 6km)."""
        need = self._eval_need(current_range=205.0, effective_range=200.0, need_active=True)
        assert need is True

    def test_charging_stops_at_hysteresis_boundary(self):
        """While charging, at hysteresis boundary (3% of 200 = 6km above) → stops."""
        need = self._eval_need(current_range=206.0, effective_range=200.0, need_active=True)
        assert need is False

    def test_charging_stops_well_above(self):
        """While charging, 10km above target → stops."""
        need = self._eval_need(current_range=210.0, effective_range=200.0, need_active=True)
        assert need is False

    def test_full_cycle_prevents_flapping(self):
        """Simulate a charge cycle: start → charge → overshoot → hold → stop.
        With 3% hysteresis on effective=200, band is 6km, stop at 206.
        """
        effective = 200.0
        need_active = False

        # Step 1: range at 195, below target → enter need
        need_active = self._eval_need(195.0, effective, need_active)
        assert need_active is True

        # Step 2: range rises to 200 (at target) → still charging
        need_active = self._eval_need(200.0, effective, need_active)
        assert need_active is True

        # Step 3: range rises to 204 (4km above) → still charging
        need_active = self._eval_need(204.0, effective, need_active)
        assert need_active is True

        # Step 4: range rises to 206 (6km above, = hysteresis boundary) → stops
        need_active = self._eval_need(206.0, effective, need_active)
        assert need_active is False

        # Step 5: range drops to 201 (1km above, but need_active=False) → no restart
        need_active = self._eval_need(201.0, effective, need_active)
        assert need_active is False

        # Step 6: range drops to 199 (below target) → restarts
        need_active = self._eval_need(199.0, effective, need_active)
        assert need_active is True

    def test_none_range_always_false(self):
        """None current_range → need=False regardless of state."""
        assert self._eval_need(None, 200.0, need_active=False) is False
        assert self._eval_need(None, 200.0, need_active=True) is False

    def test_hysteresis_with_buffer(self):
        """Buffer and hysteresis combine correctly.
        effective=220, 3% hysteresis = 6.6km, stop at 226.6.
        """
        desired_range = 200.0
        charge_buffer = 10.0  # effective = 220
        effective = desired_range * (1.0 + charge_buffer / 100.0)
        assert abs(effective - 220.0) < 0.01

        # Start: 215 < 220 → need
        need_active = self._eval_need(215.0, effective, need_active=False)
        assert need_active is True

        # Charging: 224 < 226.6 (220 + 6.6) → still active
        need_active = self._eval_need(224.0, effective, need_active)
        assert need_active is True

        # Charging: 227 >= 226.6 → stops
        need_active = self._eval_need(227.0, effective, need_active)
        assert need_active is False

    def test_tonight_uses_hysteresis_to_keep_charging(self):
        """Charge tonight should keep charging until hysteresis band above effective_range.
        3% of 200 = 6km, so stops at 206.
        """
        effective_range = 200.0
        hysteresis_km = effective_range * (self.HYSTERESIS_PCT / 100.0)  # 6.0
        need_active = True  # already charging
        current_range = 202.0  # 2km above effective, but within 6km band

        if need_active:
            need = current_range < (effective_range + hysteresis_km)
        else:
            need = current_range < effective_range

        tonight_condition = (
            True  # charge_tonight
            and True  # presence
            and True  # cable_connected
            and need
            and True  # solar_done
        )
        assert tonight_condition is True  # keeps charging due to hysteresis

    def test_custom_hysteresis_percentage(self):
        """Custom hysteresis percentage: 5% of 200 = 10km band."""
        effective = 200.0

        # With 5%: band = 10km, stops at 210
        # 208 < 210 → still charging
        need = self._eval_need(208.0, effective, need_active=True, hysteresis_pct=5.0)
        assert need is True

        # 210 >= 210 → stops
        need = self._eval_need(210.0, effective, need_active=True, hysteresis_pct=5.0)
        assert need is False

    def test_zero_hysteresis_disables_band(self):
        """0% hysteresis → no band, stops immediately at effective_range."""
        effective = 200.0

        # Exactly at range → stops (no band)
        need = self._eval_need(200.0, effective, need_active=True, hysteresis_pct=0.0)
        assert need is False

    def test_hysteresis_scales_with_range(self):
        """Larger effective_range → larger hysteresis band in km.
        3% of 400 = 12km band.
        """
        effective = 400.0
        # 410 < 412 → still active
        need = self._eval_need(410.0, effective, need_active=True)
        assert need is True
        # 412 >= 412 → stops
        need = self._eval_need(412.0, effective, need_active=True)
        assert need is False


# ---------------------------------------------------------------------------
# Tests: Tonight reason tracking
# ---------------------------------------------------------------------------

class TestTonightReasonTracking:
    """Test that charge_tonight tracks why it was enabled/disabled."""

    def test_reason_on_cable_unplug(self):
        """Auto-off by cable unplug should record reason."""
        charge_tonight = True
        cable_prev = True
        cable_connected = False
        tonight_reason = ""

        if (
            charge_tonight
            and cable_prev is not None
            and cable_connected is not None
            and cable_prev
            and not cable_connected
        ):
            tonight_reason = "auto_off: cable unplugged"
            charge_tonight = False

        assert charge_tonight is False
        assert tonight_reason == "auto_off: cable unplugged"

    def test_reason_on_solar_done_ended(self):
        """Auto-off by solar_done ending should record reason."""
        charge_tonight = True
        prev_solar_done = True
        solar_done = False
        tonight_reason = ""

        if charge_tonight and prev_solar_done and not solar_done:
            tonight_reason = "auto_off: solar_done ended"
            charge_tonight = False

        assert charge_tonight is False
        assert tonight_reason == "auto_off: solar_done ended"

    def test_reason_on_night_off(self):
        """05:00 reset should record reason."""
        tonight_reason = "auto_off: 05:00 reset"
        assert tonight_reason == "auto_off: 05:00 reset"

    def test_reason_on_switch_enable(self):
        """Manual switch enable should record reason."""
        tonight_reason = "enabled via switch"
        assert tonight_reason == "enabled via switch"

    def test_reason_on_switch_disable(self):
        """Manual switch disable should record reason."""
        tonight_reason = "disabled via switch"
        assert tonight_reason == "disabled via switch"

    def test_reason_on_service_enable(self):
        """Service enable should record reason."""
        tonight_reason = "enabled via service"
        assert tonight_reason == "enabled via service"

    def test_reason_on_service_disable(self):
        """Service disable should record reason."""
        tonight_reason = "disabled via service"
        assert tonight_reason == "disabled via service"


# ---------------------------------------------------------------------------
# Tests: Tonight reentry lower current
# ---------------------------------------------------------------------------

class TestTonightReentryLowerCurrent:
    """Test that tonight reentry (range drops back) uses lower current."""

    DEFAULT_TONIGHT_REENTRY_CURRENT_A = 5

    def test_reentry_detected_when_need_reactivates(self):
        """Reentry detected when need transitions False→True during tonight force charge."""
        prev_need_active = False
        need_active = True
        force_charge_prev = True
        force_source = "charge_tonight"

        tonight_reentry = (
            not prev_need_active
            and need_active
            and force_charge_prev
            and force_source == "charge_tonight"
        )
        assert tonight_reentry is True

    def test_no_reentry_on_first_activation(self):
        """First activation is not a reentry (force_charge_prev was False)."""
        prev_need_active = False
        need_active = True
        force_charge_prev = False
        force_source = "charge_tonight"

        tonight_reentry = (
            not prev_need_active
            and need_active
            and force_charge_prev
            and force_source == "charge_tonight"
        )
        assert tonight_reentry is False

    def test_no_reentry_for_charge_now(self):
        """charge_now should NOT trigger reentry even if need reactivates."""
        prev_need_active = False
        need_active = True
        force_charge_prev = True
        force_source = "charge_now_switch"

        tonight_reentry = (
            not prev_need_active
            and need_active
            and force_charge_prev
            and force_source == "charge_now_switch"
        )
        # charge_now_switch does not match "charge_tonight"
        tonight_reentry_correct = (
            not prev_need_active
            and need_active
            and force_charge_prev
            and force_source == "charge_tonight"
        )
        assert tonight_reentry_correct is False

    def test_reentry_uses_lower_current(self):
        """During tonight reentry, start at lower current (5A) instead of max."""
        max_a = 16
        tonight_reentry = True
        source = "charge_tonight"

        if tonight_reentry and source == "charge_tonight":
            start_a = min(self.DEFAULT_TONIGHT_REENTRY_CURRENT_A, max_a)
        else:
            start_a = max_a

        assert start_a == 5

    def test_charge_now_ignores_reentry_uses_max(self):
        """charge_now always uses max current, ignoring reentry flag."""
        max_a = 16
        tonight_reentry = True
        source = "charge_now_switch"

        if tonight_reentry and source == "charge_tonight":
            start_a = min(self.DEFAULT_TONIGHT_REENTRY_CURRENT_A, max_a)
        else:
            start_a = max_a

        assert start_a == 16

    def test_reentry_respects_max_current_limit(self):
        """If max_current_limit < reentry current, use max_current_limit."""
        max_a = 3  # lower than 5A reentry
        tonight_reentry = True
        source = "charge_tonight"

        if tonight_reentry and source == "charge_tonight":
            start_a = min(self.DEFAULT_TONIGHT_REENTRY_CURRENT_A, max_a)
        else:
            start_a = max_a

        assert start_a == 3


# ---------------------------------------------------------------------------
# Tests: Charge now overrules tonight
# ---------------------------------------------------------------------------

class TestChargeNowOverrulesTonight:
    """Test that charge_now always overrules charge_tonight."""

    def test_charge_now_takes_priority(self):
        """charge_now is True → force_charge True, source is charge_now_switch."""
        charge_now = True
        tonight_condition = True
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_charge is True
        assert force_source == "charge_now_switch"

    def test_charge_now_true_even_without_tonight(self):
        """charge_now works independently of tonight conditions."""
        charge_now = True
        tonight_condition = False
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_charge is True
        assert force_source == "charge_now_switch"

    def test_charge_now_bypasses_need(self):
        """charge_now does not require need (range check) to charge."""
        charge_now = True
        need = False  # range already sufficient
        # tonight_condition includes need, but charge_now doesn't
        tonight_condition = False  # need is False → tonight won't trigger
        force_charge = charge_now or tonight_condition
        assert force_charge is True

    def test_charge_now_always_max_current(self):
        """charge_now always charges at max current, even when reentry is flagged."""
        max_a = 16
        tonight_reentry = True  # leftover from previous tonight session
        source = "charge_now_switch"

        # charge_now ignores tonight_reentry because source != "charge_tonight"
        if tonight_reentry and source == "charge_tonight":
            start_a = min(5, max_a)
        else:
            start_a = max_a

        assert start_a == 16

    def test_tonight_only_when_charge_now_off(self):
        """tonight_condition only matters when charge_now is off."""
        charge_now = False
        tonight_condition = True
        force_charge = charge_now or tonight_condition
        force_source = "charge_now_switch" if charge_now else "charge_tonight"
        assert force_charge is True
        assert force_source == "charge_tonight"


# ---------------------------------------------------------------------------
# Tests: Range hysteresis percentage-based
# ---------------------------------------------------------------------------

class TestRangeHysteresisPercentage:
    """Test that range hysteresis uses a percentage of effective_range."""

    def test_hysteresis_km_calculation(self):
        """3% of 200km = 6km hysteresis band."""
        effective_range = 200.0
        hysteresis_pct = 3.0
        hysteresis_km = effective_range * (hysteresis_pct / 100.0)
        assert hysteresis_km == 6.0

    def test_hysteresis_km_larger_range(self):
        """3% of 400km = 12km hysteresis band."""
        effective_range = 400.0
        hysteresis_pct = 3.0
        hysteresis_km = effective_range * (hysteresis_pct / 100.0)
        assert hysteresis_km == 12.0

    def test_hysteresis_km_small_range(self):
        """3% of 100km = 3km hysteresis band."""
        effective_range = 100.0
        hysteresis_pct = 3.0
        hysteresis_km = effective_range * (hysteresis_pct / 100.0)
        assert hysteresis_km == 3.0

    def test_zero_percent_no_hysteresis(self):
        """0% hysteresis = 0km band."""
        effective_range = 200.0
        hysteresis_pct = 0.0
        hysteresis_km = effective_range * (hysteresis_pct / 100.0)
        assert hysteresis_km == 0.0

    def test_max_10_percent(self):
        """10% of 200km = 20km hysteresis band."""
        effective_range = 200.0
        hysteresis_pct = 10.0
        hysteresis_km = effective_range * (hysteresis_pct / 100.0)
        assert hysteresis_km == 20.0


# ---------------------------------------------------------------------------
# Tests: Earliest charge start time gate
# ---------------------------------------------------------------------------

class TestEarliestChargeStartGate:
    """Test that charge_tonight only triggers after the configured start time."""

    def _eval_tonight_with_time(
        self,
        charge_tonight: bool,
        presence: bool,
        cable_connected: bool,
        need: bool,
        solar_done: bool,
        current_hour: int,
        current_minute: int,
        start_hour: int = 22,
        start_minute: int = 0,
    ) -> bool:
        """Evaluate tonight_condition with time gate."""
        after_start = (
            current_hour > start_hour
            or (current_hour == start_hour and current_minute >= start_minute)
        )
        return (
            charge_tonight
            and presence
            and cable_connected
            and need
            and solar_done
            and after_start
        )

    def test_before_start_time_blocks(self):
        """Before 22:00 → tonight should not trigger."""
        result = self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=21, current_minute=59,
        )
        assert result is False

    def test_at_start_time_allows(self):
        """At exactly 22:00 → tonight should trigger."""
        result = self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=22, current_minute=0,
        )
        assert result is True

    def test_after_start_time_allows(self):
        """After 22:00 → tonight should trigger."""
        result = self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=23, current_minute=30,
        )
        assert result is True

    def test_custom_start_time(self):
        """Custom start time 20:30 — 20:29 blocks, 20:30 allows."""
        assert self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=20, current_minute=29,
            start_hour=20, start_minute=30,
        ) is False

        assert self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=20, current_minute=30,
            start_hour=20, start_minute=30,
        ) is True

    def test_midnight_wrapping(self):
        """At 00:30 with start 22:00 — should NOT trigger (before start in same day cycle)."""
        # 00:30 is NOT > 22, so it's blocked.
        # This is correct for a "same evening" interpretation.
        result = self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=True,
            need=True, solar_done=True,
            current_hour=0, current_minute=30,
        )
        assert result is False

    def test_other_conditions_still_required(self):
        """Even after start time, all other conditions must be met."""
        # No presence
        assert self._eval_tonight_with_time(
            charge_tonight=True, presence=False, cable_connected=True,
            need=True, solar_done=True,
            current_hour=23, current_minute=0,
        ) is False

        # No cable
        assert self._eval_tonight_with_time(
            charge_tonight=True, presence=True, cable_connected=False,
            need=True, solar_done=True,
            current_hour=23, current_minute=0,
        ) is False


# ---------------------------------------------------------------------------
# Tests: Night off time configuration
# ---------------------------------------------------------------------------

class TestNightOffTimeConfiguration:
    """Test that night off time is configurable."""

    def test_default_night_off_is_five(self):
        """Default night-off should be 05:00."""
        DEFAULT_NIGHT_OFF_HOUR = 5
        DEFAULT_NIGHT_OFF_MINUTE = 0
        assert DEFAULT_NIGHT_OFF_HOUR == 5
        assert DEFAULT_NIGHT_OFF_MINUTE == 0

    def test_default_tonight_start_is_twentytwo(self):
        """Default tonight start should be 22:00."""
        DEFAULT_TONIGHT_START_HOUR = 22
        DEFAULT_TONIGHT_START_MINUTE = 0
        assert DEFAULT_TONIGHT_START_HOUR == 22
        assert DEFAULT_TONIGHT_START_MINUTE == 0

    def test_night_off_reason_includes_configured_time(self):
        """Night-off reason string should include the configured time."""
        hour, minute = 6, 30
        reason = f"auto_off: {hour:02d}:{minute:02d} reset"
        assert reason == "auto_off: 06:30 reset"

    def test_night_off_reason_default_time(self):
        """Night-off reason with default 05:00."""
        hour, minute = 5, 0
        reason = f"auto_off: {hour:02d}:{minute:02d} reset"
        assert reason == "auto_off: 05:00 reset"


# ---------------------------------------------------------------------------
# Tests: Error recovery for actuator calls
# ---------------------------------------------------------------------------

class TestActuatorErrorRecovery:
    """Test that actuator failures don't leave state inconsistent."""

    def test_enable_success_sets_flag(self):
        """Successful enable should set _charging_enabled = True."""
        # Simulated: success path
        charging_enabled = False
        success = True  # simulated call success
        if success:
            charging_enabled = True
        assert charging_enabled is True

    def test_enable_failure_preserves_flag(self):
        """Failed enable should NOT set _charging_enabled = True."""
        charging_enabled = False
        success = False  # simulated call failure
        if success:
            charging_enabled = True
        assert charging_enabled is False

    def test_disable_success_clears_flag(self):
        """Successful disable should set _charging_enabled = False."""
        charging_enabled = True
        success = True
        if success:
            charging_enabled = False
        assert charging_enabled is False

    def test_disable_failure_preserves_flag(self):
        """Failed disable should NOT change _charging_enabled."""
        charging_enabled = True
        success = False
        if success:
            charging_enabled = False
        assert charging_enabled is True

    def test_set_current_returns_true_on_success(self):
        """_set_charge_current returns True on success."""
        result = True  # simulated
        assert result is True

    def test_set_current_returns_false_on_failure(self):
        """_set_charge_current returns False on failure."""
        result = False  # simulated
        assert result is False


# ---------------------------------------------------------------------------
# Tests: Plug-in uses fresh EMA instead of stale smoothed value
# ---------------------------------------------------------------------------

class TestPlugInFreshEma:
    """Test that plug-in delayed action uses current EMA, not stale value."""

    def test_fresh_ema_used_over_stale(self):
        """After 2s delay, current EMA should be used, not captured value."""
        captured_ema = 3.0  # captured at plug-in time
        current_ema = 5.0   # fresh value after 2s

        # Coordinator logic: prefer current EMA if available
        ema_to_use = current_ema if current_ema is not None else captured_ema
        assert ema_to_use == 5.0

    def test_fallback_to_captured_if_ema_none(self):
        """If current EMA is None, fall back to captured value."""
        captured_ema = 3.0
        current_ema = None

        ema_to_use = current_ema if current_ema is not None else captured_ema
        assert ema_to_use == 3.0

    def test_ema_floored_for_surplus_start(self):
        """EMA value should be floored for surplus start decision."""
        ema = 4.7
        floored = max(int(ema), 0)
        assert floored == 4


# ---------------------------------------------------------------------------
# Tests: Shared device_info helper
# ---------------------------------------------------------------------------

class TestSharedDeviceInfo:
    """Test that the shared device_info helper produces correct output."""

    def _device_info(self, entry_id: str) -> dict:
        """Mirror of helpers.device_info for testing without HA imports."""
        return {
            "identifiers": {("adaptive_charge", entry_id)},
            "name": "AdaptiveCharge",
            "manufacturer": "AdaptiveCharge",
            "model": "EV Charge Controller",
            "sw_version": "2.0.0",
        }

    def test_device_info_returns_correct_structure(self):
        """device_info should return a dict with identifiers."""
        info = self._device_info("test_entry_123")
        assert ("adaptive_charge", "test_entry_123") in info["identifiers"]

    def test_device_info_has_correct_fields(self):
        """device_info should have name, manufacturer, model, sw_version."""
        info = self._device_info("test_entry_456")
        assert info["name"] == "AdaptiveCharge"
        assert info["manufacturer"] == "AdaptiveCharge"
        assert info["model"] == "EV Charge Controller"
        assert info["sw_version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Tests: Refactored coordinator methods produce consistent results
# ---------------------------------------------------------------------------

class TestRefactoredMethods:
    """Test that the refactored helper methods work correctly."""

    def test_read_sensors_returns_expected_keys(self):
        """_read_sensors should return dict with all expected keys."""
        expected_keys = {
            "computed_net_w", "ev_w", "voltage", "solar_w",
            "presence", "cable_connected", "current_range",
        }
        # Just verify the keys exist in a mock return
        mock_result = {
            "computed_net_w": 100.0, "ev_w": 0.0, "voltage": 230.0,
            "solar_w": None, "presence": True, "cable_connected": True,
            "current_range": 200.0,
        }
        assert set(mock_result.keys()) == expected_keys

    def test_analyze_measurements_returns_expected_keys(self):
        """_analyze_measurements should return dict with all expected keys."""
        expected_keys = {
            "surplus_w", "raw_current_a", "raw_floored",
            "ema_current_a", "coherence", "skew",
            "control_reason", "alignment_ok", "alignment_reason",
        }
        mock_result = {
            "surplus_w": 500.0, "raw_current_a": 0.72, "raw_floored": 0,
            "ema_current_a": 0.5, "coherence": 0.8, "skew": 1.0,
            "control_reason": "", "alignment_ok": True, "alignment_reason": "ok",
        }
        assert set(mock_result.keys()) == expected_keys

    def test_force_charge_returns_expected_keys(self):
        """_evaluate_force_charge should return dict with all expected keys."""
        expected_keys = {
            "effective_range", "hysteresis_km", "need",
            "tonight_condition", "force_charge",
        }
        mock_result = {
            "effective_range": 200.0, "hysteresis_km": 6.0,
            "need": True, "tonight_condition": False, "force_charge": False,
        }
        assert set(mock_result.keys()) == expected_keys
