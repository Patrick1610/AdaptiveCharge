"""Typed inputs and outputs shared by planner and preview command tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import ceil, isfinite
from typing import Any

from ..const import DEFAULT_DROOP_PP, DEFAULT_MARGIN, EPSILON_PP, MAX_CLOCK_SKEW, MAX_COHERENCE_SPREAD, MAX_SOURCE_AGE


def finite(value: Any) -> float | None:
    """Reject booleans, unknowns and non-finite measurements."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def timestamp(value: Any) -> float | None:
    """Read epoch or timezone-aware ISO; never guess a timezone for source data."""
    number = finite(value)
    if number is not None:
        return number if 0 < number < 100_000_000_000 else None
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Measurement:
    """Reception is not evidence of the upstream measurement time."""

    value: float | None
    entity_id: str = ""
    unit: str | None = None
    measured_at: float | None = None
    received_at: float | None = None

    def fresh(self, now: float, max_age: float = MAX_SOURCE_AGE) -> bool:
        """Only a source measurement timestamp establishes vehicle freshness."""
        return self.value is not None and self.measured_at is not None and -MAX_CLOCK_SKEW <= now - self.measured_at <= max_age


@dataclass(frozen=True)
class VehicleConfig:
    """One independently configured vehicle under a shared charger."""

    vehicle_id: str
    name: str
    kind: str
    droop_pp: float = DEFAULT_DROOP_PP
    margin: float = DEFAULT_MARGIN
    limit_writer: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"tesla", "id3"} or not self.vehicle_id:
            raise ValueError("Invalid vehicle identity/type")
        if not 0.1 <= self.droop_pp <= 10 or not 0 <= self.margin <= 0.5:
            raise ValueError("Invalid droop or energy margin")


@dataclass(frozen=True)
class VehicleSnapshot:
    """Stored battery energy (not capacity), Tesla remaining/VW full range.

    connection_id must be stable and unique to one physical attachment. A new
    attachment needs a new ID; a reused charger/device ID misattributes energy.
    """

    soc: Measurement
    battery_kwh: Measurement
    range_km: Measurement
    limit_soc: Measurement
    soh: Measurement = field(default_factory=lambda: Measurement(100.0, unit="%"))
    connected: bool | None = None
    home: bool | None = None
    connection_id: str | None = None
    connection_measured_at: float | None = None
    shared_connected: bool | None = None


@dataclass(frozen=True)
class Demand:
    """Unfresh energy/band values are provisional and cannot grant a budget.

    A shell must use a zero omission tolerance for such advisory estimates.
    """

    requested_km: float
    effective_soc: float
    full_range_km: float
    needed_kwh: float
    band_kwh: float
    measured_at: float | None
    confirmed: bool
    source_fresh: bool
    limited: bool
    suggested_limit: float | None
    requested_soc: float
    warnings: tuple[str, ...] = ()


def demand(config: VehicleConfig, snapshot: VehicleSnapshot, target: float, now: float) -> Demand:
    """Preserve Tesla range and ID.3 SOC semantics without modifying either car."""
    soc, energy, range_km, limit = (m.value for m in (snapshot.soc, snapshot.battery_kwh, snapshot.range_km, snapshot.limit_soc))
    for m, unit in ((snapshot.soc, "%"), (snapshot.battery_kwh, "kWh"), (snapshot.range_km, "km"), (snapshot.limit_soc, "%")):
        if m.unit != unit or finite(m.value) is None:
            raise ValueError(f"Invalid vehicle measurement unit/value; expected {unit}")
    # Limits, SOH and VW's full-range reference are settings/reference values,
    # not frequently changing vehicle observations. Never forge capture times
    # for them merely to make the dynamic freshness gate pass.
    dynamic = (snapshot.soc, snapshot.battery_kwh, snapshot.range_km) if config.kind == "tesla" else (snapshot.soc, snapshot.battery_kwh)
    times = [m.measured_at for m in dynamic if m.measured_at is not None]
    coherent = not times or max(times) - min(times) <= MAX_COHERENCE_SPREAD
    fresh = all(m.fresh(now) for m in dynamic) and coherent
    if finite(target) is None or not 0 < target <= 2000 or soc is None or not 0 < soc <= 100 or energy is None or energy <= 0 or range_km is None or range_km <= 0 or limit is None or not 0 < limit <= 100:
        raise ValueError("SOC, battery energy, range, target or limit unavailable/invalid")
    if fresh and not 10 <= energy / soc * 100 <= 150:
        raise ValueError("Implausible battery reference for supported Tesla/ID.3")
    if config.kind == "tesla":
        full_range = range_km * 100 / soc
    else:
        soh = snapshot.soh.value
        if snapshot.soh.unit != "%" or finite(soh) is None or not 0 < soh <= 100:
            raise ValueError("SOH unavailable/invalid")
        full_range = range_km * soh / 100
    if not 50 <= full_range <= 2000:
        raise ValueError("Full-range reference implausible")
    requested_soc = ceil(100 * target / full_range)
    if requested_soc > 100:
        requested_soc = 100
    # A configured writer is only a capability. This read-only preview must
    # not pretend that an unperformed limit raise has already succeeded.
    effective_soc = float(min(requested_soc, limit))
    limited = 100 * target / full_range > effective_soc + EPSILON_PP
    per_pp = energy / soc * (1 + config.margin)
    effective_range = min(target, full_range * effective_soc / 100)
    need = max(0.0, effective_range - range_km) * energy / range_km * (1 + config.margin) if config.kind == "tesla" else max(0.0, effective_soc - soc) * per_pp
    reached = range_km >= effective_range if config.kind == "tesla" else soc >= effective_soc
    warnings = ([] if fresh else ["vehicle_measurement_stale_or_time_unknown"]) + (["vehicle_limit_caps_target"] if limited else [])
    if not coherent:
        warnings.append("vehicle_measurement_incoherent")
    suggested = min(100.0, requested_soc + 1) if config.limit_writer and requested_soc > limit else None
    if suggested is not None:
        warnings.append("limit_raise_suggested_not_performed")
    # Both band and need are AC-kWh: the same loss margin is applied once.
    return Demand(target, effective_soc, full_range, need, config.droop_pp * per_pp, min(times) if times else None, bool(fresh and reached), fresh, limited, suggested, float(requested_soc), tuple(warnings))


@dataclass(frozen=True)
class Tariff:
    """Half-open UTC interval, all-in import and net foregone export prices."""

    start: float
    end: float
    buy: float
    sell: float = 0.0
    solar_kw: float = 0.0


@dataclass(frozen=True)
class Window:
    """Wall kWh and incremental grid cost; Green cost is separate in Plan."""
    start: float
    end: float
    kwh: float
    cost: float
    additional_kwh: float = 0.0


@dataclass(frozen=True)
class Plan:
    """Read-only advice. When cost is known: cost = green_cost + sum(w.cost).

    Both cost fields are None on degraded paths. No cost identity is claimed
    without complete cost inputs.
    On a selected plan, need = expected_solar_kwh + sum(w.additional_kwh)
    + omitted_kwh + deferred_kwh; deferred energy has no known price yet.
    """

    mode: str
    reason: str
    needed_kwh: float
    windows: tuple[Window, ...] = ()
    expected_solar_kwh: float = 0.0
    omitted_kwh: float = 0.0
    cost: float | None = None
    warnings: tuple[str, ...] = ()
    green_cost: float | None = None
    deferred_kwh: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Expose a serializable read-only plan."""
        return asdict(self)
