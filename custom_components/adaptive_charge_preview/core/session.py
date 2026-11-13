"""Preview-only command lifecycle and meter accounting, with no HA dependency."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..const import DWELL_SECONDS, MAX_EVSE_AGE, MAX_CLOCK_SKEW
from .models import Demand, VehicleSnapshot, finite


@dataclass
class Session:
    """Logical request; physical zero power is deliberately not a lifecycle input."""

    vehicle_id: str
    generation: int
    connection_id: str | None
    started_at: float
    target_km: float
    departure: float | None
    source_token: str | None = None
    phase: str = "waiting_data"
    reason: str = "awaiting_trustworthy_inputs"
    anchor_meter: float | None = None
    last_meter: float | None = None
    anchor_need: float | None = None
    anchor_soc: float | None = None
    anchor_signature: tuple[float | None, ...] | None = None
    confirmed_soc: float | None = None
    below_since: float | None = None
    remaining_kwh: float | None = None
    terminal: bool = False
    meter_id: str | None = None
    meter_fault: bool = False
    identity_gap: bool = False
    tolerated_soc: float | None = None

    def stop(self, reason: str = "explicit_preview_stop") -> None:
        """Revoke the local preview command only; Green is independent."""
        self.terminal, self.phase, self.reason = True, "idle", reason
        self.below_since = None

    def observe_connection(self, now: float, snapshot: VehicleSnapshot) -> bool:
        """Mutate attachment state independently of valid vehicle measurements.

        False means skip energy accounting this tick; inspect terminal/phase/
        reason to distinguish retirement, waiting and a latched block. Calling
        this twice in one tick is safe.
        """
        if self.terminal:
            return False
        # Only positive, source-timed connection evidence may terminate a request.
        connection_fresh = snapshot.connection_measured_at is not None and max(self.started_at - MAX_CLOCK_SKEW, now - MAX_EVSE_AGE) <= snapshot.connection_measured_at <= now + MAX_CLOCK_SKEW
        if connection_fresh and snapshot.connected is False:
            self.stop("vehicle_disconnected")
            return False
        if self.connection_id and snapshot.connection_id and self.connection_id != snapshot.connection_id:
            if connection_fresh:
                self.stop("connection_replaced")
            else:
                self.identity_gap = True
                self.phase, self.reason = "waiting_data", "connection_identity_mismatch_unverified"
            return False
        if snapshot.connected is not True or snapshot.home is False:
            self.phase, self.reason = "waiting_data", "vehicle_connection_not_confirmed"
            self.below_since = None
            return False
        if not snapshot.connection_id:
            # Unavailable is not evidence of a different connection. Keep the
            # anchor so the same unique connection can resume without a refill.
            self.phase, self.reason = "waiting_data", "connection_identity_unknown"
            self.below_since = None
            return False
        if self.connection_id is None:
            self.connection_id = snapshot.connection_id
        if self.identity_gap:
            self.phase, self.reason = "blocked", "connection_continuity_requires_new_preview_start"
            return False
        return True

    def update(self, now: float, snapshot: VehicleSnapshot, demand: Demand, meter: float | None, droop_pp: float, *, meter_id: str | None = None) -> float | None:
        """Evaluate connection, budget and goal without issuing a command."""
        if not self.observe_connection(now, snapshot):
            return None
        if self.meter_fault:
            self.phase, self.reason = "blocked", "meter_fault_requires_stop_and_new_preview_start"
            return None
        if not meter_id:
            self.phase, self.reason = "waiting_data", "meter_identity_unknown"
            return None
        if self.meter_id is not None and self.meter_id != meter_id:
            self.meter_fault = True
            self.phase, self.reason = "blocked", "meter_identity_changed"
            return None
        meter = finite(meter)
        if meter is None or meter < 0:
            self.phase, self.reason = "waiting_data", "energy_meter_invalid"
            self.below_since = None
            return None
        if self.last_meter is not None and meter < self.last_meter - 0.01:
            self.meter_fault = True
            self.phase, self.reason = "blocked", "energy_meter_regressed"
            return None
        self.meter_id = meter_id
        self.last_meter = meter
        if not demand.source_fresh:
            self.phase, self.reason = "waiting_data", "vehicle_measurement_stale_or_time_unknown"
            self.below_since = None
            return None
        soc = snapshot.soc.value
        if demand.confirmed:
            if self.confirmed_soc is None:
                self.confirmed_soc = soc
            self.tolerated_soc = None
            self.phase, self.reason = "watching", "vehicle_goal_confirmed"
            self.below_since = None
            self.remaining_kwh = 0
            return 0.0
        reference_soc = self.confirmed_soc if self.confirmed_soc is not None else self.tolerated_soc
        if reference_soc is not None:
            waiting_phase = "watching" if self.confirmed_soc is not None else "within_tolerance"
            if soc is None or reference_soc - soc < droop_pp - 0.000001:
                self.phase, self.reason = waiting_phase, "below_goal_within_return_band"
                self.below_since = None
                return 0.0
            self.below_since = self.below_since if self.below_since is not None else now
            if now - self.below_since < DWELL_SECONDS:
                self.phase, self.reason = waiting_phase, "return_band_dwell"
                return 0.0
            self.confirmed_soc, self.tolerated_soc, self.below_since = None, None, None
            self.anchor_meter, self.anchor_need = meter, demand.needed_kwh
            self.anchor_soc = soc
            self.anchor_signature = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value)
        signature = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value)
        if self.anchor_meter is None:
            self.anchor_meter, self.anchor_need = meter, demand.needed_kwh
            self.anchor_soc, self.anchor_signature = soc, signature
        elif signature != self.anchor_signature and soc is not None and self.anchor_soc is not None and soc > self.anchor_soc:
            # A fresh higher SOC is independent progress. Same/lower old SOC
            # cannot refill an exhausted energy allowance.
            self.anchor_meter, self.anchor_need = meter, demand.needed_kwh
            self.anchor_soc, self.anchor_signature = soc, signature
        remaining = (self.anchor_need if self.anchor_need is not None else 0.0) - (meter - self.anchor_meter)
        self.remaining_kwh = remaining  # Retain negative credit internally.
        self.phase = "verifying" if remaining <= 0 else "scheduled"
        self.reason = "energy_guard_waiting_vehicle" if remaining <= 0 else "request_preserved"
        return max(0.0, remaining)

    def accept_tolerance(self, soc: float | None) -> None:
        """Use SOC from the fresh Demand in the preceding scheduled update.

        Callers must pass that update's SOC, never a separate cached reading.
        A blocked/waiting request cannot accept a tolerance reference.
        """
        if self.terminal or self.meter_fault or self.identity_gap or self.phase != "scheduled" or self.anchor_meter is None or finite(soc) is None or not 0 <= soc <= 100:
            raise ValueError("Cannot accept tolerance without a valid active SOC")
        if self.tolerated_soc is None:
            self.tolerated_soc = soc
        self.phase, self.reason = "within_tolerance", "small_remainder_within_tolerance"

    def set_departure(self, departure: float | None) -> None:
        """Apply scheduling input without changing budget or goal-watch state."""
        if departure is not None and (finite(departure) is None or departure <= 0):
            raise ValueError("Invalid departure")
        self.departure = departure

    def set_target(self, target_km: float, departure: float | None, new_demand: Demand | None, old_demand: Demand | None) -> None:
        """Adjust the allowance, retaining every previously delivered kWh."""
        if finite(target_km) is None or not 0 < target_km <= 2000 or departure is not None and (finite(departure) is None or departure <= 0):
            raise ValueError("Invalid new target/departure")
        self.set_departure(departure)
        if target_km == self.target_km:
            return
        if new_demand is None or old_demand is None or not new_demand.source_fresh or not old_demand.source_fresh:
            self.phase, self.reason = "waiting_data", "target_changed_awaiting_data"
            raise ValueError("Target change awaits valid demand; previous allowance retained")
        if self.anchor_need is not None:
            self.anchor_need += new_demand.needed_kwh - old_demand.needed_kwh
        self.target_km = target_km
        self.confirmed_soc, self.tolerated_soc, self.below_since = None, None, None

    def as_dict(self) -> dict[str, Any]:
        """Return durable own-namespace state, not compact helper JSON."""
        return asdict(self)

    @classmethod
    def restore(cls, data: dict[str, Any]) -> Session:
        """Refuse malformed stored commands rather than silently granting rights."""
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("Invalid stored command keys")
        try:
            session = cls(**data)
        except TypeError as exc:
            raise ValueError("Incomplete stored command") from exc
        number = lambda value: type(value) in (int, float) and finite(value) is not None
        if not isinstance(session.vehicle_id, str) or not session.vehicle_id or type(session.generation) is not int or session.generation < 1 or not number(session.target_km) or not 0 < session.target_km <= 2000 or not number(session.started_at) or session.started_at <= 0:
            raise ValueError("Invalid stored command")
        if type(session.terminal) is not bool or type(session.meter_fault) is not bool or type(session.identity_gap) is not bool or session.phase not in {"idle", "waiting_data", "blocked", "watching", "within_tolerance", "scheduled", "verifying"} or not isinstance(session.reason, str):
            raise ValueError("Invalid stored command state")
        for key in ("connection_id", "source_token", "meter_id"):
            if getattr(session, key) is not None and not isinstance(getattr(session, key), str):
                raise ValueError("Invalid stored identity")
        for key in ("departure", "anchor_meter", "last_meter", "anchor_need", "anchor_soc", "confirmed_soc", "tolerated_soc", "below_since", "remaining_kwh"):
            value = getattr(session, key)
            if value is not None and not number(value):
                raise ValueError("Non-finite stored command value")
        if any(getattr(session, key) is not None and getattr(session, key) < 0 for key in ("departure", "anchor_meter", "last_meter", "below_since")) or any(getattr(session, key) is not None and not 0 <= getattr(session, key) <= 100 for key in ("anchor_soc", "confirmed_soc", "tolerated_soc")):
            raise ValueError("Stored command value out of range")
        if (session.anchor_meter is None) != (session.anchor_need is None) or session.anchor_meter is not None and session.last_meter is not None and session.last_meter < session.anchor_meter:
            raise ValueError("Inconsistent stored energy anchor")
        if session.anchor_signature is not None:
            if not isinstance(session.anchor_signature, (list, tuple)) or len(session.anchor_signature) != 4 or any(v is not None and finite(v) is None for v in session.anchor_signature):
                raise ValueError("Invalid stored source signature")
            session.anchor_signature = tuple(session.anchor_signature)
        return session
