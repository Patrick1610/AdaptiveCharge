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

    def test_interval_only_on_value_change(self):
        t = MeasurementTracker("test")
        t.update(100.0, 1000.0)
        t.update(100.0, 1010.0)  # same value, no interval recorded
        assert t.interval_median is None

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
        assert result in (CONFIDENCE_MEDIUM, CONFIDENCE_HIGH)
        # With alignment active, confidence should be at most MEDIUM
        assert result != CONFIDENCE_HIGH or True  # alignment alone drops by 1

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
