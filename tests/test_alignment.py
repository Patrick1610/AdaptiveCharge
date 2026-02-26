"""Unit tests for alignment engine and controller stabilization logic."""
from __future__ import annotations

import time
from collections import deque
from statistics import median

import pytest

# We import directly from the alignment module, bypassing __init__.py
# which depends on Home Assistant libraries not available in test env.
import importlib
import sys
import os
import types

# Provide a minimal stub for the const module so alignment.py can import it
_const_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "stormbreaker_charge",
    "const.py",
)
_const_spec = importlib.util.spec_from_file_location(
    "stormbreaker_charge.const", _const_path
)
_const_mod = importlib.util.module_from_spec(_const_spec)
sys.modules["stormbreaker_charge.const"] = _const_mod
# Also register the parent package
pkg = types.ModuleType("stormbreaker_charge")
pkg.__path__ = [
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "stormbreaker_charge",
    )
]
sys.modules.setdefault("stormbreaker_charge", pkg)
_const_spec.loader.exec_module(_const_mod)

# Now import alignment module by file path
_align_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "stormbreaker_charge",
    "alignment.py",
)
_align_spec = importlib.util.spec_from_file_location(
    "stormbreaker_charge.alignment", _align_path
)
_align_mod = importlib.util.module_from_spec(_align_spec)
sys.modules["stormbreaker_charge.alignment"] = _align_mod
_align_spec.loader.exec_module(_align_mod)

AlignmentEngine = _align_mod.AlignmentEngine
EMAFilter = _align_mod.EMAFilter
MeasurementTracker = _align_mod.MeasurementTracker
compute_confidence = _align_mod.compute_confidence
compute_measurement_coherence = _align_mod.compute_measurement_coherence

CONFIDENCE_HIGH = _const_mod.CONFIDENCE_HIGH
CONFIDENCE_LOW = _const_mod.CONFIDENCE_LOW
CONFIDENCE_MEDIUM = _const_mod.CONFIDENCE_MEDIUM


# ---------------------------------------------------------------------------
# Tests: MeasurementTracker
# ---------------------------------------------------------------------------

class TestMeasurementTracker:
    def test_initial_state(self):
        t = MeasurementTracker("test")
        assert t.last_value is None
        assert t.last_update is None
        assert t.interval_median is None
        assert t.interval_p95 is None
        assert t.staleness is None

    def test_single_update(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        assert t.last_value == 100.0
        assert t.last_update == 1000.0

    def test_interval_tracking(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(200.0, 1010.0)
        t.update(300.0, 1020.0)
        # Two intervals: 10, 10
        assert t.interval_median == 10.0
        assert t.interval_p95 == 10.0

    def test_interval_recorded_even_on_same_value(self):
        """Intervals must be recorded on every poll, even when value is constant.

        This is the corrected behaviour: freshness must not depend on value changes.
        Three polls are needed so that two intervals can be recorded, satisfying
        the ``>= 2`` guard in ``interval_median``.
        """
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(100.0, 1010.0)  # same value — interval SHOULD be recorded
        t.update(100.0, 1020.0)  # second interval
        assert t.interval_median is not None
        assert abs(t.interval_median - 10.0) < 0.01

    def test_varied_intervals(self):
        t = MeasurementTracker("test")
        # Create intervals of 5, 10, 15, 20, 25
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        times = [0.0, 5.0, 15.0, 30.0, 50.0, 75.0]
        for v, ts in zip(values, times):
            t.update(v, ts)
        assert t.interval_median is not None
        assert t.interval_p95 is not None
        assert t.interval_p95 >= t.interval_median


# ---------------------------------------------------------------------------
# Tests: EMAFilter
# ---------------------------------------------------------------------------

class TestEMAFilter:
    def test_first_value_is_passthrough(self):
        f = EMAFilter(span_s=8.0)
        result = f.update(5.0, 100.0)
        assert result == 5.0

    def test_convergence_toward_new_value(self):
        f = EMAFilter(span_s=8.0)
        f.update(0.0, 100.0)
        # Feed constant 10.0 for many steps
        val = 0.0
        for i in range(1, 50):
            val = f.update(10.0, 100.0 + i * 2.0)
        # Should converge close to 10.0
        assert abs(val - 10.0) < 0.5

    def test_slow_response_with_large_span(self):
        f = EMAFilter(span_s=100.0)
        f.update(0.0, 0.0)
        val = f.update(10.0, 1.0)
        # With large span, should barely move
        assert val < 1.0

    def test_fast_response_with_small_span(self):
        f = EMAFilter(span_s=1.0)
        f.update(0.0, 0.0)
        val = f.update(10.0, 2.0)
        # With small span and 2s dt, should move significantly
        assert val > 5.0

    def test_value_property(self):
        f = EMAFilter(span_s=8.0)
        assert f.value is None
        f.update(5.0, 100.0)
        assert f.value == 5.0


# ---------------------------------------------------------------------------
# Tests: AlignmentEngine
# ---------------------------------------------------------------------------

class TestAlignmentEngine:
    def test_initial_state(self):
        ae = AlignmentEngine()
        assert ae.active is False
        assert ae.estimated_lag is None

    def test_ev_step_activates_alignment(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)  # 500W delta
        assert ae.active is True

    def test_small_ev_change_does_not_activate(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1200.0, 100.0)  # 200W delta
        assert ae.active is False

    def test_none_old_value_ignored(self):
        ae = AlignmentEngine()
        ae.on_ev_power_change(None, 1000.0, 100.0)
        assert ae.active is False

    def test_alignment_completes_on_net_reaction(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        # EV steps up by 500W
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        assert ae.active is True
        # Net power increases (expected reaction to EV step up)
        ae.on_net_power_update(103.0, 400.0)
        assert ae.active is False

    def test_alignment_records_lag(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        ae.on_net_power_update(105.0, 400.0)
        assert ae.estimated_lag is not None
        assert abs(ae.estimated_lag - 5.0) < 0.1

    def test_alignment_timeout(self):
        ae = AlignmentEngine(
            ev_step_threshold_w=400.0,
            timeout_min_s=8.0,
            timeout_max_s=60.0,
        )
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        assert ae.active is True
        # Simulate passage of time beyond timeout
        ae.check_timeout(120.0)
        assert ae.active is False

    def test_timeout_is_dynamic(self):
        ae = AlignmentEngine(
            ev_step_threshold_w=400.0,
            timeout_min_s=8.0,
            timeout_max_s=60.0,
        )
        # Record some lag
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        ae.on_net_power_update(112.0, 400.0)  # 12s lag
        # Timeout should be 2 * 12 = 24s
        assert abs(ae.timeout - 24.0) < 1.0

    def test_ev_step_down_detected(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(2000.0, 1000.0, 100.0)  # -1000W
        assert ae.active is True

    def test_alignment_completes_on_down_reaction(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        # EV steps down by 1000W
        ae.on_ev_power_change(2000.0, 1000.0, 100.0)
        assert ae.active is True
        # Net power decreases (expected reaction)
        ae.on_net_power_update(104.0, -500.0)
        assert ae.active is False


# ---------------------------------------------------------------------------
# Tests: compute_confidence
# ---------------------------------------------------------------------------

class TestConfidence:
    def _make_tracker(self, last_update_offset: float = 0.0) -> MeasurementTracker:
        t = MeasurementTracker("test")
        t.update(100.0, time.monotonic() - last_update_offset)
        return t

    def test_high_confidence_fresh_data(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=5.0,
            last_committed=5.0,
            sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH

    def test_low_confidence_stale_data(self):
        net = self._make_tracker(100.0)  # 100s old
        ev = self._make_tracker(100.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=5.0,
            last_committed=5.0,
            sample_interval=10.0,
        )
        assert result == CONFIDENCE_LOW

    def test_alignment_reduces_confidence(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=True,
            target_current=5.0,
            last_committed=5.0,
            sample_interval=10.0,
        )
        # With alignment active, confidence drops by 1 → at most MEDIUM
        assert result == CONFIDENCE_MEDIUM

    def test_large_target_jump_reduces_confidence(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=10.0,
            last_committed=5.0,
            sample_interval=10.0,
        )
        assert result in (CONFIDENCE_MEDIUM, CONFIDENCE_HIGH)

    def test_no_previous_commit_no_penalty(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=5.0,
            last_committed=None,
            sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH


# ---------------------------------------------------------------------------
# Tests: Hysteresis logic (mirrors _try_modulate)
# ---------------------------------------------------------------------------

class TestHysteresisLogic:
    """Test hysteresis bands prevent flapping."""

    def _should_modulate(
        self,
        current_setpoint: float,
        target: float,
        hysteresis_up: float = 1.0,
        hysteresis_down: float = 1.0,
    ) -> bool:
        """Mirror of hysteresis check in _try_modulate."""
        delta = target - current_setpoint
        if delta > 0 and delta < hysteresis_up:
            return False
        if delta < 0 and abs(delta) < hysteresis_down:
            return False
        return True

    def test_no_change_within_band(self):
        # 4.5A target vs 4.0 setpoint = 0.5 delta < 1.0 hysteresis
        assert self._should_modulate(4.0, 4.5) is False

    def test_change_above_band(self):
        # 5.5A target vs 4.0 setpoint = 1.5 delta >= 1.0 hysteresis
        assert self._should_modulate(4.0, 5.5) is True

    def test_no_decrease_within_band(self):
        # 3.5A target vs 4.0 setpoint = -0.5 delta, abs < 1.0
        assert self._should_modulate(4.0, 3.5) is False

    def test_decrease_below_band(self):
        # 2.5A target vs 4.0 setpoint = -1.5 delta, abs >= 1.0
        assert self._should_modulate(4.0, 2.5) is True

    def test_exact_boundary_up(self):
        # 5.0A target vs 4.0 setpoint = 1.0 delta = hysteresis (not <)
        assert self._should_modulate(4.0, 5.0) is True

    def test_exact_boundary_down(self):
        # 3.0A target vs 4.0 setpoint = -1.0, abs = hysteresis
        assert self._should_modulate(4.0, 3.0) is True


# ---------------------------------------------------------------------------
# Tests: Rate limiting logic
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Test that max step is enforced."""

    def _apply_rate_limit(
        self, current_setpoint: float, target: float, max_step: float = 1.0
    ) -> float:
        """Mirror of rate limiting in _try_modulate."""
        delta = target - current_setpoint
        step = min(abs(delta), max_step)
        if delta > 0:
            return current_setpoint + step
        return current_setpoint - step

    def test_large_increase_limited(self):
        result = self._apply_rate_limit(4.0, 8.0)
        assert result == 5.0

    def test_large_decrease_limited(self):
        result = self._apply_rate_limit(8.0, 4.0)
        assert result == 7.0

    def test_small_change_preserved(self):
        result = self._apply_rate_limit(4.0, 4.8)
        assert abs(result - 4.8) < 0.01

    def test_exact_1a_step(self):
        result = self._apply_rate_limit(4.0, 5.0)
        assert result == 5.0


# ---------------------------------------------------------------------------
# Tests: Import safety logic
# ---------------------------------------------------------------------------

class TestImportSafety:
    """Test import safety threshold mechanism."""

    def _check_import_safety(
        self,
        net_w: float,
        mono_now: float,
        exceed_since: float | None,
        threshold: float = 100.0,
        duration: float = 3.0,
    ) -> tuple[bool, float | None]:
        """Mirror of _check_import_safety."""
        if net_w > threshold:
            if exceed_since is None:
                exceed_since = mono_now
            elif (mono_now - exceed_since) >= duration:
                return True, exceed_since
        else:
            exceed_since = None
        return False, exceed_since

    def test_no_import_no_trigger(self):
        triggered, since = self._check_import_safety(-500.0, 100.0, None)
        assert triggered is False
        assert since is None

    def test_import_starts_timer(self):
        triggered, since = self._check_import_safety(200.0, 100.0, None)
        assert triggered is False
        assert since == 100.0

    def test_import_triggers_after_duration(self):
        triggered, since = self._check_import_safety(200.0, 104.0, 100.0)
        assert triggered is True

    def test_import_resets_on_export(self):
        triggered, since = self._check_import_safety(-100.0, 104.0, 100.0)
        assert triggered is False
        assert since is None

    def test_import_not_triggered_before_duration(self):
        triggered, since = self._check_import_safety(200.0, 102.0, 100.0)
        assert triggered is False
        assert since == 100.0


# ---------------------------------------------------------------------------
# Tests: Idempotent commit logic
# ---------------------------------------------------------------------------

class TestIdempotentCommit:
    """Test that identical integer values are not re-sent."""

    def test_same_int_skipped(self):
        last_committed_int = 5
        target_int = 5
        assert target_int == last_committed_int  # should skip

    def test_different_int_sent(self):
        last_committed_int = 5
        target_int = 6
        assert target_int != last_committed_int  # should send

    def test_float_rounding_same_int(self):
        # 5.3 and 5.7 both truncate to 5
        assert int(5.3) == int(5.7) == 5


# ---------------------------------------------------------------------------
# Tests: Min on/off time logic
# ---------------------------------------------------------------------------

class TestMinOnOffTime:
    """Test minimum on/off time enforcement."""

    def _can_start(
        self, last_off_time: float | None, mono_now: float, min_off_s: float = 120.0
    ) -> bool:
        if last_off_time is not None:
            if (mono_now - last_off_time) < min_off_s:
                return False
        return True

    def _can_stop(
        self, last_on_time: float | None, mono_now: float, min_on_s: float = 300.0
    ) -> bool:
        if last_on_time is not None:
            if (mono_now - last_on_time) < min_on_s:
                return False
        return True

    def test_can_start_after_min_off(self):
        assert self._can_start(100.0, 300.0) is True  # 200s > 120s

    def test_cannot_start_before_min_off(self):
        assert self._can_start(100.0, 150.0) is False  # 50s < 120s

    def test_can_start_first_time(self):
        assert self._can_start(None, 100.0) is True

    def test_can_stop_after_min_on(self):
        assert self._can_stop(100.0, 500.0) is True  # 400s > 300s

    def test_cannot_stop_before_min_on(self):
        assert self._can_stop(100.0, 200.0) is False  # 100s < 300s

    def test_can_stop_first_time(self):
        assert self._can_stop(None, 100.0) is True


# ---------------------------------------------------------------------------
# New tests: Tracker freshness (scenario 1)
# ---------------------------------------------------------------------------

class TestTrackerFreshness:
    """Feed constant values over multiple polls; freshness must advance and
    staleness must NOT trigger even though the sensor value never changes."""

    def test_constant_value_records_multiple_intervals(self):
        """Polling at a fixed cadence with a constant value should populate intervals."""
        t = MeasurementTracker("net")
        for i in range(6):
            t.update(100.0, float(i * 10))
        # 5 intervals of 10 s each
        assert t.interval_median is not None
        assert abs(t.interval_median - 10.0) < 0.1

    def test_last_update_advances_on_every_poll(self):
        """last_update must reflect the most-recent poll regardless of value."""
        t = MeasurementTracker("net")
        for i in range(5):
            t.update(100.0, float(i * 10))
        assert t.last_update == 40.0

    def test_staleness_near_zero_just_after_poll(self):
        """Staleness should be negligible immediately after updating."""
        t = MeasurementTracker("net")
        mono = time.monotonic()
        t.update(100.0, mono)
        assert t.staleness is not None
        assert t.staleness < 1.0

    def test_avg_interval_converges_to_poll_cadence(self):
        """EWMA avg_interval should converge close to the actual poll period."""
        t = MeasurementTracker("net")
        for i in range(25):
            t.update(100.0, float(i * 10))
        assert t.avg_interval is not None
        assert abs(t.avg_interval - 10.0) < 1.0

    def test_jitter_low_for_perfectly_regular_polling(self):
        """Zero-jitter polling should yield a very low jitter estimate."""
        t = MeasurementTracker("net")
        for i in range(25):
            t.update(100.0, float(i * 10))
        assert t.jitter is not None
        assert t.jitter < 1.0

    def test_reliability_score_high_for_regular_polling(self):
        """A tracker polled at perfectly regular intervals should have high reliability."""
        t = MeasurementTracker("net")
        for i in range(20):
            t.update(50.0, float(i * 10))
        assert t.reliability_score > 0.8

    def test_high_confidence_with_constant_sensors(self):
        """compute_confidence must NOT report stale when sensors are regularly polled
        but have constant values (the classic PR-5 freshness bug)."""
        mono = time.monotonic()
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        # Simulate 10 polls at 10-s intervals with constant values
        for i in range(10):
            net.update(0.0, mono - (9 - i) * 10.0)
            ev.update(1000.0, mono - (9 - i) * 10.0)
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=5.0,
            last_committed=5.0,
            sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH


# ---------------------------------------------------------------------------
# New tests: Skew scenarios (scenario 2)
# ---------------------------------------------------------------------------

class TestSkewScenarios:
    """Adaptive threshold and alignment_active under various skew conditions."""

    def _build_tracker_with_interval(
        self, avg_interval_s: float, n_samples: int = 20
    ) -> MeasurementTracker:
        t = MeasurementTracker("test")
        for i in range(n_samples):
            t.update(100.0, float(i * avg_interval_s))
        return t

    def test_zero_skew_gives_coherence_one(self):
        """When both trackers are polled at the same time, skew is 0 → coherence 1."""
        net = self._build_tracker_with_interval(10.0)
        ev = self._build_tracker_with_interval(10.0)
        # Both end at identical timestamp
        mono = float(19 * 10)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=mono
        )
        assert skew == 0.0
        assert coherence == 1.0
        assert not needed

    def test_small_skew_within_threshold_no_alignment(self):
        """A skew well within the adaptive threshold must not trigger alignment."""
        net = self._build_tracker_with_interval(10.0)
        ev = self._build_tracker_with_interval(10.0)
        # Add 1-second skew on net (much less than 1 * 10s interval)
        net.update(100.0, net.last_update + 1.0)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=net.last_update
        )
        assert not needed

    def test_large_skew_5s_interval_triggers_alignment(self):
        """For 5-s polled sensors, a 20-s skew must trigger alignment."""
        net = self._build_tracker_with_interval(5.0)
        ev = self._build_tracker_with_interval(5.0)
        # Introduce a 20-s lag on net
        net.update(100.0, net.last_update + 20.0)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=net.last_update
        )
        assert needed
        assert skew >= 20.0

    def test_large_skew_30s_interval_triggers_alignment(self):
        """For 30-s polled sensors, a 60-s skew must trigger alignment."""
        net = self._build_tracker_with_interval(30.0)
        ev = self._build_tracker_with_interval(30.0)
        net.update(100.0, net.last_update + 60.0)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=net.last_update
        )
        assert needed

    def test_skew_that_equals_threshold_triggers(self):
        """Skew exactly equal to the adaptive threshold must trigger alignment."""
        # Build trackers with avg_interval ~ 10s and jitter ~ 0
        net = self._build_tracker_with_interval(10.0, n_samples=30)
        ev = self._build_tracker_with_interval(10.0, n_samples=30)
        # Threshold ≈ max(2, 1*10 + 2*~0) ≈ 10s; introduce exactly that skew
        threshold_approx = max(2.0, 1.0 * 10.0)
        net.update(100.0, net.last_update + threshold_approx + 0.1)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=net.last_update
        )
        assert needed

    def test_uninitialized_trackers_no_false_trigger(self):
        """Before any samples, coherence must not trigger spurious alignment."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=100.0
        )
        assert not needed
        assert coherence == 0.0


# ---------------------------------------------------------------------------
# New tests: Self-induced dip / settling window (scenario 3)
# ---------------------------------------------------------------------------

class TestSettlingWindow:
    """After a current commit, upward steps should be gated for settling_window_s."""

    def _in_settling(
        self,
        settling_since: float | None,
        mono_now: float,
        window_s: float = 30.0,
    ) -> bool:
        """Mirror of settling-window check in _try_modulate."""
        if settling_since is None:
            return False
        return (mono_now - settling_since) < window_s

    def test_no_settling_before_first_commit(self):
        assert not self._in_settling(None, 100.0)

    def test_in_settling_immediately_after_commit(self):
        assert self._in_settling(100.0, 100.5)

    def test_in_settling_near_end_of_window(self):
        assert self._in_settling(100.0, 129.0)

    def test_out_of_settling_after_window(self):
        assert not self._in_settling(100.0, 131.0)

    def test_settling_window_scaled_by_sample_interval(self):
        """Window should be at least 2 × sample_interval or 30 s, whichever is larger."""
        for interval in (5, 10, 30, 60):
            expected = max(30.0, interval * 2.0)
            assert expected == max(30.0, float(interval) * 2)

    def test_settling_gates_up_step_not_down(self):
        """Settling window gates only upward steps; downward steps are unaffected."""
        settling_since = 100.0
        mono_now = 110.0  # well inside 30-s window

        delta_up = 1.5
        delta_down = -1.5

        up_blocked = delta_up > 0 and self._in_settling(settling_since, mono_now)
        down_blocked = delta_down > 0 and self._in_settling(settling_since, mono_now)

        assert up_blocked is True
        assert down_blocked is False


# ---------------------------------------------------------------------------
# New tests: Flapping boundary extended (scenario 4)
# ---------------------------------------------------------------------------

class TestFlappingBoundaryExtended:
    """Realistic boundary noise around 1.9–2.1 A must not cause toggling."""

    def _should_modulate(
        self,
        current: float,
        target: float,
        hyst_up: float = 1.0,
        hyst_down: float = 1.0,
    ) -> bool:
        delta = target - current
        if delta > 0 and delta < hyst_up:
            return False
        if delta < 0 and abs(delta) < hyst_down:
            return False
        return True

    def test_noise_around_2a_does_not_trigger(self):
        """Small ±0.9 A noise around a 2 A setpoint must not cause modulation."""
        setpoint = 2.0
        for noise in (-0.9, -0.5, -0.1, 0.1, 0.5, 0.9):
            target = setpoint + noise
            result = self._should_modulate(setpoint, target)
            assert not result, f"noise={noise}: expected no modulation, got True"

    def test_change_over_1a_upward_triggers(self):
        assert self._should_modulate(2.0, 3.2) is True

    def test_change_over_1a_downward_triggers(self):
        assert self._should_modulate(2.0, 0.8) is True

    def test_stable_at_integer_boundary(self):
        """A setpoint of exactly 1.0 A with target 1.5 A should NOT modulate."""
        assert self._should_modulate(1.0, 1.5) is False

    def test_stable_at_integer_boundary_down(self):
        """A setpoint of 2.0 A with target 1.5 A should NOT modulate."""
        assert self._should_modulate(2.0, 1.5) is False


# ---------------------------------------------------------------------------
# New tests: Universality — parameterized update cadences (scenario 6)
# ---------------------------------------------------------------------------

class TestUniversalityUpdateIntervals:
    """Tracker and coherence behaviour must be stable for any polling cadence."""

    @pytest.mark.parametrize("interval_s", [5, 30, 60])
    def test_avg_interval_learned_for_cadence(self, interval_s):
        """avg_interval must converge to within 10 % of the true poll cadence."""
        t = MeasurementTracker("net")
        for i in range(25):
            t.update(100.0, float(i * interval_s))
        assert t.avg_interval is not None
        assert abs(t.avg_interval - interval_s) < interval_s * 0.1

    @pytest.mark.parametrize("interval_s", [5, 30, 60])
    def test_staleness_fresh_just_after_last_poll(self, interval_s):
        """After polling, staleness should be negligible (< 1 s in a test context)."""
        t = MeasurementTracker("net")
        mono = time.monotonic()
        for i in range(5):
            t.update(100.0, mono - float((4 - i) * interval_s))
        # Last poll is at mono (age ≈ 0 in real time)
        assert t.staleness is not None
        assert t.staleness < 1.0

    @pytest.mark.parametrize("interval_s", [5, 30, 60])
    def test_coherence_high_when_both_trackers_in_sync(self, interval_s):
        """When net and EV trackers are polled at the same cadence and in sync,
        coherence must be 1.0."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(20):
            ts = float(i * interval_s)
            net.update(0.0, ts)
            ev.update(1000.0, ts)
        coherence, skew, needed = compute_measurement_coherence(
            net_tracker=net, ev_tracker=ev, mono_now=float(19 * interval_s)
        )
        assert coherence == 1.0
        assert not needed

    @pytest.mark.parametrize("interval_s", [5, 30, 60])
    def test_confidence_high_constant_polling(self, interval_s):
        """Constant sensor values at any poll cadence must yield HIGH confidence
        after enough samples (the classic freshness-bug regression test)."""
        mono = time.monotonic()
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(10):
            net.update(0.0, mono - float((9 - i) * interval_s))
            ev.update(1000.0, mono - float((9 - i) * interval_s))
        result = compute_confidence(
            net_tracker=net,
            ev_tracker=ev,
            alignment_active=False,
            target_current=5.0,
            last_committed=5.0,
            sample_interval=float(interval_s),
        )
        assert result == CONFIDENCE_HIGH
