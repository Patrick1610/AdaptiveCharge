"""Dynamic alignment engine for Stormbreaker Surplus EV Charge.

Tracks measurement sources, detects EV step events, manages alignment
phases, and computes confidence scores for charge current decisions.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from statistics import median

from .const import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DEFAULT_ALIGNMENT_TIMEOUT_MAX,
    DEFAULT_ALIGNMENT_TIMEOUT_MIN,
    DEFAULT_EMA_SPAN_S,
    DEFAULT_EV_STEP_THRESHOLD_W,
)

_LOGGER = logging.getLogger(__name__)

# Maximum samples kept for interval statistics
_MAX_INTERVAL_SAMPLES = 30

# Minimum delta-time (seconds) to prevent division-by-zero in EMA alpha
_MIN_DT_S = 0.001


class MeasurementTracker:
    """Tracks a single measurement source with timing statistics."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_value: float | None = None
        self.last_update: float | None = None  # monotonic seconds
        self._intervals: deque[float] = deque(maxlen=_MAX_INTERVAL_SAMPLES)

    def update(self, value: float, mono_now: float) -> None:
        """Record a new measurement value."""
        if self.last_update is not None and value != self.last_value:
            interval = mono_now - self.last_update
            if interval > 0:
                self._intervals.append(interval)
        self.last_value = value
        self.last_update = mono_now

    @property
    def interval_median(self) -> float | None:
        """Rolling median of update intervals."""
        if len(self._intervals) < 2:
            return None
        return median(self._intervals)

    @property
    def interval_p95(self) -> float | None:
        """Approximate p95 of update intervals."""
        if len(self._intervals) < 2:
            return None
        sorted_vals = sorted(self._intervals)
        idx = int(len(sorted_vals) * 0.95)
        idx = min(idx, len(sorted_vals) - 1)
        return sorted_vals[idx]

    @property
    def staleness(self) -> float | None:
        """Seconds since last update (monotonic)."""
        if self.last_update is None:
            return None
        return time.monotonic() - self.last_update


class EMAFilter:
    """Exponential moving average filter with time-based decay."""

    def __init__(self, span_s: float = DEFAULT_EMA_SPAN_S) -> None:
        self._span_s = max(span_s, 1.0)
        self._value: float | None = None
        self._last_t: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, raw: float, mono_now: float) -> float:
        """Feed a new value and return the smoothed result."""
        if self._value is None or self._last_t is None:
            self._value = raw
            self._last_t = mono_now
            return raw

        dt = max(mono_now - self._last_t, _MIN_DT_S)
        alpha = min(1.0, 2.0 * dt / (self._span_s + dt))
        self._value = self._value + alpha * (raw - self._value)
        self._last_t = mono_now
        return self._value


class AlignmentEngine:
    """Detects EV step events and manages alignment phases.

    After an EV charge current change, the net power sensor typically
    takes a few seconds to reflect the new draw.  During this
    *alignment phase* the controller should hold its setpoint to avoid
    over-reacting to stale data.
    """

    def __init__(
        self,
        ev_step_threshold_w: float = DEFAULT_EV_STEP_THRESHOLD_W,
        timeout_min_s: float = DEFAULT_ALIGNMENT_TIMEOUT_MIN,
        timeout_max_s: float = DEFAULT_ALIGNMENT_TIMEOUT_MAX,
    ) -> None:
        self._ev_step_threshold_w = ev_step_threshold_w
        self._timeout_min_s = timeout_min_s
        self._timeout_max_s = timeout_max_s

        self.active: bool = False
        self._ev_step_ts: float | None = None
        self._ev_step_direction: float = 0.0  # +1 up, -1 down

        # Rolling lag estimates
        self._lag_samples: deque[float] = deque(maxlen=_MAX_INTERVAL_SAMPLES)
        self.estimated_lag: float | None = None

    @property
    def timeout(self) -> float:
        """Dynamic alignment timeout based on observed lag."""
        if self.estimated_lag is not None:
            return min(
                max(2.0 * self.estimated_lag, self._timeout_min_s),
                self._timeout_max_s,
            )
        return self._timeout_min_s

    def on_ev_power_change(
        self, old_w: float | None, new_w: float, mono_now: float
    ) -> None:
        """Call when EV power value changes."""
        if old_w is None:
            return
        delta = new_w - old_w
        if abs(delta) >= self._ev_step_threshold_w:
            self.active = True
            self._ev_step_ts = mono_now
            self._ev_step_direction = 1.0 if delta > 0 else -1.0
            _LOGGER.debug(
                "Alignment: EV step detected Δ=%.0fW, direction=%s",
                delta,
                "up" if self._ev_step_direction > 0 else "down",
            )

    def on_net_power_update(self, mono_now: float, net_delta: float) -> None:
        """Call when net power sensor updates.  Checks alignment completion."""
        if not self.active or self._ev_step_ts is None:
            return

        elapsed = mono_now - self._ev_step_ts

        # Check if net reacted in the expected direction
        # EV step up → net should increase; EV step down → net should decrease
        expected_sign = self._ev_step_direction
        if net_delta * expected_sign > 0 and elapsed > 0:
            self._record_lag(elapsed)
            self._complete()
            return

        # Timeout
        if elapsed >= self.timeout:
            _LOGGER.debug("Alignment: timeout after %.1fs", elapsed)
            self._complete()

    def check_timeout(self, mono_now: float) -> None:
        """Periodic check for alignment timeout."""
        if not self.active or self._ev_step_ts is None:
            return
        elapsed = mono_now - self._ev_step_ts
        if elapsed >= self.timeout:
            _LOGGER.debug("Alignment: timeout after %.1fs", elapsed)
            self._complete()

    def _record_lag(self, lag_s: float) -> None:
        self._lag_samples.append(lag_s)
        if self._lag_samples:
            self.estimated_lag = median(self._lag_samples)

    def _complete(self) -> None:
        self.active = False
        self._ev_step_ts = None
        self._ev_step_direction = 0.0


def compute_confidence(
    *,
    net_tracker: MeasurementTracker,
    ev_tracker: MeasurementTracker,
    alignment_active: bool,
    target_current: float,
    last_committed: float | None,
    sample_interval: float,
) -> str:
    """Compute a confidence level for the current recalculation.

    Returns one of CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW.
    """
    score = 3  # start HIGH

    # Data staleness: if any source hasn't updated in > 3× sample interval
    stale_threshold = sample_interval * 3.0
    for tracker in (net_tracker, ev_tracker):
        staleness = tracker.staleness
        if staleness is not None and staleness > stale_threshold:
            score -= 1

    # Alignment active → reduce confidence
    if alignment_active:
        score -= 1

    # Target instability: large jump from last committed
    if last_committed is not None:
        delta = abs(target_current - last_committed)
        if delta > 2.0:
            score -= 1

    if score >= 3:
        return CONFIDENCE_HIGH
    if score >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW
