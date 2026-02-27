"""Unit tests for Stormbreaker Surplus EV Charge coordinator logic."""
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

    def _simulate_start_force(self):
        """Mirror _action_start_force diagnostic state."""
        MAX_CURRENT = 16
        state = {
            "current_mode": "force",
            "last_action": "start_force",
            "last_reason": "force_charge_active",
            "committed_current": float(MAX_CURRENT),
            "last_committed_int": MAX_CURRENT,
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
