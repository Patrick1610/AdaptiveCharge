"""Read-only quarter-hour projection of a vehicle's current charge request."""

from datetime import datetime, timezone
from math import ceil, floor
from typing import Any

from ..const import MAX_HORIZON_SECONDS
from .models import finite


def planning_attributes(row: dict[str, Any], now: float | None) -> dict[str, Any]:
    """Expose future Manual duration/energy; autonomous Green is not predicted.

    Every quarter is explicit, including zeroes between blocks. Past parts of a
    running window are clipped. This projection never creates a charge request.
    """
    request = row.get("session") or {}
    active = bool(request and request.get("terminal") is False)
    result = {"active_request": active, "vehicle_id": row.get("vehicle_id"),
              "target_km": row.get("target_km"), "departure": row.get("departure"),
              "planning_power_kw": row.get("planning_power_kw"), "plan_provisional": row.get("plan_provisional", True),
              "status": row.get("phase"), "control_enabled": row.get("control_enabled", False), "warnings": list(row.get("warnings") or []),
              "remaining_minutes": 0.0, "scheduled_energy_kwh": 0.0, "quarters": [], "windows": [],
              "energy_scope": "scheduled_manual_wall_energy", "reason": row.get("reason")}
    if not active:
        return result
    now = finite(now)
    if now is None:
        result["remaining_minutes"] = None
        result["reason"] = "planning_clock_unknown"
        return result
    prediction = row.get("plan")
    if not isinstance(prediction, dict):
        result["remaining_minutes"] = None
        return result
    result["reason"] = prediction.get("reason")
    raw = prediction.get("windows") or []
    if not isinstance(raw, (list,tuple)):
        result["remaining_minutes"] = None
        result["reason"] = "invalid_planning_window"
        return result
    if not raw and prediction.get("mode") == "Manual":
        needed, power = finite(prediction.get("needed_kwh")), finite(row.get("planning_power_kw"))
        if needed is not None and needed > 0 and power is not None and power > 0:
            raw = [{"start": now, "end": now+needed/power*3600, "kwh": needed}]
        elif needed is None or needed > 0:
            result["remaining_minutes"] = None
            result["reason"] = "invalid_planning_power_or_energy"
            return result
    intervals = []
    for item in raw:
        if not isinstance(item,dict):
            result["remaining_minutes"] = None
            result["reason"] = "invalid_planning_window"
            return result
        start, end, energy = (finite(item.get(k)) for k in ("start", "end", "kwh"))
        if start is None or end is None or energy is None or start >= end or energy < 0:
            result["remaining_minutes"] = None
            result["reason"] = "invalid_planning_window"
            return result
        a, b = max(now, start), min(now+MAX_HORIZON_SECONDS, end)
        if a < b:
            intervals.append((a, b, energy/(end-start)))
    intervals.sort()
    if any(a[1] > b[0]+0.01 for a,b in zip(intervals,intervals[1:])):
        result["remaining_minutes"] = None
        result["reason"] = "overlapping_planning_windows"
        return result
    def iso(value: float) -> str:
        return datetime.fromtimestamp(value,timezone.utc).isoformat()
    result["windows"] = [{"start":iso(a),"end":iso(b),"minutes":(b-a)/60,"energy_kwh":rate*(b-a)} for a,b,rate in intervals]
    result["remaining_minutes"] = sum((b-a)/60 for a,b,_ in intervals)
    result["scheduled_energy_kwh"] = sum(rate*(b-a) for a,b,rate in intervals)
    departure = finite(row.get("departure"))
    horizon = max([b for _,b,_ in intervals]+[min(departure,now+MAX_HORIZON_SECONDS) if departure and departure>now else now])
    first = floor(now/900)*900
    quarter_bound = ceil(MAX_HORIZON_SECONDS/900)+1
    for index in range(min(quarter_bound,ceil(max(0,horizon-first)/900))):
        start, end = first+index*900, min(first+(index+1)*900,horizon)
        minutes = energy = 0.0
        for a,b,rate in intervals:
            overlap = max(0,min(b,end)-max(a,start))
            minutes += overlap/60
            energy += overlap*rate
        result["quarters"].append({"start":iso(start),"end":iso(end),"charging":minutes>0,
                                   "minutes":minutes,"energy_kwh":energy})
    return result
