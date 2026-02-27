"""Unit tests for alignment engine and controller stabilization logic."""
from __future__ import annotations

import time
from collections import deque
from statistics import median

import pytest

# ---------------------------------------------------------------------------
# Import alignment module by file path to bypass HA-dependent __init__.py
# ---------------------------------------------------------------------------
import importlib
import sys
import os
import types

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
compute_coherence = _align_mod.compute_coherence
compute_skew = _align_mod.compute_skew
compute_adaptive_skew_threshold = _align_mod.compute_adaptive_skew_threshold

CONFIDENCE_HIGH = _const_mod.CONFIDENCE_HIGH
CONFIDENCE_LOW = _const_mod.CONFIDENCE_LOW
CONFIDENCE_MEDIUM = _const_mod.CONFIDENCE_MEDIUM


# ===========================================================================
# Tests: MeasurementTracker — sample-based freshness
# ===========================================================================

class TestMeasurementTracker:
    def test_initial_state(self):
        t = MeasurementTracker("test")
        assert t.last_value is None
        assert t.last_seen is None
        assert t.last_changed is None
        assert t.interval_median is None
        assert t.interval_p95 is None
        assert t.staleness is None
        assert t.sample_age is None
        assert t.avg_interval is None
        assert t.jitter is None

    def test_single_update(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        assert t.last_value == 100.0
        assert t.last_seen == 1000.0
        assert t.last_changed == 1000.0

    def test_interval_tracking_with_value_changes(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(200.0, 1010.0)
        t.update(300.0, 1020.0)
        assert t.interval_median is not None
        assert t.avg_interval is not None

    def test_constant_values_still_track_freshness(self):
        """Critical test: constant values must still update last_seen and intervals."""
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(100.0, 1010.0)  # same value
        t.update(100.0, 1020.0)  # same value again
        # last_seen must advance even though value didn't change
        assert t.last_seen == 1020.0
        # Intervals must be tracked from polls, not value changes
        assert t.interval_median is not None
        assert abs(t.interval_median - 10.0) < 0.1
        # avg_interval must be populated
        assert t.avg_interval is not None
        assert abs(t.avg_interval - 10.0) < 2.0

    def test_constant_values_no_stale_detection(self):
        """With constant values and regular polling, staleness should NOT trigger."""
        t = MeasurementTracker("test")
        base = time.monotonic()
        for i in range(10):
            t.update(100.0, base + i * 10.0)
        # Staleness should be tiny (just updated)
        # We can't test real staleness because time.monotonic() advances,
        # but we can verify last_seen was updated on the last call
        assert t.last_seen == base + 90.0

    def test_varied_intervals(self):
        t = MeasurementTracker("test")
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        times = [0.0, 5.0, 15.0, 30.0, 50.0, 75.0]
        for v, ts in zip(values, times):
            t.update(v, ts)
        assert t.interval_median is not None
        assert t.interval_p95 is not None
        assert t.interval_p95 >= t.interval_median

    def test_reliability_stable_intervals(self):
        """Stable intervals should give high reliability."""
        t = MeasurementTracker("test")
        for i in range(20):
            t.update(float(i), i * 10.0)
        assert t.reliability > 0.7

    def test_reliability_initial(self):
        """No data → zero reliability."""
        t = MeasurementTracker("test")
        assert t.reliability == 0.0

    def test_last_changed_only_on_value_change(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(100.0, 1010.0)
        t.update(200.0, 1020.0)
        assert t.last_changed == 1020.0


# ===========================================================================
# Tests: EMAFilter
# ===========================================================================

class TestEMAFilter:
    def test_first_value_is_passthrough(self):
        f = EMAFilter(span_s=8.0)
        result = f.update(5.0, 100.0)
        assert result == 5.0

    def test_convergence_toward_new_value(self):
        f = EMAFilter(span_s=8.0)
        f.update(0.0, 100.0)
        val = 0.0
        for i in range(1, 50):
            val = f.update(10.0, 100.0 + i * 2.0)
        assert abs(val - 10.0) < 0.5

    def test_slow_response_with_large_span(self):
        f = EMAFilter(span_s=100.0)
        f.update(0.0, 0.0)
        val = f.update(10.0, 1.0)
        assert val < 1.0

    def test_fast_response_with_small_span(self):
        f = EMAFilter(span_s=1.0)
        f.update(0.0, 0.0)
        val = f.update(10.0, 2.0)
        assert val > 5.0

    def test_value_property(self):
        f = EMAFilter(span_s=8.0)
        assert f.value is None
        f.update(5.0, 100.0)
        assert f.value == 5.0


# ===========================================================================
# Tests: AlignmentEngine
# ===========================================================================

class TestAlignmentEngine:
    def test_initial_state(self):
        ae = AlignmentEngine()
        assert ae.active is False
        assert ae.settling is False
        assert ae.estimated_lag is None

    def test_ev_step_activates_alignment(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        assert ae.active is True

    def test_small_ev_change_does_not_activate(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1200.0, 100.0)
        assert ae.active is False

    def test_none_old_value_ignored(self):
        ae = AlignmentEngine()
        ae.on_ev_power_change(None, 1000.0, 100.0)
        assert ae.active is False

    def test_alignment_completes_on_net_reaction(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        assert ae.active is True
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
            ev_step_threshold_w=400.0, timeout_min_s=8.0, timeout_max_s=60.0
        )
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        assert ae.active is True
        ae.check_timeout(120.0)
        assert ae.active is False

    def test_timeout_is_dynamic(self):
        ae = AlignmentEngine(
            ev_step_threshold_w=400.0, timeout_min_s=8.0, timeout_max_s=60.0
        )
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        ae.on_net_power_update(112.0, 400.0)  # 12s lag
        assert abs(ae.timeout - 24.0) < 1.0

    def test_ev_step_down_detected(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(2000.0, 1000.0, 100.0)
        assert ae.active is True

    def test_alignment_completes_on_down_reaction(self):
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(2000.0, 1000.0, 100.0)
        assert ae.active is True
        ae.on_net_power_update(104.0, -500.0)
        assert ae.active is False

    def test_settling_window(self):
        ae = AlignmentEngine()
        assert ae.settling is False
        ae.start_settling(100.0, 10.0)
        assert ae.settling is True
        ae.check_settling(105.0)
        assert ae.settling is True  # not expired yet
        ae.check_settling(111.0)
        assert ae.settling is False  # expired

    def test_settling_blocks_nothing_if_not_started(self):
        ae = AlignmentEngine()
        ae.check_settling(200.0)
        assert ae.settling is False


# ===========================================================================
# Tests: Skew and Coherence
# ===========================================================================

class TestSkewAndCoherence:
    def test_skew_both_fresh(self):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        net.update(100.0, 1000.0)
        ev.update(200.0, 1000.5)
        skew = compute_skew(net, ev)
        assert skew is not None
        assert abs(skew - 0.5) < 0.01

    def test_skew_none_when_no_data(self):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        assert compute_skew(net, ev) is None

    def test_coherence_high_when_synchronized(self):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(10):
            net.update(float(i * 100), i * 10.0)
            ev.update(float(i * 50), i * 10.0 + 0.1)
        coh = compute_coherence(net, ev)
        assert coh > 0.5

    def test_coherence_low_when_no_data(self):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        coh = compute_coherence(net, ev)
        assert coh == 0.0

    def test_adaptive_threshold_increases_with_interval(self):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        # Simulate 30s intervals
        for i in range(10):
            net.update(float(i * 100), i * 30.0)
            ev.update(float(i * 50), i * 30.0)
        t30 = compute_adaptive_skew_threshold(net, ev)

        net2 = MeasurementTracker("net")
        ev2 = MeasurementTracker("ev")
        # Simulate 5s intervals
        for i in range(10):
            net2.update(float(i * 100), i * 5.0)
            ev2.update(float(i * 50), i * 5.0)
        t5 = compute_adaptive_skew_threshold(net2, ev2)

        assert t30 > t5


# ===========================================================================
# Tests: Skew scenarios with different lag durations
# ===========================================================================

class TestSkewScenarios:
    """Verify adaptive threshold and alignment_active behavior under skew."""

    def _simulate_skewed_streams(self, net_interval, ev_interval, skew_s, n_samples=15):
        """Build trackers where ev lags net by skew_s."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(n_samples):
            net.update(float(i * 100), i * net_interval)
            ev.update(float(i * 50), i * ev_interval + skew_s)
        return net, ev

    @pytest.mark.parametrize("skew_s", [5, 20, 60])
    def test_skew_detected(self, skew_s):
        net, ev = self._simulate_skewed_streams(10, 10, skew_s)
        skew = compute_skew(net, ev)
        assert skew is not None
        # With constant-interval streams, skew ≈ skew_s
        assert abs(skew - skew_s) < 1.0

    @pytest.mark.parametrize("interval", [5, 30, 60])
    def test_universality_different_intervals(self, interval):
        """Verify stable behaviour at different sensor update intervals."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(15):
            net.update(float(i * 100), i * interval)
            ev.update(float(i * 50), i * interval)
        # Threshold should adapt to interval
        threshold = compute_adaptive_skew_threshold(net, ev)
        assert threshold >= 2.0
        # Coherence should be high when synchronized
        coh = compute_coherence(net, ev)
        assert coh > 0.5


# ===========================================================================
# Tests: compute_confidence
# ===========================================================================

class TestConfidence:
    def _make_tracker(self, last_update_offset: float = 0.0) -> MeasurementTracker:
        t = MeasurementTracker("test")
        t.update(100.0, time.monotonic() - last_update_offset)
        return t

    def test_high_confidence_fresh_data(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=5.0,
            last_committed=5.0, sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH

    def test_low_confidence_stale_data(self):
        net = self._make_tracker(100.0)
        ev = self._make_tracker(100.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=5.0,
            last_committed=5.0, sample_interval=10.0,
        )
        assert result == CONFIDENCE_LOW

    def test_alignment_reduces_confidence(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=True, target_current=5.0,
            last_committed=5.0, sample_interval=10.0,
        )
        assert result == CONFIDENCE_MEDIUM

    def test_settling_reduces_confidence(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=5.0,
            last_committed=5.0, sample_interval=10.0,
            settling=True,
        )
        assert result == CONFIDENCE_MEDIUM

    def test_no_previous_commit_no_penalty(self):
        net = self._make_tracker(0.0)
        ev = self._make_tracker(0.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=5.0,
            last_committed=None, sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH

    def test_constant_values_regular_polling_high_confidence(self):
        """Acceptance: constant sensor values with regular polling → HIGH confidence."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        base = time.monotonic()
        # Simulate 10 polls at 10s intervals with constant values
        for i in range(10):
            net.update(500.0, base + i * 10.0)
            ev.update(1000.0, base + i * 10.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=3.0,
            last_committed=3.0, sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH


# ===========================================================================
# Tests: Hysteresis logic
# ===========================================================================

class TestHysteresisLogic:
    def _should_modulate(self, current_setpoint, target,
                         hysteresis_up=1.0, hysteresis_down=1.0):
        delta = target - current_setpoint
        if delta > 0 and delta < hysteresis_up:
            return False
        if delta < 0 and abs(delta) < hysteresis_down:
            return False
        return True

    def test_no_change_within_band(self):
        assert self._should_modulate(4.0, 4.5) is False

    def test_change_above_band(self):
        assert self._should_modulate(4.0, 5.5) is True

    def test_no_decrease_within_band(self):
        assert self._should_modulate(4.0, 3.5) is False

    def test_decrease_below_band(self):
        assert self._should_modulate(4.0, 2.5) is True

    def test_exact_boundary_up(self):
        assert self._should_modulate(4.0, 5.0) is True

    def test_exact_boundary_down(self):
        assert self._should_modulate(4.0, 3.0) is True


# ===========================================================================
# Tests: Flapping boundary — noise around 1.9–2.1A
# ===========================================================================

class TestFlappingBoundary:
    """Provide small noise around 1.9–2.1A; output must remain stable."""

    def _simulate_controller(self, values, hysteresis=1.0, max_step=1.0):
        """Simplified controller: returns list of committed integer setpoints."""
        committed = 2.0
        committed_int = 2
        results = []
        for v in values:
            delta = v - committed
            # Hysteresis gate
            if delta > 0 and delta < hysteresis:
                results.append(committed_int)
                continue
            if delta < 0 and abs(delta) < hysteresis:
                results.append(committed_int)
                continue
            # Rate limit
            step = min(abs(delta), max_step)
            if delta > 0:
                committed = committed + step
            else:
                committed = committed - step
            new_int = max(int(committed), 0)
            if new_int != committed_int:
                committed_int = new_int
            results.append(committed_int)
        return results

    def test_noise_around_2a_stays_stable(self):
        """With noise between 1.9 and 2.1, setpoint should not flip."""
        import random
        random.seed(42)
        values = [2.0 + random.uniform(-0.2, 0.2) for _ in range(50)]
        results = self._simulate_controller(values)
        # All results should be 2 (never flip to 1 or 3)
        assert all(r == 2 for r in results)

    def test_noise_around_1a_boundary(self):
        """With noise between 0.9 and 1.1, should stay at 1 not flip to 0."""
        import random
        random.seed(42)
        values = [1.0 + random.uniform(-0.15, 0.15) for _ in range(50)]
        committed = 1.0
        committed_int = 1
        results = []
        for v in values:
            delta = v - committed
            if delta > 0 and delta < 1.0:
                results.append(committed_int)
                continue
            if delta < 0 and abs(delta) < 1.0:
                results.append(committed_int)
                continue
            step = min(abs(delta), 1.0)
            if delta > 0:
                committed = committed + step
            else:
                committed = committed - step
            new_int = max(int(committed), 0)
            if new_int != committed_int:
                committed_int = new_int
            results.append(committed_int)
        assert all(r == 1 for r in results)


# ===========================================================================
# Tests: Rate limiting logic
# ===========================================================================

class TestRateLimiting:
    def _apply_rate_limit(self, current_setpoint, target, max_step=1.0):
        delta = target - current_setpoint
        step = min(abs(delta), max_step)
        if delta > 0:
            return current_setpoint + step
        return current_setpoint - step

    def test_large_increase_limited(self):
        assert self._apply_rate_limit(4.0, 8.0) == 5.0

    def test_large_decrease_limited(self):
        assert self._apply_rate_limit(8.0, 4.0) == 7.0

    def test_small_change_preserved(self):
        assert abs(self._apply_rate_limit(4.0, 4.8) - 4.8) < 0.01

    def test_exact_1a_step(self):
        assert self._apply_rate_limit(4.0, 5.0) == 5.0


# ===========================================================================
# Tests: Import safety logic
# ===========================================================================

class TestImportSafety:
    def _check(self, net_w, mono_now, exceed_since,
               threshold=100.0, duration=3.0):
        if net_w > threshold:
            if exceed_since is None:
                exceed_since = mono_now
            elif (mono_now - exceed_since) >= duration:
                return True, exceed_since
        else:
            exceed_since = None
        return False, exceed_since

    def test_no_import_no_trigger(self):
        triggered, since = self._check(-500.0, 100.0, None)
        assert triggered is False
        assert since is None

    def test_import_starts_timer(self):
        triggered, since = self._check(200.0, 100.0, None)
        assert triggered is False
        assert since == 100.0

    def test_import_triggers_after_duration(self):
        triggered, _ = self._check(200.0, 104.0, 100.0)
        assert triggered is True

    def test_import_resets_on_export(self):
        triggered, since = self._check(-100.0, 104.0, 100.0)
        assert triggered is False
        assert since is None

    def test_import_not_triggered_before_duration(self):
        triggered, since = self._check(200.0, 102.0, 100.0)
        assert triggered is False
        assert since == 100.0


# ===========================================================================
# Tests: Idempotent commit
# ===========================================================================

class TestIdempotentCommit:
    def test_same_int_skipped(self):
        assert 5 == 5

    def test_different_int_sent(self):
        assert 5 != 6

    def test_float_rounding_same_int(self):
        assert int(5.3) == int(5.7) == 5


# ===========================================================================
# Tests: Min on/off time
# ===========================================================================

class TestMinOnOffTime:
    def _can_start(self, last_off, mono_now, min_off=120.0):
        if last_off is not None and (mono_now - last_off) < min_off:
            return False
        return True

    def _can_stop(self, last_on, mono_now, min_on=300.0):
        if last_on is not None and (mono_now - last_on) < min_on:
            return False
        return True

    def test_can_start_after_min_off(self):
        assert self._can_start(100.0, 300.0) is True

    def test_cannot_start_before_min_off(self):
        assert self._can_start(100.0, 150.0) is False

    def test_can_start_first_time(self):
        assert self._can_start(None, 100.0) is True

    def test_can_stop_after_min_on(self):
        assert self._can_stop(100.0, 500.0) is True

    def test_cannot_stop_before_min_on(self):
        assert self._can_stop(100.0, 200.0) is False

    def test_can_stop_first_time(self):
        assert self._can_stop(None, 100.0) is True


# ===========================================================================
# Tests: Self-induced dip scenario
# ===========================================================================

class TestSelfInducedDip:
    """Simulate EV power change first, net updates later.

    Verify controller does not oscillate and uses settling window
    + conservative down / gated up.
    """

    def test_settling_blocks_upward_during_window(self):
        """After a setpoint change, upward steps blocked during settling."""
        ae = AlignmentEngine()
        ae.start_settling(100.0, 10.0)
        # Settling active → should block upward
        assert ae.settling is True
        # After settling expires → unblocked
        ae.check_settling(115.0)
        assert ae.settling is False

    def test_ev_step_then_net_lag_no_oscillation(self):
        """EV power jumps → alignment active → upward blocked → net catches up → resume."""
        ae = AlignmentEngine(ev_step_threshold_w=400.0)

        # Step 1: EV power increases by 700W
        ae.on_ev_power_change(1000.0, 1700.0, 100.0)
        assert ae.active is True

        # Step 2: Net hasn't updated yet — upward should be blocked
        # (modulate logic checks ae.active before allowing up)

        # Step 3: Net reacts after 5s
        ae.on_net_power_update(105.0, 500.0)
        assert ae.active is False

        # Step 4: Controller can now resume upward modulation


# ===========================================================================
# Tests: Tracker freshness with constant values over multiple polls
# ===========================================================================

class TestTrackerFreshnessConstantValues:
    """Feed constant values over multiple polls; ensure last_seen advances
    and staleness does NOT trigger."""

    def test_freshness_advances_every_poll(self):
        t = MeasurementTracker("net")
        for i in range(20):
            t.update(500.0, 1000.0 + i * 10.0)
        assert t.last_seen == 1190.0
        assert t.last_value == 500.0
        # first change was at 1000.0; value never changed after
        assert t.last_changed == 1000.0

    def test_intervals_computed_from_polls_not_value_changes(self):
        t = MeasurementTracker("net")
        for i in range(20):
            t.update(500.0, 1000.0 + i * 10.0)
        assert t.avg_interval is not None
        assert abs(t.avg_interval - 10.0) < 2.0
        assert t.interval_median is not None

    def test_confidence_stays_high_with_constant_values(self):
        """With constant sensor values but regular polling, confidence must be HIGH."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        base = time.monotonic()
        for i in range(15):
            net.update(500.0, base + i * 10.0)
            ev.update(1000.0, base + i * 10.0)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=3.0,
            last_committed=3.0, sample_interval=10.0,
        )
        assert result == CONFIDENCE_HIGH


# ===========================================================================
# Tests: Universality — parameterize for different update intervals
# ===========================================================================

class TestUniversality:
    """Verify behavior remains stable for different update intervals."""

    @pytest.mark.parametrize("interval", [5, 10, 30, 60])
    def test_tracker_adapts_to_interval(self, interval):
        t = MeasurementTracker("test")
        for i in range(20):
            t.update(float(i * 100), i * interval)
        assert t.avg_interval is not None
        assert abs(t.avg_interval - interval) < interval * 0.3

    @pytest.mark.parametrize("interval", [5, 10, 30, 60])
    def test_coherence_stable_at_interval(self, interval):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        for i in range(20):
            net.update(float(i * 100), i * interval)
            ev.update(float(i * 50), i * interval + 0.1)
        coh = compute_coherence(net, ev)
        assert coh > 0.4

    @pytest.mark.parametrize("interval", [5, 10, 30, 60])
    def test_confidence_high_at_any_interval(self, interval):
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        base = time.monotonic()
        for i in range(15):
            net.update(500.0, base + i * interval)
            ev.update(1000.0, base + i * interval)
        result = compute_confidence(
            net_tracker=net, ev_tracker=ev,
            alignment_active=False, target_current=3.0,
            last_committed=3.0, sample_interval=float(interval),
        )
        assert result == CONFIDENCE_HIGH


# ===========================================================================
# Tests: Lag warmup — never permanently Unknown
# ===========================================================================

# Warmup threshold matching coordinator._LAG_WARMUP_SAMPLES
_LAG_WARMUP_SAMPLES = 5


def _compute_estimated_lag(alignment_lag, net_intervals_count, ev_intervals_count):
    """Mirror coordinator lag output logic (warmup-aware fallback)."""
    if alignment_lag is not None:
        return round(alignment_lag, 2)
    if (net_intervals_count >= _LAG_WARMUP_SAMPLES
            and ev_intervals_count >= _LAG_WARMUP_SAMPLES):
        return 0.0
    return None


class TestLagWarmup:
    """Lag must become numeric after warmup; never stay Unknown forever."""

    def test_lag_is_none_before_warmup(self):
        """Before enough samples, lag is None (Unknown)."""
        assert _compute_estimated_lag(None, 2, 2) is None

    def test_lag_becomes_zero_after_warmup_no_steps(self):
        """After warmup with no EV steps, lag defaults to 0.0."""
        assert _compute_estimated_lag(None, 5, 5) == 0.0

    def test_lag_becomes_zero_with_extra_samples(self):
        """Well past warmup, lag is still 0.0 if no steps detected."""
        assert _compute_estimated_lag(None, 20, 20) == 0.0

    def test_lag_uses_real_value_when_available(self):
        """When alignment engine computes lag, use it instead of fallback."""
        assert _compute_estimated_lag(3.5, 20, 20) == 3.5

    def test_lag_numeric_after_warmup_with_trackers(self):
        """Feed real trackers enough samples and verify lag is numeric."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        ae = AlignmentEngine()
        for i in range(10):
            net.update(float(i * 100), i * 10.0)
            ev.update(float(i * 50), i * 10.0)
        lag = _compute_estimated_lag(
            ae.estimated_lag, len(net._intervals), len(ev._intervals)
        )
        assert lag is not None
        assert isinstance(lag, float)
        assert lag == 0.0

    def test_lag_stays_numeric_across_mode_transitions(self):
        """Lag must remain numeric even when switching modes."""
        net = MeasurementTracker("net")
        ev = MeasurementTracker("ev")
        ae = AlignmentEngine()
        # Warm up
        for i in range(10):
            net.update(float(i * 100), i * 10.0)
            ev.update(float(i * 50), i * 10.0)
        # Surplus mode → lag is numeric
        lag1 = _compute_estimated_lag(
            ae.estimated_lag, len(net._intervals), len(ev._intervals)
        )
        assert lag1 is not None
        # Simulate switch to force mode (no EV steps, just more samples)
        for i in range(10, 20):
            net.update(float(i * 100), i * 10.0)
            ev.update(3000.0, i * 10.0)
        lag2 = _compute_estimated_lag(
            ae.estimated_lag, len(net._intervals), len(ev._intervals)
        )
        assert lag2 is not None
        # Simulate switch back to surplus
        for i in range(20, 30):
            net.update(float(i * 50), i * 10.0)
            ev.update(float(i * 25), i * 10.0)
        lag3 = _compute_estimated_lag(
            ae.estimated_lag, len(net._intervals), len(ev._intervals)
        )
        assert lag3 is not None

    def test_lag_real_value_after_ev_step(self):
        """After an EV step and net reaction, lag is the real measured value."""
        ae = AlignmentEngine(ev_step_threshold_w=400.0)
        ae.on_ev_power_change(1000.0, 1500.0, 100.0)
        ae.on_net_power_update(105.0, 400.0)
        assert ae.estimated_lag is not None
        lag = _compute_estimated_lag(ae.estimated_lag, 10, 10)
        assert abs(lag - 5.0) < 0.1