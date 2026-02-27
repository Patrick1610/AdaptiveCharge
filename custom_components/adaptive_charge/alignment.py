"""Dynamic alignment engine for AdaptiveCharge.

Tracks measurement sources, detects EV step events, manages alignment
phases, computes confidence and coherence scores for charge current decisions.

Key design principles:
- Sample-based freshness: timestamps update on EVERY poll, not just value changes.
- Adaptive thresholds: alignment uses learned intervals and jitter, not fixed constants.
- Settling awareness: after a setpoint change a settling window prevents self-induced
  dip flapping.
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

# Maximum samples kept for interval / lag statistics
_MAX_INTERVAL_SAMPLES = 30

# Minimum delta-time (seconds) to prevent division-by-zero in EMA alpha
_MIN_DT_S = 0.001

# EWMA smoothing factor for interval and jitter estimates
_EWMA_ALPHA = 0.15

# Default multipliers for adaptive alignment threshold
_SKEW_K = 0.5  # fraction of max interval
_SKEW_M = 2.0  # multiplier for max jitter
_MIN_SKEW_THRESHOLD = 2.0  # minimum threshold in seconds


class MeasurementTracker:
    """Tracks a single measurement source with timing statistics.

    Freshness is tracked on **every** call to :meth:`update`, regardless
    of whether the value changed.  This prevents false staleness when a
    sensor reports the same value repeatedly (e.g. steady power draw).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_value: float | None = None
        # last_seen: updated on every poll (sample-based freshness)
        self.last_seen: float | None = None  # monotonic seconds
        # last_changed: updated only when the value changes
        self.last_changed: float | None = None
        self._intervals: deque[float] = deque(maxlen=_MAX_INTERVAL_SAMPLES)
        # EWMA-based interval and jitter estimates
        self.avg_interval: float | None = None
        self.jitter: float | None = None

    def update(self, value: float, mono_now: float) -> None:
        """Record a new sample — called every poll cycle."""
        # Always update freshness timestamp
        prev_seen = self.last_seen
        self.last_seen = mono_now

        # Track intervals between polls (regardless of value change)
        if prev_seen is not None:
            interval = mono_now - prev_seen
            if interval > 0:
                self._intervals.append(interval)
                # Update EWMA interval estimate
                if self.avg_interval is None:
                    self.avg_interval = interval
                else:
                    self.avg_interval += _EWMA_ALPHA * (interval - self.avg_interval)
                # Update EWMA jitter (abs deviation)
                dev = abs(interval - (self.avg_interval if self.avg_interval is not None else interval))
                if self.jitter is None:
                    self.jitter = dev
                else:
                    self.jitter += _EWMA_ALPHA * (dev - self.jitter)

        # Track value changes separately
        if self.last_value is None or value != self.last_value:
            self.last_changed = mono_now
        self.last_value = value

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
        """Seconds since last sample (monotonic).  Uses last_seen, not last_changed."""
        if self.last_seen is None:
            return None
        return time.monotonic() - self.last_seen

    @property
    def sample_age(self) -> float | None:
        """Alias for staleness — seconds since last poll sample."""
        return self.staleness

    @property
    def reliability(self) -> float:
        """Reliability score 0..1 based on jitter relative to interval.

        Low jitter relative to interval → high reliability.
        """
        if self.avg_interval is None or self.avg_interval <= 0:
            return 0.0
        if self.jitter is None:
            return 1.0
        ratio = self.jitter / self.avg_interval
        return max(0.0, min(1.0, 1.0 - ratio))


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

    The alignment threshold is *adaptive*: it is computed from the
    learned update intervals and jitter of the net and EV trackers.
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

        # Settling window: after a setpoint commit, expect transient
        self.settling: bool = False
        self._settle_start: float | None = None
        self._settle_duration: float = 0.0

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

    def start_settling(self, mono_now: float, duration: float) -> None:
        """Start a settling window after a setpoint change."""
        self.settling = True
        self._settle_start = mono_now
        self._settle_duration = duration

    def check_settling(self, mono_now: float) -> None:
        """Check if settling window has expired."""
        if not self.settling or self._settle_start is None:
            return
        if mono_now - self._settle_start >= self._settle_duration:
            self.settling = False
            self._settle_start = None

    def _record_lag(self, lag_s: float) -> None:
        self._lag_samples.append(lag_s)
        if self._lag_samples:
            self.estimated_lag = median(self._lag_samples)

    def _complete(self) -> None:
        self.active = False
        self._ev_step_ts = None
        self._ev_step_direction = 0.0


def compute_skew(
    net_tracker: MeasurementTracker,
    ev_tracker: MeasurementTracker,
) -> float | None:
    """Compute the absolute timestamp skew between net and ev streams.

    Returns None if either tracker has no data yet.
    """
    if net_tracker.last_seen is None or ev_tracker.last_seen is None:
        return None
    return abs(net_tracker.last_seen - ev_tracker.last_seen)


def compute_adaptive_skew_threshold(
    net_tracker: MeasurementTracker,
    ev_tracker: MeasurementTracker,
) -> float:
    """Compute the adaptive skew threshold from learned intervals and jitter.

    threshold = max(min_threshold, k * max(net_interval, ev_interval) + m * max(jitter))
    """
    net_int = net_tracker.avg_interval or 10.0
    ev_int = ev_tracker.avg_interval or 10.0
    net_jit = net_tracker.jitter or 0.0
    ev_jit = ev_tracker.jitter or 0.0

    threshold = _SKEW_K * max(net_int, ev_int) + _SKEW_M * max(net_jit, ev_jit)
    return max(_MIN_SKEW_THRESHOLD, threshold)


def compute_coherence(
    net_tracker: MeasurementTracker,
    ev_tracker: MeasurementTracker,
) -> float:
    """Compute measurement coherence score 0..1.

    1.0 = perfectly coherent (low skew, high reliability)
    0.0 = completely incoherent
    """
    skew = compute_skew(net_tracker, ev_tracker)
    threshold = compute_adaptive_skew_threshold(net_tracker, ev_tracker)

    if skew is None:
        return 0.0

    # Skew component: 1.0 when skew=0, 0.0 when skew >= threshold
    skew_score = max(0.0, 1.0 - skew / threshold) if threshold > 0 else 0.0

    # Reliability component: average of both tracker reliabilities
    rel_score = (net_tracker.reliability + ev_tracker.reliability) / 2.0

    # Combined: skew weighted more heavily (0.6) because timestamp alignment
    # is the primary indicator of coherent data; reliability (0.4) is secondary.
    return skew_score * 0.6 + rel_score * 0.4


def compute_confidence(
    *,
    net_tracker: MeasurementTracker,
    ev_tracker: MeasurementTracker,
    alignment_active: bool,
    target_current: float,
    last_committed: float | None,
    sample_interval: float,
    settling: bool = False,
) -> str:
    """Compute a confidence level for the current recalculation.

    Returns one of CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW.
    """
    score = 3  # start HIGH

    # Data staleness: if any source hasn't been seen in > 3× sample interval
    stale_threshold = sample_interval * 3.0
    for tracker in (net_tracker, ev_tracker):
        staleness = tracker.staleness
        if staleness is not None and staleness > stale_threshold:
            score -= 1

    # Alignment active → reduce confidence
    if alignment_active:
        score -= 1

    # Settling window active → reduce confidence
    if settling:
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