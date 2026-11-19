"""Bounded price-horizon planning and the explicitly selected Green policy.

All energy is AC energy. Unknown prices are never forecasts of cheap energy.
Only this pure calculation module chooses windows; it cannot actuate equipment.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, time
from math import isfinite
from zoneinfo import ZoneInfo

from ..const import EPSILON_KWH, MAX_HORIZON_SECONDS
from .models import Plan, Tariff, Window
from .planner import _short_gaps, _slots, deadline_fallback


def mean_import_price(rows: list[Tariff], now: float, timezone: str) -> float | None:
    """Duration-weight complete local days: today plus tomorrow if published.

    Historical intervals of today are intentionally included. A partial tomorrow
    is excluded in full. Real timestamps give 23/25-hour days their correct weight.
    """
    try:
        zone = ZoneInfo(timezone)
        today = datetime.fromtimestamp(now, zone).date()
        ordered = sorted(rows, key=lambda r: (r.start, r.end))
        if any(not all(isfinite(v) for v in (r.start, r.end, r.buy)) or r.end <= r.start for r in ordered):
            return None
        if any(a.end > b.start + .001 for a, b in zip(ordered, ordered[1:])):
            return None
        total = duration = 0.0
        for day in (today, today + timedelta(days=1)):
            start = datetime.combine(day, time(), zone).timestamp()
            end = datetime.combine(day + timedelta(days=1), time(), zone).timestamp()
            cursor, value = start, 0.0
            for row in ordered:
                a, b = max(start, row.start), min(end, row.end)
                if a >= b:
                    continue
                if abs(a-cursor) > .001:
                    break
                value += (b-a)*row.buy
                cursor = b
            if abs(cursor-end) > .001:
                if day == today:
                    return None
                break
            total += value
            duration += end-start
        return total/duration if duration else None
    except (ValueError, KeyError, OverflowError):
        return None


def idle_green_policy(rows: list[Tariff], now: float, timezone: str, *, enabled: bool, export_known: bool) -> dict:
    """No goal: compare net export now with the full-day import mean.

    This grants no Manual authority. Missing prices always leave Green allowed.
    The early-charging cent offset belongs to requests, not to this idle policy.
    """
    mean = mean_import_price(rows, now, timezone)
    current = next((r for r in rows if r.start <= now < r.end), None)
    valid = export_known and mean is not None and current is not None
    pause = bool(enabled and valid and current.sell > mean + 1e-9)
    return {"enabled": enabled, "pause": pause, "mean_import_eur_kwh": mean,
            "export_eur_kwh": current.sell if current and export_known else None,
            "reason": "export_above_known_day_mean" if pause else "green_economics_disabled" if not enabled else "economic_inputs_missing_green_allowed" if not valid else "green_export_not_above_mean"}


@dataclass
class _Piece:
    start: float
    end: float
    mode: str
    power: float
    buy: float
    sell: float
    solar: float
    baseline: float = 0.0

    @property
    def energy(self) -> float:
        return (self.end-self.start)*self.power/3600


def _replace_span(pieces: list[_Piece], start: float, end: float, mode: str, power: float, *, baseline: float | None = None) -> list[_Piece]:
    result = []
    for part in pieces:
        a, b = max(start, part.start), min(end, part.end)
        if a >= b-.000001:
            result.append(part)
            continue
        if part.start < a:
            result.append(replace(part, end=a))
        result.append(replace(part, start=a, end=b, mode=mode, power=power, baseline=part.baseline if baseline is None else baseline))
        if b < part.end:
            result.append(replace(part, start=b))
    return result


def _blocks(pieces: list[_Piece], mode: str, *, export_known: bool = True) -> list[Window]:
    result: list[Window] = []
    for part in pieces:
        if part.mode != mode or part.end-part.start < .000001:
            continue
        if mode == "Manual":
            green = min(part.solar, part.power)*(part.end-part.start)/3600
            cost = (part.energy-green)*part.buy + green*part.sell if export_known or not green else None
            # additional_kwh is the grid part; Green inside Manual is credited
            # separately in Plan.expected_solar_kwh, just like the existing API.
            extra = part.energy-green
        else:
            cost = part.energy*part.sell if export_known else None
            extra = 0.0
        window = Window(part.start, part.end, part.energy, cost, extra)
        if result and abs(result[-1].end-part.start) < .001:
            old = result.pop()
            result.append(Window(old.start, window.end, old.kwh+window.kwh,
                                 old.cost+window.cost if old.cost is not None and window.cost is not None else None,
                                 old.additional_kwh+window.additional_kwh))
        else:
            result.append(window)
    return result


def _practical_pieces(pieces: list[_Piece], power: float, minimum: float, now: float, continuing: bool, import_ceiling: float) -> tuple[list[_Piece], list[str]]:
    """Move an isolated short Manual block into adjacent unused capacity.

    Destinations can already be Green: only their incremental capacity counts.
    Removing the source and adding equal AC-kWh conserves the request allowance.
    Never create a second isolated start to eliminate the first one.
    """
    warnings = []
    for _ in range(len(pieces)):
        pieces = _bridge_short_gaps(pieces, power, minimum, now if continuing else None, import_ceiling)
        blocks = _blocks(pieces, "Manual")
        changed = False
        for block in blocks:
            if block.end-block.start >= minimum-.001 or continuing and abs(block.start-now) < .001:
                continue
            candidates = []
            origins = [p for p in pieces if p.mode == "Manual" and block.start <= p.start < block.end]
            released = sum((p.power-p.baseline)*(p.end-p.start)/3600 for p in origins)
            for part in pieces:
                capacity = power-part.power
                if part.mode == "Manual" or capacity <= 0 or part.buy > import_ceiling+1e-9:
                    continue
                duration = released/capacity*3600
                if duration > part.end-part.start+.001:
                    continue
                for other in blocks:
                    if other is block:
                        continue
                    if abs(part.end-other.start) < .001:
                        a, b = part.end-duration, part.end
                    elif abs(part.start-other.end) < .001:
                        a, b = part.start, part.start+duration
                    else:
                        continue
                    old_cost = part.power*part.sell
                    cost = (power-min(power, part.solar))*part.buy + min(power, part.solar)*part.sell
                    candidates.append(((cost-old_cost)/capacity, a, b))
            if candidates:
                _, a, b = min(candidates)
                for origin in origins:
                    pieces = _replace_span(pieces, origin.start, origin.end,"Green" if origin.baseline else "Pause",origin.baseline)
                pieces = _replace_span(pieces, a, b, "Manual", power)
                changed = True
                break
        if not changed:
            break
    if any(w.end-w.start < minimum-.001 and not (continuing and abs(w.start-now) < .001) for w in _blocks(pieces, "Manual")):
        warnings.append("short_block_required_for_significant_deficit")
    return pieces, warnings


def _bridge_short_gaps(pieces: list[_Piece], power: float, minimum: float, continuing_at: float | None, import_ceiling: float) -> list[_Piece]:
    """Replace short Manual gaps with equal energy from the last Manual tail.

    Preserve selected Green energy, the early-import ceiling and price coverage.
    Nothing bridges the unknown interval before an unpriced departure fallback.
    """
    for _ in range(len(pieces)):
        blocks = _blocks(pieces, "Manual")
        for a, b in _short_gaps(blocks, minimum, continuing_at):
            parts = [p for p in pieces if a <= p.start < b]
            if abs(sum(p.end-p.start for p in parts)-(b-a)) > .001 or any(p.buy > import_ceiling+1e-9 for p in parts):
                continue
            debit = sum((power-p.power)*(p.end-p.start)/3600 for p in parts)
            donors = [p for p in pieces if p.mode == "Manual" and p.start >= b-.001]
            capacity = sum((p.power-p.baseline)*(p.end-p.start)/3600 for p in donors)
            if debit > capacity+1e-9:
                continue
            for part in parts:
                pieces = _replace_span(pieces, part.start, part.end, "Manual", power, baseline=part.power)
            for donor in reversed(donors):
                incremental = donor.power-donor.baseline
                if incremental <= 0:
                    continue
                duration = min(donor.end-donor.start, debit/incremental*3600)
                pieces = _replace_span(pieces, donor.end-duration, donor.end, "Green" if donor.baseline else "Pause", donor.baseline)
                debit = max(0, debit-duration*incremental/3600)
                if debit <= 1e-12:
                    break
            break
        else:
            return pieces
    return pieces


def economic_plan(now: float, departure: float | None, needed: float, band: float, rows: list[Tariff], *, power: float = 11,
                  reserve_min: float = 15, minimum_min: float = 3, timezone: str = "Europe/Amsterdam",
                  early_offset_cents: float = 0, export_known: bool = False, fallback_started: bool = False,
                  continue_current: bool = False) -> Plan:
    """Select Green/import energy by marginal cost on the known price horizon.

    With partial future prices a configurable below-mean ceiling filters early
    import, while a disjoint unpriced departure block covers the remaining need.
    Full price coverage or deadline urgency removes that preference. Missing
    export values preserve autonomous Green and never prevent import planning.
    """
    if not all(isfinite(v) for v in (now, needed, band, power, reserve_min, minimum_min, early_offset_cents)) or power <= 0 or needed < 0 or band < 0 or not 0 <= reserve_min <= 60 or not 1 <= minimum_min <= 30 or not 0 <= early_offset_cents <= 100:
        raise ValueError("Invalid economic planning inputs")
    if departure is not None and not isfinite(departure):
        raise ValueError("Invalid departure")
    if needed <= EPSILON_KWH:
        return Plan("Green", "energy_estimated_sufficient", needed)
    if needed <= band+EPSILON_KWH and needed/power*3600 < minimum_min*60:
        return Plan("Green", "small_remainder_within_tolerance", needed, omitted_kwh=needed)
    if departure is None or departure <= now:
        return Plan("Manual", "direct_request", needed)
    fallback = lambda warning: deadline_fallback(now, departure, needed, power, warning, started=fallback_started, minimum_min=minimum_min)
    end = min(departure-reserve_min*60, now+MAX_HORIZON_SECONDS)
    try:
        slots = _slots(rows, now, max(now, end), power)
    except ValueError:
        return fallback("invalid_or_overlapping_prices")
    if not slots:
        return fallback("prices_missing")
    continuous = abs(slots[0].start-now) < .001 and all(abs(a.end-b.start) < .001 for a,b in zip(slots, slots[1:]))
    full = continuous and slots[-1].end >= departure-reserve_min*60-.001
    if now >= departure-reserve_min*60-needed/power*3600:
        # Cost preference never consumes the time needed to reach departure.
        from .planner import plan
        if departure-now > MAX_HORIZON_SECONDS:
            return fallback("departure_capacity_requires_early_start")
        return plan(now, departure, needed, band, rows, power=power, reserve_min=reserve_min, minimum_min=minimum_min, fallback_started=fallback_started, continue_current=continue_current)
    mean = mean_import_price(rows, now, timezone)
    mean_basis = "complete_local_days"
    if not full and mean is None:
        duration = sum(s.end-s.start for s in slots)
        mean = sum((s.end-s.start)*s.buy for s in slots)/duration if duration else None
        mean_basis = "known_forward_intervals_fallback"
    ceiling = mean-early_offset_cents/100 if not full and mean is not None else None
    pieces = [_Piece(s.start, s.end, "Pause", 0, s.buy, s.sell, s.solar) for s in slots]
    choices = []
    for i, s in enumerate(slots):
        hours = (s.end-s.start)/3600
        allowed_import = full or ceiling is not None and s.buy <= ceiling+1e-9
        if not export_known:
            # Unknown export is not a free price. This ordering implements the
            # pre-existing Green preference; reported costs remain unknown.
            choices.append((float("-inf"), s.start, i, "green", s.solar*hours))
            if allowed_import:
                choices.append((s.buy, s.start, i, "grid", s.incremental_power*hours))
        elif s.sell <= s.buy:
            choices.append((s.sell, s.start, i, "green", s.solar*hours))
            if allowed_import:
                choices.append((s.buy, s.start, i, "grid", s.incremental_power*hours))
        elif allowed_import:
            # When export exceeds import, a partial full-power block is cheaper
            # per kWh than Green alone. Use the feasible lower convex envelope.
            blended = (s.solar*s.sell+s.incremental_power*s.buy)/power
            choices.append((blended, s.start, i, "bundle", power*hours))
        else:
            choices.append((s.sell, s.start, i, "green", s.solar*hours))
    allocation = [{"green": 0.0, "grid": 0.0, "bundle": 0.0} for _ in slots]
    remaining = needed
    for _, _, i, kind, capacity in sorted(choices, key=lambda c: (c[0], c[1], c[3] != "green")):
        energy = min(remaining, capacity)
        allocation[i][kind] += energy
        remaining = max(0, remaining-energy)
        if remaining <= EPSILON_KWH:
            break
    for i, (s, selected) in enumerate(zip(slots, allocation)):
        manual = selected["bundle"]/power*3600 if selected["bundle"] else selected["grid"]/s.incremental_power*3600 if s.incremental_power else 0
        green = max(0, selected["green"]/s.solar*3600-manual) if s.solar else 0
        # Start a continuing head immediately. Future partial quarters prefer
        # the end if a selected Manual neighbor follows, avoiding extra starts.
        following = i+1 < len(slots) and (allocation[i+1]["grid"] or allocation[i+1]["bundle"]) and abs(s.end-slots[i+1].start)<.001
        manual_start = s.end-manual if following and not (i == 0 and continue_current) else s.start
        if manual:
            pieces = _replace_span(pieces, manual_start, manual_start+manual, "Manual", power,baseline=s.solar if selected["grid"] else 0)
        for a,b in ((s.start,manual_start),(manual_start+manual,s.end)) if manual else ((s.start,s.end),):
            duration = min(green, b-a)
            if duration > .000001:
                pieces = _replace_span(pieces, a, a+duration, "Green", s.solar)
                green -= duration
    import_ceiling = float("inf") if full else ceiling if ceiling is not None else float("-inf")
    pieces, warnings = _practical_pieces(pieces, power, minimum_min*60, now, continue_current, import_ceiling)
    windows = _blocks(pieces, "Manual", export_known=export_known)
    green_windows = _blocks(pieces, "Green", export_known=export_known)
    actual = sum(p.energy for p in pieces)
    remaining = max(0, needed-actual)
    unpriced = False
    if remaining > EPSILON_KWH:
        # The unknown block cannot overlap known Green or Manual allocation.
        latest = now if fallback_started else departure-remaining/power*3600
        if latest < max(p.end for p in pieces)-.001:
            return fallback("known_price_capacity_insufficient")
        windows.append(Window(latest, departure, remaining, None, remaining))
        warnings.append("partial_prices_remaining_deadline_fallback")
        unpriced = True
        remaining = 0
    if _short_gaps(windows, minimum_min*60, now if continue_current else None):
        warnings.append("short_pause_retained")
    known_complete = not unpriced and full or not unpriced and sum(p.energy for p in pieces) >= needed-EPSILON_KWH
    selected_costs = [(min(p.power,p.solar)*p.sell+max(0,p.power-p.solar)*p.buy)/p.power for p in pieces if p.power>0]
    marginal = max(selected_costs, default=float("inf"))
    pauses = []
    for part in pieces:
        if part.mode == "Pause" and export_known and known_complete and part.sell > marginal+1e-9:
            pauses.append(Window(part.start, part.end, 0, 0))
    pauses = _merge_pauses(pauses)
    if any(w.end-w.start < minimum_min*60 for w in pauses):
        warnings.append("short_economic_pause_omitted")
        pauses = [w for w in pauses if w.end-w.start >= minimum_min*60]
    mode = "Manual" if any(w.start <= now < w.end for w in windows) else "Pause" if any(w.start <= now < w.end for w in pauses) else "Green"
    solar = sum(min(p.power,p.solar)*(p.end-p.start)/3600 for p in pieces)
    costs = [w.cost for w in [*windows, *green_windows]]
    cost = sum(costs) if all(c is not None for c in costs) else None
    green_cost = sum(w.cost for w in green_windows) if all(w.cost is not None for w in green_windows) else None
    if not full:
        warnings.append("partial_price_horizon_early_threshold")
        if mean_basis != "complete_local_days":
            warnings.append("day_mean_unavailable_using_known_forward_mean")
    reason = "unpriced_rolling_fallback" if unpriced and mode == "Manual" and any(w.cost is None and w.start<=now<w.end for w in windows) else "economic_export_pause" if mode == "Pause" else "selected_boost_window" if mode == "Manual" else "green_between_boost_windows"
    return Plan(mode, reason,
                needed, tuple(windows), solar, cost=cost, warnings=tuple(dict.fromkeys(warnings)), green_cost=green_cost,
                shortfall_kwh=remaining, green_windows=tuple(green_windows), pause_windows=tuple(pauses), mean_import=mean,
                price_ceiling=ceiling, tariff_coverage_end=slots[-1].end, early_threshold_active=not full,mean_basis=mean_basis,cost_scope="allocated_required_ac_energy_only")


def _merge_pauses(windows: list[Window]) -> list[Window]:
    result = []
    for w in windows:
        if result and abs(result[-1].end-w.start)<.001:
            old = result.pop()
            result.append(Window(old.start,w.end,0,0))
        else:
            result.append(w)
    return result
