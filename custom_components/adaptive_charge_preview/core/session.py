"""Preview-only command lifecycle and meter accounting, with no HA dependency."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..const import DWELL_SECONDS, EPSILON_KWH, MAX_EVSE_AGE, MAX_CLOCK_SKEW, MAX_REOPEN_PUBLICATION_LAG
from .models import Demand, GoalObservation, VehicleSnapshot, finite, valid_pack_capacity_ratio


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
    origin_meter: float | None = None
    reference_values: tuple[float | None, ...] | None = None
    connection_aliases: tuple[str, ...] = ()
    connection_gap_since: float | None = None
    anchor_provisional: bool = False
    reference_measured_at: float | None = None
    goal_observed_at: float | None = None
    goal_applied_at: float | None = None
    goal_full_range_km: float | None = None
    below_meter: float | None = None

    def refresh_charger_token(self, connection_id: str) -> None:
        """Record a device-token refresh without resetting logical ownership.

        The HA shell must first corroborate the same vehicle and attachment and
        process actual unplug edges. This method alone grants no such evidence.
        """
        if self.terminal or not isinstance(connection_id,str) or not connection_id:
            raise ValueError("Cannot refresh an inactive attachment")
        if self.connection_id and self.connection_id != connection_id:
            self.connection_aliases = (*self.connection_aliases[-7:],self.connection_id)
            self.connection_id = connection_id

    def stop(self, reason: str = "explicit_preview_stop") -> None:
        """Revoke the local preview command only; Green is independent."""
        self.terminal, self.phase, self.reason = True, "idle", reason
        self.below_since, self.below_meter = None, None

    def observe_connection(self, now: float, snapshot: VehicleSnapshot, *, trust_attachment: bool = False) -> bool:
        """Mutate attachment state independently of valid vehicle measurements.

        False means skip energy accounting this tick; inspect terminal/phase/
        reason to distinguish retirement, waiting and a latched block. Calling
        this twice in one tick is safe.
        """
        if self.terminal:
            return False
        continuous = bool(trust_attachment and snapshot.shared_connected is True and self.connection_id is not None and snapshot.connection_id == self.connection_id)
        # Only positive, source-timed connection evidence may terminate a request.
        connection_fresh = snapshot.connection_measured_at is not None and max(self.started_at - MAX_CLOCK_SKEW, now - MAX_EVSE_AGE) <= snapshot.connection_measured_at <= now + MAX_CLOCK_SKEW
        if not continuous and connection_fresh and snapshot.connected is False:
            self.stop("vehicle_disconnected")
            return False
        if self.connection_id and snapshot.connection_id and self.connection_id != snapshot.connection_id:
            if connection_fresh:
                self.stop("connection_replaced")
            else:
                self.identity_gap = True
                self.phase, self.reason = "waiting_data", "connection_identity_mismatch_unverified"
            return False
        if not continuous and (snapshot.connected is not True or snapshot.home is False):
            self.phase, self.reason = "waiting_data", "vehicle_connection_not_confirmed"
            self.below_since, self.below_meter = None, None
            return False
        if not snapshot.connection_id:
            # Unavailable is not evidence of a different connection. Keep the
            # anchor so the same unique connection can resume without a refill.
            self.phase, self.reason = "waiting_data", "connection_identity_unknown"
            self.below_since, self.below_meter = None, None
            return False
        if self.connection_id is None:
            self.connection_id = snapshot.connection_id
        if self.identity_gap:
            self.phase, self.reason = "blocked", "connection_continuity_requires_new_preview_start"
            return False
        return True

    def update(self, now: float, snapshot: VehicleSnapshot, demand: Demand, meter: float | None, droop_pp: float, *, meter_id: str | None = None, allow_estimated: bool = False, goal: GoalObservation | None = None) -> float | None:
        """Evaluate connection, budget and goal without issuing a command."""
        if not self.observe_connection(now, snapshot, trust_attachment=allow_estimated):
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
        self.meter_id = meter_id
        if goal is None and demand.source_fresh:
            goal = GoalObservation(snapshot.soc.value, demand.confirmed, demand.needed_kwh, demand.measured_at,
                                   effective_soc=demand.effective_soc, full_range_km=demand.full_range_km)
        previous_goal_at = self.goal_observed_at
        backdated = bool(goal and previous_goal_at is not None and goal.measured_at < previous_goal_at)
        if backdated:
            goal = None
        if demand.source_fresh and not backdated:
            self.reference_values = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value, snapshot.soh.value)
            self.reference_measured_at = demand.measured_at
        if goal is not None:
            self.goal_observed_at = goal.measured_at
        if goal is not None and goal.reached and finite(meter) is None:
            # Goal evidence needs only the actual goal fields, not a capture
            # timestamp on the battery-energy estimate or a working AC meter.
            return self._confirm_goal(goal)
        meter = finite(meter)
        if meter is None or meter < 0:
            self.phase, self.reason = "waiting_data", "energy_meter_invalid"
            self.below_since, self.below_meter = None, None
            return None
        if self.last_meter is not None and meter < self.last_meter - 0.01:
            self.meter_fault = True
            self.phase, self.reason = "blocked", "energy_meter_regressed"
            return None
        self.last_meter = meter
        if self.origin_meter is None:
            self.origin_meter = self.anchor_meter if self.anchor_meter is not None else meter
        if goal is not None and goal.reached:
            return self._confirm_goal(goal)
        reference_soc = self.confirmed_soc if self.confirmed_soc is not None else self.tolerated_soc
        if reference_soc is not None:
            waiting_phase = "watching" if self.confirmed_soc is not None else "within_tolerance"
            self.phase = waiting_phase
            self.remaining_kwh = 0.0
            if goal is None:
                self.reason = "preserved_goal_watch_waiting_vehicle"
                self.below_since, self.below_meter = None, None
                return 0.0
            if not demand.source_fresh and not allow_estimated:
                self.reason, self.below_since, self.below_meter = "preserved_goal_watch_waiting_vehicle", None, None
                return 0.0
            soc_declined = reference_soc - goal.soc >= droop_pp - 0.000001
            # Only a newer, timed range deterioration can reopen at equal SOC.
            # Repeated lagging captures and untimed VW reference edits confer
            # no new authority, even when the old capture is still fresh.
            # Express range deterioration in pp using the current full range.
            range_deficit = bool(goal.range_measured_at is not None
                                 and self.goal_applied_at is not None and goal.measured_at > self.goal_applied_at
                                 and self.goal_full_range_km is not None and goal.full_range_km is not None
                                 and goal.soc * (self.goal_full_range_km / goal.full_range_km - 1) >= droop_pp - 0.000001
                                 and demand.band_kwh > 0 and goal.estimated_need_kwh > demand.band_kwh + EPSILON_KWH)
            if range_deficit and not soc_declined and self.below_since is None and now - goal.measured_at > MAX_REOPEN_PUBLICATION_LAG:
                self.reason, self.below_meter = "range_goal_waiting_recent_capture", None
                return 0.0
            if not soc_declined and not range_deficit:
                self.reason, self.below_since, self.below_meter = "below_goal_within_return_band", None, None
                return 0.0
            if self.below_since is None:
                self.below_since = now
                self.below_meter = self.below_meter if self.below_meter is not None else meter
            if now - self.below_since < DWELL_SECONDS:
                self.reason = "return_band_dwell" if soc_declined else "range_goal_return_band_dwell"
                return 0.0
            credit = max(0.0, meter - self.below_meter) if self.below_meter is not None else 0.0
            if credit > EPSILON_KWH and goal.estimated_need_kwh - credit <= demand.band_kwh + EPSILON_KWH:
                self.goal_applied_at, self.goal_full_range_km = goal.measured_at, goal.full_range_km
                self.reason, self.below_since = "below_goal_within_return_band", None
                return 0.0
            self.confirmed_soc, self.tolerated_soc, self.below_since, self.below_meter = None, None, None, None
            self.anchor_meter, self.anchor_need, self.anchor_soc = meter - credit, goal.estimated_need_kwh, goal.soc
            self.anchor_signature = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value)
            self.goal_applied_at = goal.measured_at
            self.goal_full_range_km = goal.full_range_km
            self.anchor_provisional = False
            return self.consume_meter(meter)
        if (allow_estimated and not demand.source_fresh and goal is not None and self.anchor_meter is not None
                and goal.measured_at >= self.started_at
                and (self.goal_applied_at is None or goal.measured_at > self.goal_applied_at)
                and (self.anchor_provisional or self.anchor_soc is not None and goal.soc > self.anchor_soc)):
            # One first qualified SOC can correct the initial estimate. Later
            # allocations require actual SOC progress; repeating a capture or
            # receiving the same SOC cannot replenish the allowance.
            self.anchor_meter, self.anchor_need, self.anchor_soc = meter, goal.estimated_need_kwh, goal.soc
            self.anchor_signature = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value)
            self.goal_applied_at = goal.measured_at
            self.goal_full_range_km = goal.full_range_km
            self.anchor_provisional = False
            return self.consume_meter(meter)
        if not demand.source_fresh or backdated:
            if not allow_estimated:
                self.phase, self.reason = "waiting_data", "vehicle_measurement_stale_or_time_unknown"
                self.below_since, self.below_meter = None, None
                return None
            # An existing, bounded energy allowance does not expire with the
            # vehicle app. Stale data cannot confirm a goal or refill it.
            if self.confirmed_soc is not None or self.tolerated_soc is not None:
                self.phase = "watching" if self.confirmed_soc is not None else "within_tolerance"
                self.reason = "preserved_goal_watch_waiting_vehicle"
                self.below_since, self.below_meter = None, None
                return 0.0
            if self.anchor_meter is None:
                if not valid_pack_capacity_ratio(snapshot.battery_kwh.value / snapshot.soc.value):
                    self.phase, self.reason = "waiting_data", "initial_battery_estimate_incoherent"
                    return None
                self.anchor_meter = meter
                self.anchor_need = goal.estimated_need_kwh if goal is not None else demand.needed_kwh
                self.anchor_soc = goal.soc if goal is not None else snapshot.soc.value
                self.goal_applied_at = goal.measured_at if goal is not None else self.goal_applied_at
                self.goal_full_range_km = goal.full_range_km if goal is not None else self.goal_full_range_km
                self.anchor_signature = (snapshot.soc.value, snapshot.battery_kwh.value, snapshot.range_km.value, snapshot.limit_soc.value)
                self.reference_values = (*self.anchor_signature, snapshot.soh.value)
                self.anchor_provisional = True
                self.reference_measured_at = demand.measured_at
            return self.consume_meter(meter)
        soc = snapshot.soc.value
        if self.anchor_provisional and demand.measured_at is not None and demand.measured_at >= self.started_at:
            # The first actual post-Start capture can correct an initial estimate
            # downward too. Repeating the same capture cannot replenish it again.
            self.anchor_meter, self.anchor_need, self.anchor_soc = meter,demand.needed_kwh,soc
            self.anchor_signature = (soc,snapshot.battery_kwh.value,snapshot.range_km.value,snapshot.limit_soc.value)
            self.anchor_provisional = False
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
        if goal is not None:
            self.goal_applied_at = goal.measured_at
            self.goal_full_range_km = goal.full_range_km
        return max(0.0, remaining)

    def _confirm_goal(self, goal: GoalObservation) -> float:
        """Retain the first qualified goal SOC for subsequent return-band checks."""
        self.confirmed_soc = self.confirmed_soc if self.confirmed_soc is not None else goal.soc
        self.goal_applied_at = goal.measured_at
        self.goal_full_range_km = goal.full_range_km
        self.tolerated_soc, self.below_since, self.below_meter = None, None, None
        self.phase, self.reason, self.remaining_kwh = "watching", "vehicle_goal_confirmed", 0.0
        return 0.0

    def consume_meter(self, meter: float) -> float | None:
        """Use the saved allowance when vehicle data is temporarily absent."""
        if self.terminal or self.meter_fault or self.identity_gap or self.anchor_meter is None or finite(meter) is None or meter < 0:
            return None
        if self.last_meter is not None and meter < self.last_meter - 0.01:
            self.meter_fault = True
            self.phase, self.reason = "blocked", "energy_meter_regressed"
            return None
        self.last_meter = meter
        if self.confirmed_soc is not None or self.tolerated_soc is not None:
            return 0.0
        self.remaining_kwh = (self.anchor_need or 0.0) - (meter - self.anchor_meter)
        self.phase = "verifying" if self.remaining_kwh <= 0 else "scheduled"
        self.reason = "energy_guard_waiting_vehicle" if self.remaining_kwh <= 0 else "saved_energy_allowance_vehicle_data_delayed"
        return max(0.0, self.remaining_kwh)

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

    def set_target(self, target_km: float, departure: float | None, new_demand: Demand | None, old_demand: Demand | None, *, allow_estimated: bool = False) -> None:
        """Adjust the allowance, retaining every previously delivered kWh."""
        if finite(target_km) is None or not 0 < target_km <= 2000 or departure is not None and (finite(departure) is None or departure <= 0):
            raise ValueError("Invalid new target/departure")
        self.set_departure(departure)
        if target_km == self.target_km:
            return
        if new_demand is None or old_demand is None or not allow_estimated and (not new_demand.source_fresh or not old_demand.source_fresh):
            self.phase, self.reason = "waiting_data", "target_changed_awaiting_data"
            raise ValueError("Target change awaits valid demand; previous allowance retained")
        if self.anchor_need is not None:
            self.anchor_need += new_demand.needed_kwh - old_demand.needed_kwh
        self.target_km = target_km
        self.confirmed_soc, self.tolerated_soc, self.below_since, self.below_meter = None, None, None, None

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
        if not isinstance(session.connection_aliases,(list,tuple)) or len(session.connection_aliases)>8 or any(not isinstance(x,str) or not x for x in session.connection_aliases):
            raise ValueError("Invalid attachment aliases")
        session.connection_aliases = tuple(session.connection_aliases)
        if type(session.anchor_provisional) is not bool:
            raise ValueError("Invalid provisional anchor flag")
        if type(session.terminal) is not bool or type(session.meter_fault) is not bool or type(session.identity_gap) is not bool or session.phase not in {"idle", "waiting_data", "blocked", "watching", "within_tolerance", "scheduled", "verifying"} or not isinstance(session.reason, str):
            raise ValueError("Invalid stored command state")
        for key in ("connection_id", "source_token", "meter_id"):
            if getattr(session, key) is not None and not isinstance(getattr(session, key), str):
                raise ValueError("Invalid stored identity")
        for key in ("departure", "anchor_meter", "last_meter", "anchor_need", "anchor_soc", "confirmed_soc", "tolerated_soc", "below_since", "remaining_kwh", "origin_meter", "connection_gap_since", "reference_measured_at", "goal_observed_at", "goal_applied_at", "goal_full_range_km", "below_meter"):
            value = getattr(session, key)
            if value is not None and not number(value):
                raise ValueError("Non-finite stored command value")
        if any(getattr(session, key) is not None and getattr(session, key) < 0 for key in ("departure", "anchor_meter", "last_meter", "below_since", "origin_meter", "connection_gap_since", "reference_measured_at", "goal_observed_at", "goal_applied_at")) or any(getattr(session, key) is not None and not 0 <= getattr(session, key) <= 100 for key in ("anchor_soc", "confirmed_soc", "tolerated_soc")):
            raise ValueError("Stored command value out of range")
        if (session.anchor_meter is None) != (session.anchor_need is None) or session.anchor_meter is not None and session.last_meter is not None and session.last_meter < session.anchor_meter:
            raise ValueError("Inconsistent stored energy anchor")
        if session.goal_full_range_km is not None and not 50 <= session.goal_full_range_km <= 2000 or session.below_meter is not None and (session.below_meter < 0 or session.last_meter is None or session.below_meter > session.last_meter):
            raise ValueError("Invalid stored range-watch reference")
        if session.anchor_signature is not None:
            if not isinstance(session.anchor_signature, (list, tuple)) or len(session.anchor_signature) != 4 or any(v is not None and finite(v) is None for v in session.anchor_signature):
                raise ValueError("Invalid stored source signature")
            session.anchor_signature = tuple(session.anchor_signature)
        if session.reference_values is not None:
            if not isinstance(session.reference_values, (list, tuple)) or len(session.reference_values) != 5 or any(type(v) not in (int, float) or finite(v) is None for v in session.reference_values[:4]) or session.reference_values[4] is not None and (type(session.reference_values[4]) not in (int, float) or finite(session.reference_values[4]) is None):
                raise ValueError("Invalid saved vehicle reference")
            session.reference_values = tuple(session.reference_values)
        return session
