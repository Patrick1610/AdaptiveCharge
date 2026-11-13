"""Deterministic economic boost planning with autonomous Green as the baseline."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..const import DEFAULT_MIN_BLOCK_MIN, DEFAULT_POWER_KW, DEFAULT_RESERVE_MIN, EPSILON_KWH, MAX_HORIZON_SECONDS
from .models import Plan, Tariff, Window


@dataclass
class _Slot:
    start: float
    end: float
    buy: float
    sell: float
    solar: float
    incremental_power: float
    chosen: float = 0.0
    chosen_start: float | None = None


def _slots(rows: list[Tariff], start: float, end: float, power: float) -> list[_Slot]:
    """Reject overlap; split hourly/mixed prices at real quarter boundaries."""
    rows = sorted(rows, key=lambda r: (r.start, r.end))
    previous_end = float("-inf")
    result = []
    for row in rows:
        if not all(isfinite(v) for v in (row.start, row.end, row.buy, row.sell, row.solar_kw)) or row.end <= row.start or row.start < previous_end:
            raise ValueError("Invalid or overlapping tariff intervals")
        previous_end = row.end
        a, b = max(start, row.start), min(end, row.end)
        while a < b:
            stop = min(b, (int(a // 900) + 1) * 900)
            solar = min(power, max(0.0, row.solar_kw))
            result.append(_Slot(a, stop, row.buy, row.sell, solar, power - solar))
            a = stop
    return result


def _windows(slots: list[_Slot], power: float) -> list[Window]:
    """Combine touching selected slots; short tails aren't separate starts."""
    windows: list[Window] = []
    for slot in slots:
        if slot.chosen <= 0:
            continue
        start = slot.start if slot.chosen_start is None else slot.chosen_start
        end = start + slot.chosen
        wall = power * slot.chosen / 3600
        extra = slot.incremental_power * slot.chosen / 3600
        cost = slot.buy * slot.incremental_power * slot.chosen / 3600
        if windows and abs(windows[-1].end - start) < 0.01:
            prev = windows.pop()
            windows.append(Window(prev.start, end, prev.kwh + wall, prev.cost + cost, prev.additional_kwh + extra))
        else:
            windows.append(Window(start, end, wall, cost, extra))
    return windows


def _practical(slots: list[_Slot], power: float, minimum: float, band: float) -> tuple[list[Window], float, list[str]]:
    """Attach isolated fragments by marginal cost, then apply agreed exceptions."""
    warnings: list[str] = []
    omitted = 0.0
    # Each successful move eliminates one isolated block. The finite budget
    # prevents pathological schedules from looping when a deadline truncates a slot.
    for _ in range(len(slots)):
        blocks = _windows(slots, power)
        moved = False
        for block in blocks:
            if block.end - block.start >= minimum - 0.01:
                continue
            origins = [s for s in slots if s.chosen > 0 and block.start <= (s.start if s.chosen_start is None else s.chosen_start) < block.end]
            candidates: list[tuple[float, float, _Slot, bool]] = []
            for slot in slots:
                if slot.chosen or slot.incremental_power <= 0:
                    continue
                for other in blocks:
                    if other == block:
                        continue
                    if abs(slot.end - other.start) < 0.01:
                        candidates.append((slot.buy, slot.start, slot, True))
                    elif abs(slot.start - other.end) < 0.01:
                        candidates.append((slot.buy, slot.start, slot, False))
            # Only a candidate that can absorb the entire fragment removes a
            # physical start; partial moves would create new fragments instead.
            candidates = [c for c in candidates if (c[2].end - c[2].start) * c[2].incremental_power / 3600 + EPSILON_KWH >= block.additional_kwh]
            if candidates:
                _, _, dest, before = min(candidates, key=lambda c: (c[0], c[1]))
                duration = block.additional_kwh * 3600 / dest.incremental_power
                for origin in origins:
                    origin.chosen = 0
                    origin.chosen_start = None
                dest.chosen_start = dest.end - duration if before else None
                dest.chosen = duration
                moved = True
                break
        if not moved:
            break
    result = []
    for block in _windows(slots, power):
        if block.end - block.start < minimum - 0.01:
            if omitted + block.additional_kwh <= band + EPSILON_KWH:
                omitted += block.additional_kwh
                warnings.append("small_remainder_within_tolerance")
                continue
            warnings.append("short_block_required_for_significant_deficit")
        result.append(block)
    return result, omitted, warnings


def plan(now: float, departure: float | None, needed: float, band: float, rows: list[Tariff], *, power: float = DEFAULT_POWER_KW, reserve_min: float = DEFAULT_RESERVE_MIN, minimum_min: float = DEFAULT_MIN_BLOCK_MIN) -> Plan:
    """Plan only the additional energy above autonomous Green, not solar control."""
    if not all(isfinite(v) for v in (now, needed, band, power, reserve_min, minimum_min)) or power <= 0 or needed < 0 or band < 0 or not 0 <= reserve_min <= 60 or not 1 <= minimum_min <= 30:
        raise ValueError("Invalid planning inputs")
    if departure is not None and not isfinite(departure):
        raise ValueError("Invalid departure")
    if needed <= EPSILON_KWH:
        return Plan("Green", "energy_estimated_sufficient", needed)
    minimum = minimum_min * 60
    if needed / power * 3600 < minimum and needed <= band + EPSILON_KWH:
        return Plan("Green", "small_remainder_within_tolerance", needed, omitted_kwh=needed)
    if departure is None or departure <= now:
        return Plan("Manual", "direct_request", needed, warnings=("short_block_required_for_significant_deficit",) if needed / power * 3600 < minimum else ())
    if departure - now > MAX_HORIZON_SECONDS:
        return Plan("Green", "departure_outside_supported_horizon", needed, warnings=("departure_outside_48h",))
    last_start = departure - reserve_min * 60 - needed / power * 3600
    if now >= last_start:
        return Plan("Manual", "departure_urgency", needed)
    cutoff = departure - reserve_min * 60
    try:
        slots = _slots(rows, now, cutoff, power)
    except ValueError:
        return Plan("Green", "invalid_prices_wait_until_latest_start", needed, warnings=("invalid_or_overlapping_prices",))
    if not slots:
        return Plan("Green", "prices_missing_wait_until_latest_start", needed, warnings=("prices_missing",))
    if abs(slots[0].start - now) > 0.01 or any(abs(a.end - b.start) > 0.01 for a, b in zip(slots, slots[1:])):
        return Plan("Green", "incomplete_prices_wait_until_latest_start", needed, warnings=("price_coverage_gap",))
    partial = slots[-1].end < cutoff - 0.01
    coverage_warnings = ["price_coverage_partial"] if partial else []
    solar = sum(s.solar * (s.end - s.start) / 3600 for s in slots)
    incremental = max(0.0, needed - solar)
    deferrable = max(0.0, cutoff - slots[-1].end) * power / 3600 if partial else 0.0
    deferred = min(incremental, deferrable)
    deferring = deferred > EPSILON_KWH
    if deferring:
        coverage_warnings.append("boost_deferred_to_unknown_prices")
    else:
        deferred = 0.0
    left = incremental - deferred
    for slot in sorted(slots, key=lambda s: (s.buy, s.start)):
        if slot.incremental_power <= 0:
            continue
        slot.chosen = min(slot.end - slot.start, left / slot.incremental_power * 3600)
        left = max(0.0, left - slot.chosen * slot.incremental_power / 3600)
        if left <= EPSILON_KWH:
            break
    for i, slot in enumerate(slots[:-1]):
        if 0 < slot.chosen < slot.end - slot.start and slots[i + 1].chosen and (i == 0 or not slots[i - 1].chosen):
            slot.chosen_start = slot.end - slot.chosen
    if left > EPSILON_KWH:
        return Plan("Green", "incomplete_prices_wait_until_latest_start", needed, expected_solar_kwh=min(needed, solar), warnings=tuple([*coverage_warnings, "insufficient_known_price_capacity"]), deferred_kwh=deferred)
    windows, omitted, warnings = _practical(slots, power, minimum, band)
    warnings = [*coverage_warnings, *warnings]
    # Full required-energy cost: Green's foregone export plus additional import.
    solar_cost = sum(s.solar * (s.end - s.start) / 3600 * s.sell for s in slots)
    extra_cost = sum(s.buy * s.incremental_power * s.chosen / 3600 for s in slots if any(w.start <= (s.start if s.chosen_start is None else s.chosen_start) < w.end for w in windows))
    if solar > needed and solar > 0:
        solar_cost *= needed / solar
    mode = "Manual" if any(w.start <= now < w.end for w in windows) else "Green"
    reason = "selected_boost_window" if mode == "Manual" else "green_between_boost_windows"
    if not windows:
        reason = "unknown_future_prices_defer_boost" if deferring else "small_remainder_within_tolerance" if omitted else "expected_green_sufficient"
    return Plan(mode, reason, needed, tuple(windows), min(needed, solar), omitted, None if deferring else solar_cost + extra_cost, tuple(dict.fromkeys(warnings)), green_cost=None if deferring else solar_cost, deferred_kwh=deferred)
