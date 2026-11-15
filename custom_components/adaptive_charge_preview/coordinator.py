"""Shared calculations and persisted commands, with one optional executor."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION, VERSION
from .core.models import VehicleConfig, demand, finite
from .core.planner import plan
from .core.economics import economic_plan, idle_green_policy
from .core.session import Session
from .control import ChargerControl
from .sources import local_time, measurement, meter_kwh, physical_power, price_feed_live, state, validate_source_bindings, vehicle_snapshot, planning_capacity
from .tariffs import export_is_known

_LOGGER = logging.getLogger(__name__)


class PreviewCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One shared charger with independent vehicle targets and one executor."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {entry.title}", update_interval=timedelta(minutes=1))
        self.entry = entry
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.values: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, Session] = {}
        self.generation = 0
        self.suppressed_token: str | None = None
        self._lock = asyncio.Lock()
        self._unsub = None
        self._last_saved: str | None = None
        self._delay_pending = False
        self.control = ChargerControl(self)
        self._unplug_edges: list[float] = []
        self.references: dict[str,tuple] = {}
        self._gateway_online: bool | None = None
        self._connection_gaps: list[float] = []
        self._uncertain_unplug_at: float | None = None
        self.reference_times: dict[str,float] = {}
        self._delivery_low: dict[str,dict[str,Any]] = {}

    @property
    def profiles(self) -> dict[str, dict[str, Any]]:
        """HA subentry identities remain stable when the vehicle is renamed."""
        return {sub.subentry_id: {**sub.data, "name": sub.title} for sub in self.entry.subentries.values() if sub.subentry_type == "vehicle"}

    async def async_initialize(self) -> None:
        """Restore only this preview's data before publishing any entities."""
        validate_source_bindings(self.hass, [dict(self.entry.data), *self.profiles.values()])
        stored = await self.store.async_load() or {}
        if not isinstance(stored, dict):
            _LOGGER.warning("Ignored malformed preview store")
            stored = {}
        for key in ("values", "sessions"):
            if not isinstance(stored.get(key, {}), dict):
                stored[key] = {}
        self.values = {k: v for k, v in stored.get("values", {}).items() if k in self.profiles and isinstance(v, dict)}
        references = stored.get("references",{})
        if isinstance(references,dict):
            self.references = {k:tuple(v) for k,v in references.items() if k in self.profiles and isinstance(v,(list,tuple)) and len(v)==5 and all(finite(x) is not None for x in v)}
        times = stored.get("reference_times",{})
        if isinstance(times,dict):
            self.reference_times = {k:float(v) for k,v in times.items() if k in self.profiles and finite(v) is not None and float(v)>0}
        generation = stored.get("generation", 0)
        self.generation = generation if type(generation) is int and generation >= 0 else 0
        self.suppressed_token = stored.get("suppressed_token") if isinstance(stored.get("suppressed_token"), str) else None
        self.control.restore(stored.get("control"))
        for vehicle_id, data in stored.get("sessions", {}).items():
            if vehicle_id not in self.profiles:
                continue
            try:
                request = Session.restore(data)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignored malformed preview command for %s", vehicle_id)
                continue
            if request.vehicle_id == vehicle_id:
                self.sessions[vehicle_id] = request
                self.generation = max(self.generation, request.generation)
        active = [s for s in self.sessions.values() if not s.terminal]
        if len(active) > 1:
            for request in active:
                request.stop("conflicting_stored_requests")
        source_ids = {str(v) for cfg in [dict(self.entry.data), *self.profiles.values()] for k, v in cfg.items() if k.endswith("_entity") and k != "current_limit_entity" and v}
        if source_ids:
            self._unsub = async_track_state_change_event(self.hass, source_ids, self._source_changed)
        gateway = state(self.hass,self.entry.data.get("charger_connectivity_entity"))
        self._gateway_online = gateway.state == "on" if gateway else None
        self.control.start_listeners()

    @callback
    def _source_changed(self, _event: Any) -> None:
        old, new = _event.data.get("old_state"), _event.data.get("new_state")
        gateway = state(self.hass,self.entry.data.get("charger_connectivity_entity"))
        if _event.data.get("entity_id") == self.entry.data.get("charger_connectivity_entity"):
            self._gateway_online = new.state == "on" if new else False
        online = not self.entry.data.get("charger_connectivity_entity") or self._gateway_online is not False and gateway and gateway.state == "on"
        if _event.data.get("entity_id") == self.entry.data.get("plug_entity") and old and new:
            if new.state == "on":
                self._uncertain_unplug_at = None
            elif new.state == "off" and old.state == "on" and online:
                self._unplug_edges.append(new.last_updated.timestamp())
            elif new.state in {"off","unknown","unavailable"}:
                self._connection_gaps.append(new.last_updated.timestamp())
                if new.state == "off" and online:
                    self._uncertain_unplug_at = new.last_updated.timestamp()
        self.hass.async_create_task(self.async_request_refresh(), name="adaptive_charge_preview refresh")

    async def async_shutdown(self) -> None:
        """Persist after explicit unload preparation; HA shutdown keeps authority."""
        await super().async_shutdown()
        async with self._lock:
            await self.control.shutdown()
            if self._unsub:
                self._unsub()
                self._unsub = None
            await self._save(immediate=True)

    def profile(self, vehicle_id: str) -> dict[str, Any]:
        """Return one selected profile including local preview overrides."""
        return {**self.profiles[vehicle_id], **self.values.get(vehicle_id, {})}

    def target(self, profile: dict[str, Any]) -> float:
        """Local preview values override a read-only external helper binding."""
        if "target_km" in profile:
            value = finite(profile["target_km"])
        else:
            value = measurement(self.hass, profile.get("target_entity")).value
        if value is None or not 0 < value <= 2000:
            raise ValueError("target_unavailable_or_invalid")
        return value

    def departure(self, profile: dict[str, Any]) -> float | None:
        if "departure" in profile:
            return profile["departure"]
        source = state(self.hass, profile.get("departure_entity"))
        if not source:
            if profile.get("departure_entity"):
                raise ValueError("departure_unavailable_or_invalid")
            return None
        parsed = local_time(source.state, self.hass.config.time_zone)
        if parsed is None:
            raise ValueError("departure_unavailable_or_invalid")
        return parsed

    def _config(self, vehicle_id: str, profile: dict[str, Any]) -> VehicleConfig:
        return VehicleConfig(vehicle_id, profile["name"], profile["kind"], float(profile.get("droop_pp", 1)), float(profile.get("margin", 0.15)), profile.get("limit_writer_entity"))

    def planning_power(self, profile: dict[str, Any]) -> float:
        """Use the same observed capacity in planning, urgency and dispatch."""
        return planning_capacity(self.hass,dict(self.entry.data),profile)["power_kw"]

    def _plan(self, now: float, departure: float | None, needed: float, band: float, rows: list, power: float, request: Any, export_known: bool) -> Any:
        shared = self.entry.data
        args = dict(power=power,reserve_min=float(shared.get("reserve_min",15)),minimum_min=float(shared.get("minimum_min",3)),
                    fallback_started=self.control.unpriced_started(request),continue_current=self.control.continuing_manual(request))
        if shared.get("economic_enabled"):
            return economic_plan(now,departure,needed,band,rows,**args,timezone=self.hass.config.time_zone,
                                 early_offset_cents=float(shared.get("early_offset_cents",0)),export_known=export_known)
        return plan(now,departure,needed,band,rows,**args)

    def _native(self) -> tuple[dict[str, Any] | None, str | None]:
        """Unknown JSON is not an explicit cancellation; empty JSON is."""
        source = state(self.hass, self.entry.data.get("session_entity"))
        if source is None:
            return None, None
        try:
            data = json.loads(source.state)
        except (ValueError, TypeError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        active = state(self.hass, self.entry.data.get("active_entity"))
        if active and active.state == "off":
            return {}, None
        if active and active.state != "on":
            return None, None
        if not data:
            return {}, None
        if data.get("v") not in {"tesla", "id3"} or finite(data.get("u")) is None or float(data["u"]) <= 0:
            return None, None
        generation = state(self.hass, self.entry.data.get("generation_entity"))
        if generation and finite(generation.state) is None:
            return None, None
        return data, f"{data.get('v')}:{data.get('u')}:{generation.state if generation else ''}"

    @callback
    def _state_for_store(self) -> dict[str, Any]:
        """Take one coherent current snapshot when HA actually serializes it."""
        self._delay_pending = False
        data = self._store_data()
        self._last_saved = json.dumps(data, sort_keys=True, allow_nan=False)
        return data

    def _store_data(self) -> dict[str, Any]:
        return {"values": deepcopy(self.values), "sessions": {k: s.as_dict() for k, s in self.sessions.items()}, "references":deepcopy(self.references), "reference_times":deepcopy(self.reference_times), "generation": self.generation, "suppressed_token": self.suppressed_token, "control": self.control.as_dict()}

    async def _save(self, *, immediate: bool = False) -> None:
        """Bound routine persistence to 60 seconds; do not re-arm its timer."""
        if immediate:
            await self.store.async_save(self._state_for_store())
        elif not self._delay_pending and json.dumps(self._store_data(), sort_keys=True, allow_nan=False) != self._last_saved:
            self._delay_pending = True
            self.store.async_delay_save(self._state_for_store, 60)

    async def _async_update_data(self) -> dict[str, Any]:
        async with self._lock:
            await self.control.drain_events()
            result = await self._observe()
            await self.control.step(result, dt_util.utcnow().timestamp())
            result.update(control_enabled=self.control.enabled, read_only=not self.control.enabled and not self.control.owned_manual and not self.control.has_limits and not self.control.green_hold, control_status=self.control.status, control_fault=self.control.fault, control_pending=deepcopy(self.control.pending), limit_audit=deepcopy(self.control.limit_audit),economic_pause=deepcopy(self.control.green_hold),economic_pause_audit=deepcopy(self.control.pause_audit),idle_green_blocked=self.control.idle_blocked,command_generation=self.generation,integration_version=VERSION,
                          unmetered_exposure_upper_kwh=(self.control.grant or {}).get("unmetered_exposure_kwh",0),
                          owned_manual=self.control.owned_manual,limit_cleanup_pending=self.control.has_limits)
            for row in result["vehicles"].values():
                row["control_enabled"] = self.control.enabled
                if self.control.owns(row["vehicle_id"]):
                    row["executor_status"] = self.control.status
                    if row.get("phase") == "would_boost" and self.control.enabled:
                        row["phase"] = "charging" if result["physical"]["positive"] and self.control.owned_manual and self.control.pending is None else "paused"
            self._delivery_diagnostics(result,dt_util.utcnow().timestamp())
            await self._save()
            return result

    def _delivery_diagnostics(self, result: dict[str,Any], now: float) -> None:
        """Warn about sustained under-delivery; never alter plans or authority."""
        physical = result["physical"]
        switch = state(self.hass,self.entry.data.get("charger_switch_entity"))
        for vehicle_id,row in result["vehicles"].items():
            expected, actual = finite(row.get("planning_power_kw")), finite(physical.get("kw"))
            received = finite(physical.get("received_at"))
            applicable = bool(self.control.owns(vehicle_id) and self.control.owned_manual
                and self.control.pending is None and row.get("would_control_mode") == "Manual"
                and switch and switch.state == "on" and physical.get("fresh") and physical.get("independent")
                and expected is not None and expected > 0 and actual is not None and received is not None)
            if not applicable or actual >= expected*.7:
                self._delivery_low.pop(vehicle_id,None)
                row["delivery"] = {"state":"on_rate" if applicable else "not_observed"}
                continue
            previous = self._delivery_low.get(vehicle_id)
            if previous is None or now < previous["since"]:
                previous = {"since":now,"last_report":received,"samples":1}
                self._delivery_low[vehicle_id] = previous
            elif received > previous["last_report"]:
                previous.update(last_report=received,samples=previous["samples"]+1)
            warning = previous["samples"] >= 3 and now-previous["since"] >= 120
            remaining = finite((row.get("session") or {}).get("remaining_kwh"))
            departure = finite(row.get("departure"))
            finish = now+max(0,remaining)/actual*3600 if remaining is not None and actual > .05 else None
            row["delivery"] = {"state":"below_capacity" if warning else "observing",
                "observed_kw":actual,"planning_kw":expected,"since":previous["since"],
                "distinct_samples":previous["samples"],
                "continuous_finish_at_observed_rate":dt_util.utc_from_timestamp(finish).isoformat() if finish is not None else None,
                "departure_lateness_seconds":max(0,finish-departure) if finish is not None and departure is not None else None}
            if warning:
                row["warnings"].append("delivered_power_below_planning_capacity")

    async def async_set_control_enabled(self, enabled: bool) -> None:
        """Persist the master; enabling alone cannot adopt a preview request."""
        if not enabled:
            self.control._stop_requested = True
        async with self._lock:
            await self.control.set_enabled(enabled)
            if enabled:
                self.control._stop_requested = False
        await self.async_request_refresh()

    async def async_control_stop(self, vehicle_id: str, reason: str = "explicit_stop") -> None:
        """Revoke resume rights before requesting any negative hardware action."""
        if self.control.owns(vehicle_id, terminal=True):
            self.control._stop_requested = True
        async with self._lock:
            self.control.block_idle_for_connection()
            request = self.sessions.get(vehicle_id)
            if request:
                request.stop(reason)
            await self._save(immediate=True)
        await self.async_request_refresh()

    async def _observe(self) -> dict[str, Any]:
        """Only reads HA sources and saves preview-owned state."""
        from .sources import price_rows

        now = dt_util.utcnow().timestamp()
        profiles = self.profiles
        if self.control.grant:
            removed = self.control.grant["vehicle_id"]
            if removed not in profiles and removed in self.sessions:
                self.sessions[removed].stop("vehicle_profile_removed")
        self.sessions = {k: s for k, s in self.sessions.items() if k in profiles or self.control.grant and k == self.control.grant["vehicle_id"]}
        self.values = {k: v for k, v in self.values.items() if k in profiles}
        shared = dict(self.entry.data)
        rows, price_warnings = price_rows(self.hass, shared, now)
        export_known = export_is_known(price_warnings)
        if self._uncertain_unplug_at is not None and now-self._uncertain_unplug_at >= 5:
            plug_now = state(self.hass,shared.get("plug_entity"))
            charger_now = state(self.hass,shared.get("charger_connectivity_entity"))
            if plug_now and plug_now.state == "off" and (not shared.get("charger_connectivity_entity") or charger_now and charger_now.state == "on"):
                self._unplug_edges.append(self._uncertain_unplug_at)
                self._uncertain_unplug_at = None
        for gap in self._connection_gaps:
            for request in self.sessions.values():
                if not request.terminal and gap >= request.started_at-1:
                    request.connection_gap_since = request.connection_gap_since or gap
        self._connection_gaps.clear()
        for edge in self._unplug_edges:
            for request in self.sessions.values():
                if not request.terminal and edge >= request.started_at-1:
                    request.stop("physical_charger_unplug_edge")
            self.control.physical_unplug_observed()
        self._unplug_edges.clear()
        meter = meter_kwh(self.hass, shared.get("meter_entity"), now)
        meter_observation = measurement(self.hass,shared.get("meter_entity"))
        native, native_token = self._native()
        native_candidates = [v for v, p in profiles.items() if native and native.get("v") == p["kind"]]
        active = next((s for s in self.sessions.values() if not s.terminal), None)
        if active and active.source_token:
            if native == {} or native_token is not None and native_token != active.source_token:
                active.stop("observed_native_request_ended_or_replaced")
                active = None
        result: dict[str, Any] = {"control_enabled": False, "read_only": True, "updated_at": dt_util.utcnow().isoformat(), "vehicles": {}, "physical": physical_power(self.hass, shared, now), "meter_kwh": meter, "native_session": native, "native_token": native_token}
        result["price_feed_valid"] = price_feed_live(self.hass, shared, now, rows, price_warnings)
        result["meter_observed_at"] = meter_observation.received_at
        result["economic_policy"] = idle_green_policy(rows,now,self.hass.config.time_zone,
            enabled=bool(shared.get("economic_enabled") and shared.get("idle_green_price_filter")),export_known=export_known)
        result["economic_policy"]["warnings"] = list(price_warnings)
        for vehicle_id in self.profiles:
            profile = self.profile(vehicle_id)
            snapshot = vehicle_snapshot(self.hass, profile, shared)
            row: dict[str, Any] = {
                "vehicle_id": vehicle_id, "name": profile["name"],
                "maximum_soc":profile.get("maximum_soc",100),
                "observations": asdict(snapshot), "control_enabled": False,
                "eligible_for_boost": False, "would_control_mode": "Green",
                "plan_provisional": True, "plan": None,
                "target_km": None, "departure": None,
                "value_sources": {"target": "preview_override" if "target_km" in profile else "bound_helper", "departure": "preview_override" if "departure" in profile else "bound_helper"},
            }
            request = self.sessions.get(vehicle_id)
            if request and not request.terminal:
                gateway = state(self.hass,shared.get("charger_connectivity_entity"))
                if shared.get("charger_fault_recovery") and shared.get("charger_connectivity_entity") and (not gateway or gateway.state != "on") and self.control.owns(vehicle_id):
                    # An offline EVSE's cached false/token change is not an
                    # observed cable removal. Preserve the allowance and wait.
                    snapshot = replace(snapshot,connected=None,shared_connected=None,connection_id=None,connection_measured_at=None)
                    row["connection_recovery"] = "gateway_unavailable_request_preserved"
                if request.connection_gap_since is not None and snapshot.shared_connected is True:
                    if snapshot.connection_id == request.connection_id or snapshot.connected is True and snapshot.home is not False and snapshot.soc.measured_at is not None and snapshot.soc.measured_at >= request.connection_gap_since:
                        request.connection_gap_since = None
                    else:
                        snapshot = replace(snapshot,connected=None,shared_connected=None,connection_id=None,connection_measured_at=None)
                        row["connection_recovery"] = "attachment_gap_waiting_vehicle_evidence_request_preserved"
                if (shared.get("charger_fault_recovery") and self.control.owns(vehicle_id) and snapshot.shared_connected is True
                        and snapshot.connected is True and snapshot.home is not False and snapshot.connection_id
                        and request.connection_id != snapshot.connection_id
                        and not any(vehicle_snapshot(self.hass,self.profile(other),shared).connected is True for other in profiles if other != vehicle_id)):
                    request.refresh_charger_token(snapshot.connection_id)
                    self.control.refresh_attachment_token(snapshot.connection_id)
                if self._uncertain_unplug_at is not None and snapshot.shared_connected is False:
                    snapshot = replace(snapshot,connected=None,shared_connected=None,connection_id=None,connection_measured_at=None)
                    row["connection_recovery"] = "unqualified_off_waiting_persistence"
                request.observe_connection(now, snapshot, trust_attachment=self.control.owns(vehicle_id))
                if request.terminal:
                    if native and native.get("v") == profile["kind"]:
                        self.suppressed_token = native_token
                    elif request.source_token:
                        self.suppressed_token = request.source_token
                    active = None
            planning_power = None
            try:
                capacity = planning_capacity(self.hass,shared,profile)
                planning_power = capacity["power_kw"]
                row["planning_power_kw"] = planning_power
                row["planning_capacity"] = capacity
                departure = self.departure(profile)
                row["departure"] = departure
                if request and not request.terminal and request.departure != departure:
                    request.set_departure(departure)
                target = self.target(profile)
                row["target_km"] = target
                cfg = self._config(vehicle_id, profile)
                calculation = snapshot
                try:
                    need = demand(cfg, calculation, target, now)
                    if need.source_fresh:
                        self.references[vehicle_id] = (snapshot.soc.value,snapshot.battery_kwh.value,snapshot.range_km.value,snapshot.limit_soc.value,snapshot.soh.value)
                        if need.measured_at is not None:
                            self.reference_times[vehicle_id] = need.measured_at
                except ValueError:
                    reference = request.reference_values if request and request.reference_values is not None else self.references.get(vehicle_id)
                    if not request or not self.control.owns(vehicle_id) or reference is None:
                        raise
                    replacements = {key: replace(getattr(snapshot, key), value=reference[i], unit=unit, measured_at=None) for i,(key,unit) in enumerate((("soc","%"),("battery_kwh","kWh"),("range_km","km"),("limit_soc","%"),("soh","%")))}
                    if snapshot.limit_soc.unit == "%" and finite(snapshot.limit_soc.value) is not None and 0 < snapshot.limit_soc.value <= 100:
                        replacements["limit_soc"] = snapshot.limit_soc
                    calculation = replace(snapshot, **replacements)
                    need = demand(cfg, calculation, target, now)
                    row["calculation_from_saved_reference"] = True
                    captured = request.reference_measured_at or self.reference_times.get(vehicle_id)
                    row["saved_reference_measured_at"] = captured
                    row["saved_reference_age_seconds"] = max(0,now-captured) if captured is not None else None
                if native and len(native_candidates) == 1 and native_token != self.suppressed_token and not active and native.get("v") == profile["kind"] and snapshot.connected is True:
                    self.generation += 1
                    active = Session(vehicle_id, self.generation, snapshot.connection_id, now, target, departure, source_token=native_token)
                    self.sessions[vehicle_id] = active
                request = self.sessions.get(vehicle_id)
                estimated = need.needed_kwh
                if request and not request.terminal:
                    controlled = self.control.owns(vehicle_id)
                    if request.target_km != target or request.departure != departure:
                        old_need = demand(cfg, calculation, request.target_km, now)
                        request.set_target(target, departure, need, old_need, allow_estimated=controlled)
                    if controlled and request.anchor_signature and request.anchor_need is not None and request.anchor_signature[3] != calculation.limit_soc.value:
                        old_limit = request.anchor_signature[3]
                        if old_limit is not None:
                            old_need = demand(cfg, replace(calculation, limit_soc=replace(calculation.limit_soc, value=old_limit)), target, now)
                            request.anchor_need += need.needed_kwh - old_need.needed_kwh
                            request.anchor_signature = (*request.anchor_signature[:3], calculation.limit_soc.value)
                    accounting_meter = meter if meter is not None else request.last_meter if controlled and shared.get("charger_fault_recovery") else None
                    if meter is None and accounting_meter is not None:
                        row["meter_credit_delayed"] = True
                    estimated_value = request.update(now, calculation, need, accounting_meter, cfg.droop_pp, meter_id=shared.get("meter_entity"), allow_estimated=controlled)
                    if estimated_value is not None:
                        estimated = estimated_value
                    if request.terminal and request.source_token:
                        self.suppressed_token = request.source_token
                        active = None
                prediction = self._plan(now,departure,estimated,need.band_kwh if need.source_fresh else 0,rows,planning_power,request,export_known)
                prediction_data = prediction.as_dict()
                if "export_price_unknown_cost_estimate_incomplete" in price_warnings and prediction.expected_solar_kwh > 0:
                    prediction_data["cost"] = None
                    prediction_data["green_cost"] = None
                phase = request.phase if request else "idle"
                reason = request.reason if request else "advice_only_no_request"
                if request and not request.terminal and phase == "scheduled" and prediction.reason == "small_remainder_within_tolerance":
                    request.accept_tolerance(snapshot.soc.value)
                    phase, reason = "within_tolerance", prediction.reason
                elif request and not request.terminal and phase == "scheduled" and prediction.mode == "Manual":
                    phase, reason = "would_boost", prediction.reason
                eligible = bool(request and not request.terminal and (need.source_fresh or self.control.owns(vehicle_id)) and estimated_value is not None and phase in {"scheduled", "would_boost"}) if request else False
                row.update({"target_km": target, "departure": departure, "demand": asdict(need), "plan": prediction_data, "plan_provisional": not need.source_fresh or prediction.early_threshold_active, "eligible_for_boost": eligible, "would_control_mode": prediction.mode if eligible else "Green", "phase": phase, "reason": reason, "session": request.as_dict() if request else None, "warnings": list(dict.fromkeys([*need.warnings, *price_warnings, *prediction.warnings]))})
                if len(native_candidates) > 1 and vehicle_id in native_candidates:
                    row["warnings"].append("native_vehicle_identity_ambiguous")
                native_plan = state(self.hass, profile.get("native_plan_entity"))
                if native_plan:
                    row["comparison"] = {"native_mode": native_plan.attributes.get("control_mode"), "preview_mode": prediction.mode, "native_needed_kwh": native_plan.attributes.get("estimated_kwh"), "native_updated_at": native_plan.attributes.get("updated_at"), "same_mode": native_plan.attributes.get("control_mode") == prediction.mode}
            except (ValueError, TypeError) as exc:
                ended = bool(request and request.terminal)
                row.update({"phase": request.phase if ended else "waiting_data", "reason": request.reason if ended else str(exc), "warnings": list(dict.fromkeys([str(exc), *price_warnings])), "plan": None, "session": request.as_dict() if request else None})
                # A temporary missing vehicle value cannot erase an owned
                # allowance or require a new VW update after an HA restart.
                if planning_power is not None and request and self.control.owns(vehicle_id) and request.observe_connection(now, snapshot, trust_attachment=True) and meter is not None and request.meter_id == shared.get("meter_entity"):
                    remaining = request.consume_meter(meter)
                    if remaining is not None:
                        prediction = self._plan(now,request.departure,remaining,0,rows,planning_power,request,export_known)
                        row.update(phase=request.phase, reason=request.reason, target_km=request.target_km, departure=request.departure, plan=prediction.as_dict(), eligible_for_boost=request.phase == "scheduled", would_control_mode=prediction.mode if request.phase == "scheduled" else "Green", session=request.as_dict())
            result["vehicles"][vehicle_id] = row
        result["active_vehicle"] = next((s.vehicle_id for s in self.sessions.values() if not s.terminal), None)
        await self._save()
        return result

    async def async_preview_action(self, action: str, vehicle_id: str, data: dict[str, Any]) -> None:
        """Change own commands; the optional executor alone can actuate afterward."""
        if action == "stop" and self.control.owns(vehicle_id, terminal=True):
            await self.async_control_stop(vehicle_id)
            return
        async with self._lock:
            await self.control.drain_events()
            if action not in {"start", "stop", "set_target", "follow_helpers"}:
                raise ValueError("Unknown preview action")
            if vehicle_id not in self.profiles:
                raise ValueError("Unknown preview vehicle")
            overrides = dict(self.values.get(vehicle_id, {}))
            if action == "follow_helpers":
                overrides.pop("target_km", None)
                overrides.pop("departure", None)
                candidate = {**self.profiles[vehicle_id], **overrides}
                try:
                    self.target(candidate)
                    self.departure(candidate)
                except ValueError as exc:
                    raise ValueError("No valid bound target/departure helper to follow") from exc
            if "target_range_km" in data:
                value = finite(data["target_range_km"])
                if value is None or not 0 < value <= 2000:
                    raise ValueError("Invalid target range")
                overrides["target_km"] = value
            if "departure" in data:
                parsed = None if data["departure"] in (None, "now") else local_time(data["departure"], self.hass.config.time_zone)
                if parsed is None and data["departure"] not in (None, "now"):
                    raise ValueError("Invalid departure")
                overrides["departure"] = parsed
            if "droop_pp" in data:
                value = finite(data["droop_pp"])
                if value is None or not 0.1 <= value <= 10:
                    raise ValueError("Invalid droop percentage points")
                overrides["droop_pp"] = value
            if action == "start":
                authority_snapshot = self.control.validate_start(vehicle_id)
                profile = {**self.profile(vehicle_id), **overrides}
                snapshot = vehicle_snapshot(self.hass, profile, dict(self.entry.data))
                if snapshot.connected is not True and not (self.control.enabled and snapshot.shared_connected is True and snapshot.connection_id):
                    raise ValueError("Preview start requires selected vehicle connection")
                target, departure = self.target(profile), self.departure(profile)
                self.generation += 1
                existing = self.sessions.get(vehicle_id)
                if existing and not existing.terminal and (snapshot.connection_id is None or existing.connection_id == snapshot.connection_id):
                    # Repeated Start on this connection isn't a fresh energy allowance.
                    existing.generation = self.generation
                    existing.source_token = None
                else:
                    for request in self.sessions.values():
                        if not request.terminal:
                            request.stop("replaced_by_preview_request")
                    self.sessions[vehicle_id] = Session(vehicle_id, self.generation, snapshot.connection_id, dt_util.utcnow().timestamp(), target, departure)
                    self.sessions[vehicle_id].reference_values = existing.reference_values if existing and existing.reference_values else self.references.get(vehicle_id)
                    self.sessions[vehicle_id].reference_measured_at = existing.reference_measured_at if existing else self.reference_times.get(vehicle_id)
            elif action == "stop":
                if not any(not s.terminal for s in self.sessions.values()) or self.control.owns(vehicle_id,terminal=True):
                    self.control.block_idle_for_connection()
                request = self.sessions.get(vehicle_id)
                native, token = self._native()
                if native and native.get("v") == self.profiles[vehicle_id]["kind"]:
                    self.suppressed_token = token
                if request:
                    if request.source_token:
                        self.suppressed_token = request.source_token
                    request.stop()
                self.generation += 1
            self.values[vehicle_id] = overrides
            if action == "start":
                self.control.assign_authority(vehicle_id, authority_snapshot)
            await self._save(immediate=True)
        await self.async_request_refresh()
