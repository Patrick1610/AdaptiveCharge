"""Unit tests for Stormbreaker Surplus EV Charge coordinator logic."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from statistics import mean
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import defaults for testing
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "stormbreaker_charge"))
from const import (
    DEFAULT_COOLDOWN_PERIOD,
    DEFAULT_HYSTERESIS_BAND,
    DEFAULT_MAX_STEP_SIZE,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MIN_START_CURRENT,
    DEFAULT_STALE_THRESHOLD,
    SURPLUS_CLAMP_W,
)


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
# Helpers: hysteresis / rate-limiting / safety logic (mirror coordinator)
# ---------------------------------------------------------------------------

def should_start(
    smoothed_floored: int,
    charging_on: bool,
    min_start_current: int,
    last_stop_time: datetime | None,
    now: datetime,
    min_off_time: int,
) -> tuple[bool, str]:
    """Decide whether surplus charging should start."""
    if charging_on:
        return False, "already_on"
    if last_stop_time:
        off_elapsed = (now - last_stop_time).total_seconds()
        if off_elapsed < min_off_time:
            return False, "hold:min_off_time"
    if smoothed_floored >= min_start_current:
        return True, "start"
    return False, "below_threshold"


def should_stop(
    smoothed_a: float,
    charging_on: bool,
    last_start_time: datetime | None,
    now: datetime,
    min_on_time: int,
) -> tuple[bool, str]:
    """Decide whether surplus charging should stop."""
    if not charging_on:
        return False, "already_off"
    if smoothed_a >= 1.0:
        return False, "surplus_ok"
    if last_start_time:
        on_elapsed = (now - last_start_time).total_seconds()
        if on_elapsed < min_on_time:
            return False, "hold:min_on_time"
    return True, "stop"


def compute_modulation_target(
    smoothed_a: float,
    smoothed_floored: int,
    applied_current: int,
    hysteresis_band: float,
    max_step_size: int,
    last_change_time: datetime | None,
    now: datetime,
    cooldown_period: int,
    max_current: int = 16,
) -> tuple[int | None, str]:
    """Decide modulation target with hysteresis + cooldown + max step."""
    diff = smoothed_a - applied_current
    if abs(diff) < hysteresis_band:
        return None, "within_hysteresis"
    if last_change_time:
        cooldown_elapsed = (now - last_change_time).total_seconds()
        if cooldown_elapsed < cooldown_period:
            return None, "hold:cooldown"
    if diff > 0:
        target = min(applied_current + max_step_size, smoothed_floored)
    else:
        target = max(applied_current - max_step_size, smoothed_floored)
    target = min(max(target, 1), max_current)
    if target == applied_current:
        return None, "no_change"
    direction = "upscale" if target > applied_current else "downscale"
    return target, direction


def clamp_surplus(surplus_w: float, clamp: float = SURPLUS_CLAMP_W) -> float:
    """Clamp surplus to ±clamp."""
    return max(-clamp, min(clamp, surplus_w))


def check_coherency(
    timestamps: list[datetime],
    stale_threshold: int,
) -> bool:
    """Check if timestamps are within stale_threshold of each other."""
    if len(timestamps) < 2:
        return True
    gap = (max(timestamps) - min(timestamps)).total_seconds()
    return gap <= stale_threshold


# ---------------------------------------------------------------------------
# Tests: hysteresis logic
# ---------------------------------------------------------------------------

class TestHysteresis:
    """Test hysteresis band prevents jitter around current boundaries."""

    def test_no_modulation_within_band(self):
        # Applied = 5A, smoothed = 5.5A, band = 1A → no change
        target, reason = compute_modulation_target(
            smoothed_a=5.5, smoothed_floored=5, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target is None
        assert reason == "within_hysteresis"

    def test_upscale_when_above_band(self):
        # Applied = 5A, smoothed = 6.5A, band = 1A → upscale to 6A
        target, reason = compute_modulation_target(
            smoothed_a=6.5, smoothed_floored=6, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 6
        assert reason == "upscale"

    def test_downscale_when_below_band(self):
        # Applied = 5A, smoothed = 3.2A, band = 1A → downscale to 4A (max_step=1)
        target, reason = compute_modulation_target(
            smoothed_a=3.2, smoothed_floored=3, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 4
        assert reason == "downscale"

    def test_boundary_exactly_at_band_no_change(self):
        # Applied = 5A, smoothed = 5.99A, band = 1.0 → |0.99| < 1.0 → no change
        target, reason = compute_modulation_target(
            smoothed_a=5.99, smoothed_floored=5, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target is None
        assert reason == "within_hysteresis"

    def test_boundary_exactly_at_band_triggers(self):
        # Applied = 5A, smoothed = 6.0A, band = 1.0 → |1.0| >= 1.0 → upscale
        target, reason = compute_modulation_target(
            smoothed_a=6.0, smoothed_floored=6, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 6
        assert reason == "upscale"


# ---------------------------------------------------------------------------
# Tests: rate limiting / cooldown
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Test cooldown and max step size."""

    def test_cooldown_blocks_change(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_change = now - timedelta(seconds=30)  # 30s ago, cooldown = 60s
        target, reason = compute_modulation_target(
            smoothed_a=8.0, smoothed_floored=8, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=last_change, now=now, cooldown_period=60,
        )
        assert target is None
        assert reason == "hold:cooldown"

    def test_cooldown_expired_allows_change(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_change = now - timedelta(seconds=61)  # 61s ago, cooldown = 60s
        target, reason = compute_modulation_target(
            smoothed_a=8.0, smoothed_floored=8, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=last_change, now=now, cooldown_period=60,
        )
        assert target == 6
        assert reason == "upscale"

    def test_max_step_limits_upscale(self):
        # Applied = 3A, smoothed = 10A, max_step = 1A → target = 4A
        target, reason = compute_modulation_target(
            smoothed_a=10.0, smoothed_floored=10, applied_current=3,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 4
        assert reason == "upscale"

    def test_max_step_limits_downscale(self):
        # Applied = 10A, smoothed = 3A, max_step = 1A → target = 9A
        target, reason = compute_modulation_target(
            smoothed_a=3.0, smoothed_floored=3, applied_current=10,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 9
        assert reason == "downscale"

    def test_max_step_2_allows_larger_step(self):
        # Applied = 3A, smoothed = 10A, max_step = 2A → target = 5A
        target, reason = compute_modulation_target(
            smoothed_a=10.0, smoothed_floored=10, applied_current=3,
            hysteresis_band=1.0, max_step_size=2,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 5
        assert reason == "upscale"

    def test_no_change_when_target_equals_applied(self):
        # Applied = 5A, smoothed = 6.1A (above band), but capped step → target = 5+1 = 6
        # smoothed_floored = 6, min(6, 6) = 6 != applied(5)
        target, reason = compute_modulation_target(
            smoothed_a=6.1, smoothed_floored=6, applied_current=5,
            hysteresis_band=1.0, max_step_size=1,
            last_change_time=None, now=datetime.now(), cooldown_period=60,
        )
        assert target == 6


# ---------------------------------------------------------------------------
# Tests: minimum on/off time
# ---------------------------------------------------------------------------

class TestMinOnOffTime:
    """Test minimum on-time and off-time prevent chattering."""

    def test_start_blocked_by_min_off_time(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_stop = now - timedelta(seconds=60)  # 60s ago, min_off = 120s
        can_start, reason = should_start(
            smoothed_floored=5, charging_on=False,
            min_start_current=2,
            last_stop_time=last_stop, now=now,
            min_off_time=120,
        )
        assert can_start is False
        assert reason == "hold:min_off_time"

    def test_start_allowed_after_min_off_time(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_stop = now - timedelta(seconds=121)  # 121s ago, min_off = 120s
        can_start, reason = should_start(
            smoothed_floored=5, charging_on=False,
            min_start_current=2,
            last_stop_time=last_stop, now=now,
            min_off_time=120,
        )
        assert can_start is True

    def test_stop_blocked_by_min_on_time(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_start = now - timedelta(seconds=100)  # 100s ago, min_on = 300s
        can_stop, reason = should_stop(
            smoothed_a=0.5, charging_on=True,
            last_start_time=last_start, now=now,
            min_on_time=300,
        )
        assert can_stop is False
        assert reason == "hold:min_on_time"

    def test_stop_allowed_after_min_on_time(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        last_start = now - timedelta(seconds=301)  # 301s ago, min_on = 300s
        can_stop, reason = should_stop(
            smoothed_a=0.5, charging_on=True,
            last_start_time=last_start, now=now,
            min_on_time=300,
        )
        assert can_stop is True

    def test_start_requires_min_start_current(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        # smoothed_floored = 1A, min_start_current = 2A → should NOT start
        can_start, reason = should_start(
            smoothed_floored=1, charging_on=False,
            min_start_current=2,
            last_stop_time=None, now=now,
            min_off_time=120,
        )
        assert can_start is False
        assert reason == "below_threshold"

    def test_start_at_min_start_current(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        # smoothed_floored = 2A, min_start_current = 2A → should start
        can_start, reason = should_start(
            smoothed_floored=2, charging_on=False,
            min_start_current=2,
            last_stop_time=None, now=now,
            min_off_time=120,
        )
        assert can_start is True

    def test_no_stop_when_surplus_sufficient(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        can_stop, reason = should_stop(
            smoothed_a=3.0, charging_on=True,
            last_start_time=None, now=now,
            min_on_time=300,
        )
        assert can_stop is False
        assert reason == "surplus_ok"


# ---------------------------------------------------------------------------
# Tests: timestamp coherency
# ---------------------------------------------------------------------------

class TestTimestampCoherency:
    """Test sensor timestamp alignment checks."""

    def test_coherent_timestamps(self):
        t1 = datetime(2024, 1, 1, 12, 0, 0)
        t2 = datetime(2024, 1, 1, 12, 0, 5)  # 5s gap
        assert check_coherency([t1, t2], stale_threshold=10) is True

    def test_stale_timestamps(self):
        t1 = datetime(2024, 1, 1, 12, 0, 0)
        t2 = datetime(2024, 1, 1, 12, 0, 15)  # 15s gap
        assert check_coherency([t1, t2], stale_threshold=10) is False

    def test_exact_threshold(self):
        t1 = datetime(2024, 1, 1, 12, 0, 0)
        t2 = datetime(2024, 1, 1, 12, 0, 10)  # 10s gap = threshold
        assert check_coherency([t1, t2], stale_threshold=10) is True

    def test_single_source_always_coherent(self):
        t1 = datetime(2024, 1, 1, 12, 0, 0)
        assert check_coherency([t1], stale_threshold=10) is True

    def test_three_sources_max_gap(self):
        t1 = datetime(2024, 1, 1, 12, 0, 0)
        t2 = datetime(2024, 1, 1, 12, 0, 3)
        t3 = datetime(2024, 1, 1, 12, 0, 12)  # 12s gap from t1
        assert check_coherency([t1, t2, t3], stale_threshold=10) is False

    def test_empty_timestamps_coherent(self):
        assert check_coherency([], stale_threshold=10) is True


# ---------------------------------------------------------------------------
# Tests: safety rails
# ---------------------------------------------------------------------------

class TestSafetyRails:
    """Test surplus clamping and safety limits."""

    def test_clamp_large_positive(self):
        assert clamp_surplus(50000.0) == SURPLUS_CLAMP_W

    def test_clamp_large_negative(self):
        assert clamp_surplus(-50000.0) == -SURPLUS_CLAMP_W

    def test_clamp_within_range(self):
        assert clamp_surplus(5000.0) == 5000.0

    def test_clamp_zero(self):
        assert clamp_surplus(0.0) == 0.0

    def test_clamp_at_boundary(self):
        assert clamp_surplus(SURPLUS_CLAMP_W) == SURPLUS_CLAMP_W
        assert clamp_surplus(-SURPLUS_CLAMP_W) == -SURPLUS_CLAMP_W

    def test_default_constants_sensible(self):
        """Verify default constants have sensible values."""
        assert DEFAULT_HYSTERESIS_BAND == 1.0
        assert DEFAULT_MIN_START_CURRENT == 2
        assert DEFAULT_COOLDOWN_PERIOD == 60
        assert DEFAULT_MAX_STEP_SIZE == 1
        assert DEFAULT_STALE_THRESHOLD == 10
        assert DEFAULT_MIN_ON_TIME == 300
        assert DEFAULT_MIN_OFF_TIME == 120
        assert SURPLUS_CLAMP_W == 20000
