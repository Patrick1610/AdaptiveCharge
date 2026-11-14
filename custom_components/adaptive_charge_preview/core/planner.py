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


def _join_running_head(slots: list[_Slot], minimum: float) -> None:
    """Bridge a short pause into the next quarter using the same allocation."""
    if len(slots) < 2:
        return
    head = slots[0]
    future = [(s,s.chosen) for s in slots[1:] if s.chosen]
    if not future:
        return
    following = future[0][0]
    gap_start = head.start+head.chosen
    gap_end = following.start if following.chosen_start is None else following.chosen_start
    if not 0 < gap_end-gap_start < minimum:
        return
    parts = [(s,max(s.start,gap_start),min(s.end,gap_end)) for s in slots if s.start < gap_end and s.end > gap_start]
    extra = sum(s.incremental_power*(end-start)/3600 for s,start,end in parts)
    available = sum(s.incremental_power*chosen/3600 for s,chosen in future)
    if extra > available + EPSILON_KWH:
        return  # A bridge must not exceed the remaining energy allowance.
    for slot,start,end in parts:
        slot.chosen += end-start
        slot.chosen_start = slot.start
    for slot,original in sorted(future,key=lambda item:(item[0].buy,item[0].start),reverse=True):
        if slot.incremental_power <= 0:
            continue
        debit = min(original,extra*3600/slot.incremental_power)
        slot.chosen -= debit
        extra = max(0,extra-debit*slot.incremental_power/3600)
        if slot.chosen <= 0:
            slot.chosen_start = None
        if extra <= 1e-12:
            break


def _practical(slots: list[_Slot], power: float, minimum: float, band: float, *, continuing_at: float | None = None) -> tuple[list[Window], float, list[str]]:
    """Attach isolated fragments by marginal cost, then apply agreed exceptions."""
    warnings: list[str] = []
    omitted = 0.0
    # Each successful move eliminates one isolated block. The finite budget
    # prevents pathological schedules from looping when a deadline truncates a slot.
    for _ in range(len(slots)):
        blocks = _windows(slots, power)
        moved = False
        for block in blocks:
            if continuing_at is not None and abs(block.start-continuing_at) < 0.01:
                continue  # This is the remainder of a running block, not a new short start.
            if block.end - block.start >= minimum - 0.01:
                continue
            origins = [s for s in slots if s.chosen > 0 and block.start <= (s.start if s.chosen_start is None else s.chosen_start) < block.end]
            candidates: list[tuple[float, float, _Slot, float, float]] = []
            for slot in slots:
                if slot in origins or slot.incremental_power <= 0:
                    continue
                selected_start = slot.start if slot.chosen_start is None else slot.chosen_start
                free_parts = [(slot.start, selected_start), (selected_start + slot.chosen, slot.end)] if slot.chosen else [(slot.start, slot.end)]
                for other in blocks:
                    if other == block:
                        continue
                    duration = block.additional_kwh * 3600 / slot.incremental_power
                    for start, end in free_parts:
                        if duration > end - start + 0.000001:
                            continue
                        if abs(end - other.start) < 0.01:
                            placed = end - duration
                        elif abs(start - other.end) < 0.01:
                            placed = start
                        else:
                            continue
                        if slot.chosen and not (abs(placed + duration - selected_start) < 0.01 or abs(placed - selected_start - slot.chosen) < 0.01):
                            continue  # One slot cannot represent two separated occupied spans.
                        candidates.append((slot.buy, start, slot, placed, duration))
            # Only a candidate that can absorb the entire fragment removes a
            # physical start; partial moves would create new fragments instead.
            if candidates:
                _, _, dest, start, duration = min(candidates, key=lambda c: (c[0], c[1]))
                for origin in origins:
                    origin.chosen = 0
                    origin.chosen_start = None
                old_start = dest.start if dest.chosen_start is None else dest.chosen_start
                dest.chosen_start = min(old_start, start) if dest.chosen else start
                dest.chosen += duration
                moved = True
                break
        if not moved:
            break
    result = []
    for block in _windows(slots, power):
        continuing = continuing_at is not None and abs(block.start-continuing_at) < 0.01
        if not continuing and block.end - block.start < minimum - 0.01:
            if omitted + block.additional_kwh <= band + EPSILON_KWH:
                omitted += block.additional_kwh
                warnings.append("small_remainder_within_tolerance")
                continue
            warnings.append("short_block_required_for_significant_deficit")
        result.append(block)
    return result, omitted, warnings


def deadline_fallback(now: float, departure: float | None, needed: float, power: float, warning: str = "prices_missing", *, started: bool = False, minimum_min: float = DEFAULT_MIN_BLOCK_MIN) -> Plan:
    """One unpriced block ending at departure; tariff loss never cancels demand.

    The ordinary price-plan reserve is deliberately not applied: three hours
    needed for 06:15 means 03:15. Meter credits shrink the same allowance, and
    source updates rebuild the economic plan without a new command.
    """
    if not all(isfinite(v) for v in (now, needed, power)) or power <= 0 or needed < 0 or departure is not None and not isfinite(departure):
        raise ValueError("Invalid fallback planning inputs")
    warnings = [warning, "unpriced_deadline_fallback"]
    if needed <= EPSILON_KWH:
        return Plan("Green", "energy_estimated_sufficient", needed, warnings=tuple(warnings))
    if departure is None or departure <= now:
        return Plan("Manual", "unpriced_direct_request", needed, warnings=tuple(warnings))
    start = now if started else max(now, departure - needed / power * 3600)
    end = min(departure, start + needed / power * 3600)
    planned = power * (end - start) / 3600
    if end - start < minimum_min * 60:
        warnings.append("short_block_required_for_significant_deficit")
    if planned + EPSILON_KWH < needed:
        warnings.append("departure_energy_shortfall")
    return Plan("Manual" if start <= now else "Green", "unpriced_deadline_fallback", needed,
                (Window(start, end, planned, None, planned),), warnings=tuple(warnings), shortfall_kwh=max(0, needed-planned))


def plan(now: float, departure: float | None, needed: float, band: float, rows: list[Tariff], *, power: float = DEFAULT_POWER_KW, reserve_min: float = DEFAULT_RESERVE_MIN, minimum_min: float = DEFAULT_MIN_BLOCK_MIN, fallback_started: bool = False, continue_current: bool = False) -> Plan:
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
    cutoff = departure - reserve_min * 60
    coverage_end = max(cutoff, min(departure, now + 1))
    def fallback(warning: str) -> Plan:
        return deadline_fallback(now, departure, needed, power, warning, started=fallback_started, minimum_min=minimum_min)
    try:
        slots = _slots(rows, now, coverage_end, power)
    except ValueError:
        return fallback("invalid_or_overlapping_prices")
    if not slots:
        return fallback("prices_missing")
    if abs(slots[0].start - now) > 0.01 or any(abs(a.end - b.start) > 0.01 for a, b in zip(slots, slots[1:])):
        return fallback("price_coverage_gap")
    if slots[-1].end < coverage_end - 0.01:
        return fallback("price_coverage_partial")
    last_start = departure - reserve_min * 60 - needed / power * 3600
    if now >= last_start:
        # The reserve may be consumed to meet the goal; known prices remain
        # known even if there is insufficient time. This is not a feed outage.
        end = min(departure, now + needed / power * 3600)
        urgent = _slots(rows, now, end, power)
        if not urgent or abs(urgent[0].start-now)>0.01 or abs(urgent[-1].end-end)>0.01 or any(abs(a.end-b.start)>0.01 for a,b in zip(urgent,urgent[1:])):
            return fallback("price_coverage_partial")
        for slot in urgent:
            slot.chosen = slot.end - slot.start
        windows = _windows(urgent, power)
        solar = sum(s.solar * s.chosen / 3600 for s in urgent)
        green_cost = sum(s.solar * s.chosen / 3600 * s.sell for s in urgent)
        shortfall = max(0, needed - sum(w.kwh for w in windows))
        warnings = ("departure_energy_shortfall",) if shortfall > EPSILON_KWH else ()
        return Plan("Manual", "departure_urgency", needed, tuple(windows), solar, cost=green_cost+sum(w.cost for w in windows), warnings=warnings, green_cost=green_cost, shortfall_kwh=shortfall)
    solar = sum(s.solar * (s.end - s.start) / 3600 for s in slots)
    left = max(0.0, needed - solar)
    for slot in sorted(slots, key=lambda s: (s.buy, s.start)):
        if slot.incremental_power <= 0:
            continue
        slot.chosen = min(slot.end - slot.start, left / slot.incremental_power * 3600)
        left = max(0.0, left - slot.chosen * slot.incremental_power / 3600)
        if left <= EPSILON_KWH:
            break
    for i, slot in enumerate(slots[:-1]):
        # End-align future partial quarters to join their neighbor. A selected
        # current quarter that we already execute stays at now: faster meter
        # progress must not move its start forward and restart the EVSE handshake.
        if 0 < slot.chosen < slot.end - slot.start and slots[i + 1].chosen and (i == 0 or not slots[i - 1].chosen) and not (i == 0 and continue_current):
            slot.chosen_start = slot.end - slot.chosen
    if continue_current:
        _join_running_head(slots,minimum)
    windows, omitted, warnings = _practical(slots, power, minimum, band, continuing_at=now if continue_current else None)
    if left > EPSILON_KWH:
        # Defensive accounting for accumulated sub-centisecond coverage noise;
        # continuous non-urgent coverage otherwise guarantees enough capacity.
        warnings.append("departure_energy_shortfall")
    # Full required-energy cost: Green's foregone export plus additional import.
    solar_cost = sum(s.solar * (s.end - s.start) / 3600 * s.sell for s in slots)
    extra_cost = sum(s.buy * s.incremental_power * s.chosen / 3600 for s in slots if any(w.start <= (s.start if s.chosen_start is None else s.chosen_start) < w.end for w in windows))
    if solar > needed and solar > 0:
        solar_cost *= needed / solar
    mode = "Manual" if any(w.start <= now < w.end for w in windows) else "Green"
    reason = "selected_boost_window" if mode == "Manual" else "green_between_boost_windows"
    if not windows:
        reason = "small_remainder_within_tolerance" if omitted else "expected_green_sufficient"
    return Plan(mode, reason, needed, tuple(windows), min(needed, solar), omitted, solar_cost + extra_cost, tuple(dict.fromkeys(warnings)), green_cost=solar_cost, shortfall_kwh=left)
