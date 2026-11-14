"""Single charger executor; disabled means no new hardware authority.

Only this module calls actuator services. A preview mirror never grants control.
Physical zero power and anonymous OFF are deliberately not cancellation inputs.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers import entity_registry as er, device_registry as dr, area_registry as ar
from homeassistant.util import dt as dt_util

from .core.models import finite, timestamp
from .core.planner import deadline_fallback
from .const import DOMAIN
from .sources import connection_identity, physical_power, price_feed_live, state, vehicle_snapshot

if TYPE_CHECKING:
    from .coordinator import PreviewCoordinator

FEEDBACK_TIMEOUT = 120
SERVICE_TIMEOUT = 20
CONTROL_INTERVAL = 10
DEFAULT_COMMAND_KWH = 30.0
MAX_MANUAL_SECONDS = 8 * 3600
START_SETTLE_SECONDS = 120
BINDING_KEYS = ("control_mode_entity", "charger_switch_entity", "control_feedback_entity", "control_report_entity", "meter_entity", "power_entity", "fallback_power_entity", "plug_entity", "connection_entity", "connection_attribute", "legacy_master_entity", "session_entity", "active_entity", "pending_entity", "generation_entity")
VEHICLE_BINDING_KEYS = ("kind", "cable_entity", "vehicle_charge_switch_entity", "limit_writer_entity")
TRANSIENT_INTERLOCKS = {"waiting_legacy_request_state", "waiting_legacy_controller_state", "control_bindings_incomplete", "waiting_actuator_source"}
_LOGGER = logging.getLogger(__name__)


class DispatchRefused(ValueError):
    """A validation refusal before any hardware service was dispatched."""


class ChargerControl:
    """Serialize service intent/readback under the coordinator's existing lock."""

    def __init__(self, coordinator: PreviewCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.enabled = False
        self.grant: dict[str, Any] | None = None
        self.owned_manual = False
        self.pending: dict[str, Any] | None = None
        self.limit: dict[str, Any] | None = None
        self.dormant_limits: list[dict[str, Any]] = []
        self.fault: str | None = None
        self.status = "disabled"
        self._timer = None
        self._interval = None
        self._bus = None
        self._contexts: set[str] = set()
        self._closed = False
        self._restored = False
        self._stop_requested = False
        self._cleanup_connection: str | None = None
        self._notified_fault: str | None = None
        self._notified_status: str | None = None
        self._events: list[dict[str, Any]] = []
        self._external_mode = False
        self._external_limit_entities: set[str] = set()
        self._cleanup_required = False
        self._interlock_fault: str | None = None
        self.limit_audit: dict[str, Any] | None = None
        self._interlock_since: float | None = None
        self._connection_unknown_since: float | None = None
        self.green_hold: dict[str, Any] | None = None
        self.idle_blocked = False
        self._charger_outage = False
        self.pause_audit: dict[str,Any] | None = None
        self._green_interrupt = False

    @property
    def config(self) -> dict[str, Any]:
        """Read configured entities only; no guessed hardware targets."""
        return dict(self.coordinator.entry.data)

    def restore(self, data: Any) -> None:
        """Restore explicit authority, never infer it from an old preview."""
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        grant = data.get("grant")
        number = lambda value: type(value) in (int, float) and finite(value) is not None
        if grant is not None and (not isinstance(grant, dict) or not isinstance(grant.get("vehicle_id"), str) or type(grant.get("generation")) is not int or grant["generation"] < 1 or not isinstance(grant.get("connection_id"), str) or not grant["connection_id"] or not number(grant.get("manual_seconds")) or grant["manual_seconds"] < 0 or not number(grant.get("max_kwh")) or not 0.1 <= grant["max_kwh"] <= 100 or not isinstance(grant.get("binding"), dict) or grant.get("manual_since") is not None and not number(grant["manual_since"])):
            self.fault = "invalid_stored_control_authority"
            return
        if grant is not None and (grant.get("legacy_generation") is not None and not isinstance(grant["legacy_generation"], str) or type(grant.get("limit_overridden", False)) is not bool):
            self.fault = "invalid_stored_control_authority"
            return
        if grant is not None and grant.get("start_watch_since") is not None and not number(grant["start_watch_since"]):
            self.fault = "invalid_stored_control_authority"
            return
        invalid_exposure = bool(grant is not None and any(k in grant and (not number(grant[k]) or grant[k] < 0) for k in ("unmetered_exposure_kwh", "cap_checked_at", "last_known_used_kwh", "exposure_fault_report_at")))
        pending, limit = data.get("pending"), data.get("limit")
        if pending is not None and (not isinstance(pending, dict) or pending.get("kind") not in {"manual", "stop", "green"} or not number(pending.get("sent")) or pending["sent"] <= 0 or "mode_ack_at" in pending and not number(pending["mode_ack_at"]) or type(pending.get("attempts", 1)) is not int or not 1 <= pending.get("attempts", 1) <= 3 or not number(pending.get("last_attempt", pending["sent"])) or type(pending.get("recovery", False)) is not bool or pending.get("recovery") and pending["kind"] != "manual"):
            self.fault = "invalid_stored_control_operation"
            return
        dormant = data.get("dormant_limits", [])
        def valid_limit(item: Any) -> bool:
            return isinstance(item, dict) and isinstance(item.get("vehicle_id"), str) and isinstance(item.get("entity_id"), str) and item["entity_id"].startswith("number.") and item.get("vehicle_binding") == {"kind": "tesla", "limit_writer_entity": item["entity_id"]} and isinstance(item.get("writer_identity"), list) and all(number(item.get(k)) and 1 <= item[k] <= 100 for k in ("original", "written")) and item["original"] <= item["written"] and number(item.get("sent")) and type(item.get("pending")) is bool and type(item.get("restore_pending", False)) is bool and (not item.get("restore_pending") or number(item.get("restore_sent")))
        if not isinstance(dormant, list) or len(dormant) > 32 or any(not valid_limit(item) for item in dormant) or limit is not None and (not valid_limit(limit) or grant is None or limit["vehicle_id"] != grant["vehicle_id"]):
            self.fault = "invalid_stored_limit_ownership"
            return
        all_limits = [*dormant, *([limit] if limit else [])]
        if len({item["vehicle_id"] for item in all_limits}) != len(all_limits):
            self.fault = "conflicting_stored_limit_ownership"
            return
        hold = data.get("green_hold")
        invalid_hold = False
        if hold is not None:
            if not isinstance(hold,dict) or not isinstance(hold.get("connection_id"),str) or not hold["connection_id"] or not isinstance(hold.get("binding"),dict) or set(hold["binding"]) != set(BINDING_KEYS) or hold.get("stage") not in {"pausing","paused","resume_mode","resume_switch"} or not number(hold.get("sent")) or type(hold.get("attempts",1)) is not int or not 1 <= hold.get("attempts",1) <= 3 or not number(hold.get("last_attempt",hold["sent"])) or pending is not None or data.get("owned_manual") is True or hold.get("pre_pause_on") is not True or type(hold.get("off_confirmed")) is not bool:
                invalid_hold = True
            elif type(hold.get("off_dispatched")) is not bool or hold.get("off_confirmed") and not hold.get("off_dispatched"):
                invalid_hold = True
            else:
                self.green_hold = deepcopy(hold)
        self.enabled = data.get("enabled") is True and not invalid_hold and not invalid_exposure
        self.idle_blocked = data.get("idle_blocked") is True
        self._charger_outage = data.get("charger_outage") is True
        self.grant = deepcopy(grant)
        if self.grant:
            self.grant["binding"].setdefault("fallback_power_entity",None)
            if "cap_checked_at" in self.grant:
                self.grant["cap_checked_at"] = dt_util.utcnow().timestamp()
        if self.grant and self.grant.get("unpriced_departure") is not None:
            hint = timestamp(self.grant["unpriced_departure"])
            if hint is None:
                self.grant.pop("unpriced_departure", None)  # Discard the hint, not the valid request.
            else:
                self.grant["unpriced_departure"] = hint
        self.owned_manual = data.get("owned_manual") is True and grant is not None
        self.pending = deepcopy(pending)
        self.limit = deepcopy(limit)
        self.dormant_limits = deepcopy(dormant)
        self._cleanup_connection = data.get("cleanup_connection") if isinstance(data.get("cleanup_connection"), str) else None
        self.fault = data.get("fault") if isinstance(data.get("fault"), str) else None
        if invalid_hold:
            self.fault = "invalid_stored_green_pause"
        elif invalid_exposure:
            self.fault = "invalid_stored_exposure_bound"
        self.pause_audit = deepcopy(data.get("pause_audit")) if isinstance(data.get("pause_audit"),dict) else None
        if invalid_hold:
            self.pause_audit = {"retired_reason":"invalid_stored_green_pause", "rejected_hold":deepcopy(hold),
                                "charger_may_be_off":True,"retired_at":dt_util.utcnow().timestamp()}
        self._green_interrupt = self.idle_blocked or data.get("green_interrupt") is True
        self._restored = grant is not None
        self._cleanup_required = data.get("cleanup_required") is True
        self._interlock_fault = data.get("interlock_fault") if isinstance(data.get("interlock_fault"), str) else None
        self.limit_audit = deepcopy(data["limit_audit"]) if valid_limit(data.get("limit_audit")) else None
        self._interlock_since = finite(data.get("interlock_since"))
        self._connection_unknown_since = finite(data.get("connection_unknown_since"))

    def as_dict(self) -> dict[str, Any]:
        """Return the persisted authority and bounded asynchronous operation."""
        return {"version": 1, "enabled": self.enabled, "grant": deepcopy(self.grant), "owned_manual": self.owned_manual, "pending": deepcopy(self.pending), "limit": deepcopy(self.limit), "dormant_limits": deepcopy(self.dormant_limits), "fault": self.fault, "cleanup_connection": self._cleanup_connection, "cleanup_required": self._cleanup_required, "interlock_fault": self._interlock_fault, "limit_audit": deepcopy(self.limit_audit), "interlock_since": self._interlock_since, "connection_unknown_since": self._connection_unknown_since, "green_hold":deepcopy(self.green_hold), "idle_blocked":self.idle_blocked, "charger_outage":self._charger_outage,"pause_audit":deepcopy(self.pause_audit),"green_interrupt":self._green_interrupt}

    def block_idle_for_connection(self) -> None:
        """Explicit Stop also prevents an idle economic pause from resuming."""
        self.idle_blocked = True
        self._green_interrupt = True
        if self.green_hold:
            self.green_hold["retire_reason"] = "explicit_stop_preserved"

    def physical_unplug_observed(self) -> None:
        """End the old command, retain the charger's own Green cleanup debt."""
        if self.green_hold:
            self.green_hold.update(release_required=True,release_reason="physical_unplug")
        self.idle_blocked = False
        self._green_interrupt = False

    async def _retire_green_pause(self, reason: str, *, notify: bool = False) -> None:
        if self.green_hold:
            self.pause_audit = {**deepcopy(self.green_hold),"retired_reason":reason,"retired_at":dt_util.utcnow().timestamp()}
            self.green_hold = None
            self.status = reason
            await self.coordinator._save(immediate=True)
            if notify:
                await self._notify_fault(reason)

    def refresh_attachment_token(self, token: str) -> None:
        """The coordinator corroborated continuity; retain generation and budget."""
        if self.grant:
            self.grant["connection_id"] = token
        if self.green_hold:
            self.green_hold["connection_id"] = token

    @property
    def has_limits(self) -> bool:
        return bool(self.limit or self.dormant_limits)

    def start_listeners(self) -> None:
        """Track explicit user commands without mistaking charger telemetry for Stop."""
        self._bus = self.hass.bus.async_listen("call_service", self._service_seen)
        self._interval = async_track_time_interval(self.hass, self._wake, timedelta(seconds=CONTROL_INTERVAL))

    @callback
    def _wake(self, _now: datetime) -> None:
        if not self._closed and (self.enabled or self.owned_manual or self.pending or self.green_hold):
            self.hass.async_create_task(self.coordinator.async_request_refresh())

    def _targets(self, service_data: dict[str, Any], entity_id: str | None) -> bool:
        """Resolve configured actuators only, including HA area/device targets."""
        if not entity_id:
            return False
        values = lambda key: [service_data[key]] if isinstance(service_data.get(key), str) else service_data.get(key, [])
        if entity_id in values("entity_id") or "all" in values("entity_id"):
            return True
        registry = self.hass.data.get(er.DATA_REGISTRY)
        item = registry.async_get(entity_id) if registry and hasattr(registry, "entities") else None
        if item is None:
            return False
        if item.device_id in values("device_id"):
            return True
        devices = self.hass.data.get(dr.DATA_REGISTRY)
        device = devices.async_get(item.device_id) if devices and item.device_id else None
        area_id = item.area_id or (device.area_id if device else None)
        areas = self.hass.data.get(ar.DATA_REGISTRY)
        area = areas.async_get_area(area_id) if areas and area_id else None
        labels = item.labels | (device.labels if device else set()) | (area.labels if area else set())
        return area_id in values("area_id") or bool(labels.intersection(values("label_id")))

    @callback
    def _service_seen(self, event: Event) -> None:
        if event.context.id in self._contexts:
            return
        data = event.data
        service_data = data.get("service_data") or {}
        charger_target = self._targets(service_data,self.config.get("charger_switch_entity"))
        mode_target = self._targets(service_data,self.config.get("control_mode_entity"))
        idle_override = self.config.get("economic_enabled") and ((data.get("domain") == "switch" and charger_target and event.context.user_id) or (data.get("domain") == "select" and data.get("service") == "select_option" and mode_target))
        if idle_override and (self.green_hold or not self.grant):
            self._green_interrupt = True  # Interrupt only; mutate authority under the lock.
            self._events.append({"kind":"idle_override"})
            self.hass.async_create_task(self._apply_service_events())
        affected = [item["entity_id"] for item in [*self.dormant_limits, *([self.limit] if self.limit else [])] if data.get("domain") == "number" and self._targets(service_data, item["entity_id"])]
        if affected:
            self._external_limit_entities.update(affected)
            self._events.append({"kind": "limit", "entities": affected})
            self.hass.async_create_task(self._apply_service_events())
            return
        if not self.grant:
            return
        binding = self.grant["binding"]
        switches = (binding.get("charger_switch_entity"), binding.get("vehicle", {}).get("vehicle_charge_switch_entity"))
        off = data.get("domain") == "switch" and (data.get("service") == "turn_off" or data.get("service") == "toggle" and any(self._targets(service_data, e) and state(self.hass, e) and state(self.hass, e).state == "on" for e in switches))
        kind = None
        if data.get("domain") == "switch" and off and any(self._targets(service_data, e) for e in switches) and event.context.user_id:
            self._stop_requested = True  # Immediate interrupt before any await.
            kind = "stop"
        elif data.get("domain") == "select" and data.get("service") == "select_option" and self._targets(service_data, binding.get("control_mode_entity")):
            self._external_mode = True
            kind = "mode"
        if kind:
            self._events.append({"kind": kind, "vehicle_id": self.grant["vehicle_id"], "generation": self.grant["generation"]})
            self.hass.async_create_task(self._apply_service_events())

    async def _apply_service_events(self) -> None:
        async with self.coordinator._lock:
            await self.drain_events()
        self._wake(dt_util.utcnow())

    async def drain_events(self) -> None:
        """Run under the coordinator lock; callbacks never mutate authority."""
        while self._events:
            event = self._events.pop(0)
            if event["kind"] == "idle_override":
                self.block_idle_for_connection()
                await self._retire_green_pause("explicit_foreign_green_control_preserved",notify=True)
                await self.coordinator._save(immediate=True)
                continue
            if event["kind"] == "limit":
                for item in [*self.dormant_limits, *([self.limit] if self.limit else [])]:
                    if item["entity_id"] in event["entities"]:
                        if self.grant and self.grant["vehicle_id"] == item["vehicle_id"]:
                            self.grant["limit_overridden"] = True
                        self._retire_limit("external_limit_command", item)
                await self.coordinator._save(immediate=True)
                continue
            if not self.grant or any(self.grant[k] != event[k] for k in ("vehicle_id", "generation")):
                continue
            if event["kind"] == "stop":
                request = self.coordinator.sessions.get(event["vehicle_id"])
                if request:
                    request.stop("explicit_user_charger_stop")
            elif event["kind"] == "mode":
                self.fault = "external_mode_command_requires_new_start"
                self.owned_manual, self.pending = False, None
            await self.coordinator._save(immediate=True)

    def _retire_limit(self, reason: str, obligation: dict[str, Any] | None = None) -> None:
        obligation = self.limit if obligation is None else obligation
        if obligation:
            self.limit_audit = {**obligation, "retired_reason": reason}
            self._forget_limit(obligation)

    def _forget_limit(self, obligation: dict[str, Any]) -> None:
        if obligation is self.limit:
            self.limit = None
        else:
            self.dormant_limits = [item for item in self.dormant_limits if item is not obligation]

    def _binding(self, vehicle_id: str) -> dict[str, Any]:
        """Pin writer identities and the measurements that qualify their use."""
        cfg = self.config
        profile = self.coordinator.profiles.get(vehicle_id, {})
        return {**{k: cfg.get(k) for k in BINDING_KEYS}, "vehicle": {k: profile.get(k) for k in VEHICLE_BINDING_KEYS}}

    def binding_change_blocked(self, data: dict[str, Any], vehicle_id: str | None = None) -> bool:
        if self.green_hold and vehicle_id is None and any(data.get(k) != self.green_hold["binding"].get(k) for k in BINDING_KEYS):
            return True
        if not self.grant or not (self.owned_manual or self.pending or self.limit):
            return False
        old = self.grant["binding"]
        if vehicle_id is not None:
            return vehicle_id == self.grant["vehicle_id"] and any(data.get(k) != old["vehicle"].get(k) for k in VEHICLE_BINDING_KEYS)
        return any(data.get(k) != old.get(k) for k in BINDING_KEYS)

    def foreign_reason(self) -> str | None:
        """Positive foreign ownership outranks all cleanup, even after drift."""
        for cfg in (self.config, self.grant["binding"] if self.grant else {}):
            master = state(self.hass, cfg.get("legacy_master_entity"))
            if master and master.state == "on":
                return "legacy_controller_not_disabled"
            for key, expected in (("active_entity", "off"), ("session_entity", "{}"), ("pending_entity", "{}")):
                source = state(self.hass, cfg.get(key))
                if source and source.state not in {expected, "unknown", "unavailable"}:
                    return "another_controller_request"
        return None

    def interlock(self, *, check_grant: bool = True) -> str | None:
        """A stable disabled legacy Master has no age limit."""
        cfg = self.config
        if check_grant and self.grant:
            current, saved = self._binding(self.grant["vehicle_id"]), self.grant.get("binding", {})
            if self.grant["vehicle_id"] not in self.coordinator.profiles:
                current, saved = {k: v for k,v in current.items() if k != "vehicle"}, {k: v for k,v in saved.items() if k != "vehicle"}
            if saved != current:
                return "control_binding_changed_requires_new_start"
        for key, prefix in (("control_mode_entity", "select."), ("charger_switch_entity", "switch."), ("control_feedback_entity", "sensor."), ("control_report_entity", "sensor."), ("legacy_master_entity", "input_boolean.")):
            entity_id = cfg.get(key)
            source = state(self.hass, entity_id)
            if not isinstance(entity_id, str) or not entity_id.startswith(prefix) or source is None or source.state in {"unknown", "unavailable"}:
                return "control_bindings_incomplete"
        if state(self.hass, cfg["legacy_master_entity"]).state not in {"off", "on"}:
            return "waiting_legacy_controller_state"
        if state(self.hass, cfg["legacy_master_entity"]).state == "on":
            return "legacy_controller_not_disabled"
        for key, expected in (("active_entity", "off"), ("session_entity", "{}"), ("pending_entity", "{}")):
            source = state(self.hass, cfg.get(key))
            if source is None or source.state in {"unknown", "unavailable"}:
                return "waiting_legacy_request_state"
            if source.state != expected:
                return "another_controller_request"
        return None

    async def set_enabled(self, enabled: bool) -> None:
        """Changing the Master alone never starts a new request."""
        if enabled and (reason := self.interlock(check_grant=self.owned_manual)):
            raise ValueError(reason)
        self.enabled = enabled
        await self.coordinator._save(immediate=True)

    def validate_start(self, vehicle_id: str) -> Any:
        """Validate synchronously before the coordinator mutates a command."""
        if not self.enabled:
            if self.owned_manual or self.pending:
                raise ValueError("finish_previous_owned_manual_first")
            return None
        if reason := self.interlock(check_grant=self.owned_manual):
            raise ValueError(reason)
        snapshot = vehicle_snapshot(self.hass, self.coordinator.profile(vehicle_id), self.config)
        if snapshot.shared_connected is not True or not snapshot.connection_id:
            raise ValueError("selected_vehicle_not_connected")
        if self.owned_manual and self.grant and self.grant["vehicle_id"] != vehicle_id:
            raise ValueError("finish_previous_owned_manual_first")
        if self._cleanup_required or any(item.get("vehicle_id") == vehicle_id and item.get("restore_pending") for item in [*self.dormant_limits, *([self.limit] if self.limit else [])]):
            raise ValueError("finish_previous_cleanup_first")
        return snapshot

    def assign_authority(self, vehicle_id: str, snapshot: Any) -> None:
        """Commit the already validated Start without an intervening await."""
        if snapshot is None:
            return
        request = self.coordinator.sessions[vehicle_id]
        old = self.grant if self.grant and self.grant["vehicle_id"] == vehicle_id and self.grant["connection_id"] == snapshot.connection_id else {}
        if self.limit and self.limit["vehicle_id"] != vehicle_id:
            self.dormant_limits.append(self.limit)
            self.limit = None
        returning = next((item for item in self.dormant_limits if item["vehicle_id"] == vehicle_id), None)
        if returning:
            self.dormant_limits.remove(returning)
            self.limit = returning
        self.grant = {"vehicle_id": vehicle_id, "generation": request.generation, "connection_id": snapshot.connection_id, "manual_seconds": old.get("manual_seconds", 0.0), "manual_since": old.get("manual_since"), "max_kwh": float(self.config.get("maximum_command_kwh", DEFAULT_COMMAND_KWH))}
        self.grant["binding"] = self._binding(vehicle_id)
        self.grant["start_watch_since"] = old.get("start_watch_since")
        native_generation = state(self.hass, self.config.get("generation_entity"))
        self.grant["legacy_generation"] = native_generation.state if native_generation else None
        self.fault = None
        self._notified_fault = None
        self._notified_status = None
        self._stop_requested = False
        self._restored = False
        self._external_mode = False
        self.idle_blocked = False
        self._green_interrupt = False
        self._external_limit_entities.discard(self.coordinator.profile(vehicle_id).get("limit_writer_entity"))

    async def authorize(self, vehicle_id: str) -> None:
        """Compatibility helper; production Start validates before mutation."""
        self.assign_authority(vehicle_id, self.validate_start(vehicle_id))
        await self.coordinator._save(immediate=True)

    def owns(self, vehicle_id: str, *, terminal: bool = False) -> bool:
        """Generation and attachment bind every callback, not just vehicle name."""
        request = self.coordinator.sessions.get(vehicle_id)
        return bool(self.grant and request and self.grant["vehicle_id"] == vehicle_id and self.grant["generation"] == request.generation and self.grant["connection_id"] == request.connection_id and (terminal or not request.terminal))

    def _same_attachment(self) -> bool:
        return bool(self.grant and self._connection_id() == self.grant["connection_id"])

    def _connection_id(self) -> str | None:
        return connection_identity(self.hass, self.config)

    def _mode_confirmed(self, option: str, sent: float | None = None) -> bool:
        cfg = self.grant["binding"] if self.owned_manual and self.grant else self.config
        selected, feedback = state(self.hass, cfg.get("control_mode_entity")), state(self.hass, cfg.get("control_feedback_entity"))
        reported = state(self.hass, cfg.get("control_report_entity"))
        report_time = timestamp(reported.state) if reported else None
        expected = "MANUAL_CHARGING" if option == "Manual" else "GREEN_CHARGING"
        switch = state(self.hass, cfg.get("charger_switch_entity"))
        power = physical_power(self.hass, cfg, dt_util.utcnow().timestamp())
        stopped = switch and switch.state == "off" and power["fresh"] and not power["positive"]
        recent = report_time is not None and -60 <= dt_util.utcnow().timestamp() - report_time <= 600
        if self.config.get("charger_fault_recovery"):
            # Stable cached mode is enough to send a new mode intent. Its
            # acknowledgment remains separate from proof of physical charging.
            changed_feedback = sent is not None and feedback and feedback.last_updated.timestamp() >= sent-2
            fresh_power = sent is not None and power["fresh"] and power["received_at"] >= sent-2
            recent = recent or sent is None or bool(changed_feedback or fresh_power)
        acknowledged = sent is None or report_time is not None and report_time >= sent-2 or stopped
        if self.config.get("charger_fault_recovery") and sent is not None:
            acknowledged = acknowledged or bool(feedback and feedback.last_updated.timestamp() >= sent-2 and feedback.attributes.get("effective_mode") == expected) or bool(power["fresh"] and power["received_at"] >= sent-2 and power["positive"])
        return bool(selected and selected.state == option and feedback and feedback.state not in {"unknown","unavailable"} and feedback.attributes.get("requested_mode") is None and feedback.attributes.get("preferred_mode") == expected and (feedback.attributes.get("effective_mode") == expected or stopped) and recent and acknowledged)

    def _running_manual_readback(self, now: float) -> bool:
        """Observe continuing Manual; this is not acknowledgment of a new write."""
        cfg = self.grant["binding"]
        selected = state(self.hass, cfg.get("control_mode_entity"))
        feedback = state(self.hass, cfg.get("control_feedback_entity"))
        power = physical_power(self.hass, cfg, now)
        return bool(power["fresh"] and power["positive"]
                    and (self.pending is None or power["received_at"] > self.pending["sent"])
                    and selected and selected.state == "Manual"
                    and feedback and feedback.state not in {"unknown", "unavailable"}
                    and feedback.attributes.get("requested_mode") is None
                    and feedback.attributes.get("preferred_mode") == "MANUAL_CHARGING"
                    and feedback.attributes.get("effective_mode") == "MANUAL_CHARGING")

    def continuing_manual(self, request: Any) -> bool:
        """Preserve the start of our current block, including its start handshake."""
        owned = bool(request and self.coordinator.sessions.get(request.vehicle_id) is request
                    and self.enabled and self.owned_manual and self.owns(request.vehicle_id)
                    and self._same_attachment() and not self._stop_requested and not self._cleanup_required
                    and not self.fault and not self._external_mode and not self.foreign_reason()
                    and (self.pending is None or self.pending.get("kind") == "manual"))
        if not owned:
            return False
        reason = self.interlock()
        return not reason or reason in TRANSIENT_INTERLOCKS

    def unpriced_started(self, request: Any) -> bool:
        """Keep one started fallback block contiguous, bound to its departure."""
        if not request or not self.owns(request.vehicle_id) or not self.grant or "unpriced_departure" not in self.grant:
            return False
        previous = self.grant["unpriced_departure"]
        if previous is None or request.departure is None:
            return previous is request.departure
        previous, current = finite(previous), finite(request.departure)
        return previous is not None and current is not None and abs(previous-current) <= 60

    def _unpriced_dispatch_due(self, now: float) -> bool:
        request = self.coordinator.sessions.get(self.grant["vehicle_id"]) if self.grant else None
        remaining = finite(request.remaining_kwh) if request else None
        if request is None or request.terminal or remaining is None:
            return False
        try:
            power = self.coordinator.planning_power(self.coordinator.profile(request.vehicle_id))
        except ValueError:
            return False
        return deadline_fallback(now, request.departure, max(0, remaining), power, started=self.unpriced_started(request)).mode == "Manual"

    async def _call(self, domain: str, service: str, entity_id: str, **values: Any) -> None:
        negative = (domain, service) == ("switch", "turn_off") or domain == "select" and values.get("option") == "Green"
        obligation = next((item for item in [*self.dormant_limits, *([self.limit] if self.limit else [])] if domain == "number" and entity_id == item["entity_id"] and values.get("value") == item["original"] and item.get("restore_pending")), None)
        restoring_limit = obligation is not None
        cfg = self.grant["binding"] if negative and self.owned_manual and self.grant else self.config
        holding = bool(self.green_hold and (self.green_hold["connection_id"] == self._connection_id() or self.green_hold.get("release_required")) and self.green_hold["binding"] == {k:self.config.get(k) for k in BINDING_KEYS})
        if self.green_hold and not self.owned_manual and self.green_hold["binding"] != {k:self.config.get(k) for k in BINDING_KEYS}:
            raise DispatchRefused("economic_pause_binding_changed")
        green_resuming = bool(holding and self.green_hold.get("stage") == "resume_switch" and self.green_hold.get("pre_pause_on") is True and self.green_hold.get("off_confirmed") is True and not self.idle_blocked and not self._green_interrupt and
                              self._mode_confirmed("Green") and domain == "switch" and service == "turn_on" and entity_id == cfg.get("charger_switch_entity"))
        profile = obligation["vehicle_binding"] if restoring_limit else self.coordinator.profiles.get(self.grant["vehicle_id"], {}) if self.grant else {}
        allowed = domain == "select" and service == "select_option" and entity_id == cfg.get("control_mode_entity") and values.get("option") in {"Green", "Manual"}
        allowed |= domain == "switch" and service in {"turn_on", "turn_off"} and entity_id == cfg.get("charger_switch_entity")
        allowed |= domain == "switch" and service == "turn_on" and profile.get("kind") == "tesla" and entity_id == profile.get("vehicle_charge_switch_entity")
        allowed |= domain == "number" and service == "set_value" and profile.get("kind") == "tesla" and entity_id == profile.get("limit_writer_entity") and finite(values.get("value")) is not None and 1 <= values["value"] <= 100
        if not allowed:
            raise DispatchRefused("actuator_target_outside_configured_allowlist")
        reason = self.foreign_reason() or ("external_mode_command" if self._external_mode and not restoring_limit else None)
        if reason:
            raise DispatchRefused(reason)
        if not negative and not restoring_limit and (reason := self.interlock(check_grant=not green_resuming)):
            raise DispatchRefused(reason)
        cleanup = negative and self.owned_manual and self.grant
        if not self._same_attachment() and not cleanup and not restoring_limit and not (holding and (negative or green_resuming)):
            raise DispatchRefused("attachment_changed_before_actuator_call")
        source = state(self.hass, entity_id)
        if negative and (source is None or source.state in {"unknown", "unavailable"} or not self.hass.services.has_service(domain, service)):
            raise DispatchRefused("waiting_actuator_source")
        positive = (domain, service) == ("switch", "turn_on") or domain == "select" and values.get("option") == "Manual" or domain == "number" and (source is None or finite(source.state) is None or values.get("value", 0) > finite(source.state))
        if positive and not restoring_limit and not green_resuming and not (self.grant and self.owns(self.grant["vehicle_id"])):
            raise DispatchRefused("positive_call_requires_active_request")
        if positive and self._stop_requested and not green_resuming:
            raise DispatchRefused("stop_precedes_positive_actuator_call")
        if positive and not restoring_limit and not green_resuming and not price_feed_live(self.hass, self.config, dt_util.utcnow().timestamp()):
            if not self._unpriced_dispatch_due(dt_util.utcnow().timestamp()):
                raise DispatchRefused("fallback_window_not_due")
        if domain == "number" and entity_id in self._external_limit_entities:
            raise DispatchRefused("external_limit_command")
        context = Context()
        self._contexts.add(context.id)
        if len(self._contexts) > 100:
            self._contexts = {context.id}
        if self.green_hold and service == "turn_off" and entity_id == cfg.get("charger_switch_entity"):
            self.green_hold.update(off_dispatched=True,off_context=context.id)
            await self.coordinator._save(immediate=True)
        async with asyncio.timeout(SERVICE_TIMEOUT):
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id, **values}, blocking=True, context=context)

    async def _begin(self, kind: str, now: float, *, recovery: bool = False) -> None:
        self.pending = {"kind": kind, "sent": now, "attempts": 1, "last_attempt": now}
        if recovery and kind == "manual":
            self.pending["recovery"] = True
        await self.coordinator._save(immediate=True)

    async def _green(self, now: float) -> None:
        """Stop only our Manual, confirm physical idle, then yield to Green."""
        if not self.owned_manual:
            return
        cfg = self.grant["binding"]
        if self.pending is None or self.pending.get("kind") not in {"stop", "green"}:
            await self._begin("stop", now)
            await self._call("switch", "turn_off", cfg["charger_switch_entity"])
            self.status = "waiting_physical_stop"
            return
        sent = self.pending["sent"]
        if self.pending["kind"] == "stop":
            switch = state(self.hass, cfg["charger_switch_entity"])
            power = physical_power(self.hass, cfg, now)
            if not (switch and switch.state == "off" and power["fresh"] and not power["positive"] and power["received_at"] >= sent - 2):
                self.status = "waiting_physical_stop"
                await self._retry_negative("switch", "turn_off", cfg["charger_switch_entity"], now)
                return
            await self._begin("green", now)
            await self._call("select", "select_option", cfg["control_mode_entity"], option="Green")
            self.status = "waiting_green_feedback"
            return
        if not self._mode_confirmed("Green", sent):
            self.status = "waiting_green_feedback"
            await self._retry_negative("select", "select_option", cfg["control_mode_entity"], now, option="Green")
            return
        if self.grant and self.grant.get("manual_since") is not None:
            self.grant["manual_seconds"] += max(0, now - self.grant["manual_since"])
            self.grant["manual_since"] = None
        if self.grant:
            self.grant["start_watch_since"] = None
        self.owned_manual, self.pending = False, None
        self.status = "green_confirmed"
        await self.coordinator._save(immediate=True)

    async def _retry_negative(self, domain: str, service: str, entity_id: str, now: float, **values: Any) -> None:
        """Only owned stop/Green can retry, at most three times, 30s apart."""
        source = state(self.hass, entity_id)
        if source is None or source.state in {"unknown", "unavailable"} or not self.hass.services.has_service(domain, service):
            self.status = "waiting_actuator_source"
            return  # A source reload is not a dispatched hardware attempt.
        if not self.pending or self.pending.get("attempts", 1) >= 3 or now - self.pending.get("last_attempt", self.pending["sent"]) < 30:
            return
        self.pending["attempts"] = self.pending.get("attempts", 1) + 1
        self.pending["last_attempt"] = now
        await self.coordinator._save(immediate=True)
        await self._call(domain, service, entity_id, **values)

    async def _notify_fault(self, reason: str | None = None) -> None:
        """One local HA warning; never a silent unconfirmed physical stop."""
        slot = "_notified_fault" if reason is None else "_notified_status"
        reason = reason or self.fault
        if reason is None or reason == getattr(self, slot):
            return
        setattr(self, slot, reason)
        if self.hass.services.has_service("persistent_notification", "create"):
            audit = self.limit_audit
            extra = (f" Laadlimiet {audit['entity_id']}: AdaptiveCharge schreef {audit['written']}%; oorspronkelijke instelling {audit['original']}%. Automatisch herstel is overgedragen; controleer deze voertuiglimiet zelf." if audit and audit.get("retired_reason") else "")
            message = {"physical_start_unconfirmed": "Een laadstart is aangevraagd, maar vers laadvermogen is nog niet bevestigd. De opdracht blijft bewaard; er wordt geen extra Start verstuurd. Controleer de auto en laadpaal.", "foreign_controller_requires_new_start": "Een andere bediening heeft de laadpaal overgenomen. De oude opdracht blijft bewaard; een nieuwe eigen Start is nodig voor overname.", "external_mode_command_requires_new_start": "De laadmodus is door een andere bediening gewijzigd. De opdracht blijft bewaard; een nieuwe eigen Start is nodig voor overname."}.get(reason, "Geen automatische nieuwe laadstart. Controleer de besturingsstatus en het fysieke laadvermogen; een aangevraagde stop kan nog onbevestigd zijn.")
            if reason == "explicit_stop_preserved":
                message = f"De laadopdracht is gestopt. De eerder door AdaptiveCharge gepauzeerde laadschakelaar {self.config.get('charger_switch_entity')} blijft uit. Ook Green hervat pas als je de schakelaar weer aanzet of de auto opnieuw aansluit."
            if reason == "invalid_stored_green_pause":
                message = "De bewaarde economische pauze is ongeldig. De laadschakelaar kan nog uit staan; besturing blijft uit tot je deze toestand controleert."
            await self.hass.services.async_call("persistent_notification", "create", {"notification_id": f"{DOMAIN}_{self.coordinator.entry.entry_id}", "title": "AdaptiveCharge vraagt aandacht", "message": message + extra}, blocking=True)

    async def _manual(self, now: float) -> None:
        cfg = self.config
        if self.owned_manual and self.pending is None:
            since = self.grant.get("start_watch_since") if self.grant else None
            if since is not None:
                power = physical_power(self.hass, cfg, now)
                if power["fresh"] and power["positive"] and power["received_at"] > since:
                    self.grant["start_watch_since"] = None
                    await self.coordinator._save(immediate=True)
                elif now - since > FEEDBACK_TIMEOUT:
                    self.status = "physical_start_unconfirmed"
                    await self._notify_fault("physical_start_unconfirmed")
                    return  # Preserve command; neither retry On nor cancel a phase pause.
            self.status = "manual_requested"  # Zero/anonymous OFF is not a retry.
            return
        if not self.owned_manual:
            if not (self._mode_confirmed("Green") or self._mode_confirmed("Manual")):
                self.status = "waiting_existing_mode_feedback"
                return
            self.owned_manual = True
            prior_manual_since = self.grant.get("manual_since")
            if self.grant and self.grant.get("manual_since") is None:
                self.grant["manual_since"] = now
            await self._begin("manual", now)
            try:
                await self._call("select", "select_option", cfg["control_mode_entity"], option="Manual")
            except DispatchRefused:
                self.owned_manual, self.pending = False, None
                self.grant["manual_since"] = prior_manual_since
                raise  # No Manual service was issued, so no charger cleanup is owed.
            self.status = "waiting_manual_feedback"
            return
        if self.pending and self.pending["kind"] == "manual":
            if not self._mode_confirmed("Manual", self.pending["sent"]):
                self.status = "waiting_manual_feedback"
                return
            if "mode_ack_at" not in self.pending:
                self.pending["mode_ack_at"] = now
                await self.coordinator._save(immediate=True)
            profile = self.coordinator.profile(self.grant["vehicle_id"])
            vehicle_switch = state(self.hass, profile.get("vehicle_charge_switch_entity")) if profile.get("kind") == "tesla" else None
            if vehicle_switch and vehicle_switch.state == "off" and not self.pending.get("vehicle_start_sent"):
                self.pending["vehicle_start_sent"] = True
                await self.coordinator._save(immediate=True)
                try:
                    await self._call("switch", "turn_on", vehicle_switch.entity_id)
                except DispatchRefused:
                    self.pending["vehicle_start_sent"] = False
                    await self.coordinator._save(immediate=True)
                    raise
            power = physical_power(self.hass, cfg, now)
            if power["fresh"] and power["positive"]:
                self.pending = None
                self.grant["start_watch_since"] = None
                if self.grant.get("manual_since") is None:
                    self.grant["manual_since"] = now
                self.status = "physical_charging_observed"
                await self.coordinator._save(immediate=True)
                return
            # Selecting Manual may itself energize the EVSE. Allow independent
            # telemetry to settle before considering one active override.
            if now - self.pending["mode_ack_at"] < START_SETTLE_SECONDS:
                self.status = "waiting_physical_start"
                return
            switch = state(self.hass, cfg["charger_switch_entity"])
            if not (switch and switch.state == "off" and power["fresh"] and not power["positive"]):
                self.status = "waiting_physical_start"
                return
            # Intent is durable before the only positive switch action.
            pending = self.pending
            self.pending = None
            self.grant["start_watch_since"] = now
            if self.grant and self.grant.get("manual_since") is None:
                self.grant["manual_since"] = now
            await self.coordinator._save(immediate=True)
            try:
                await self._call("switch", "turn_on", cfg["charger_switch_entity"])
            except DispatchRefused:
                self.pending = pending
                self.grant["start_watch_since"] = None
                await self.coordinator._save(immediate=True)
                raise
            self.status = "manual_requested"

    async def _limit(self, row: dict[str, Any]) -> bool:
        """Optional Tesla capability; never infer an allowed raise from a field."""
        if not self.grant:
            return False
        if row.get("calculation_from_saved_reference"):
            return False  # A blind fallback may finish a budget, never raise a car setting.
        profile = self.coordinator.profile(self.grant["vehicle_id"])
        writer = profile.get("limit_writer_entity")
        if profile.get("kind") != "tesla" or not profile.get("allow_limit_raise", False) or not writer:
            return False
        source = state(self.hass, writer)
        current = finite(source.state) if source else None
        requested = finite((row.get("demand") or {}).get("requested_soc"))
        maximum = min(float(profile.get("maximum_soc", 100)), finite(source.attributes.get("max")) or 100) if source else 0
        if self.limit and self.limit.get("pending"):
            if current == self.limit["written"]:
                self.limit["pending"] = False
                await self.coordinator._save(immediate=True)
            elif dt_util.utcnow().timestamp() - self.limit["sent"] <= FEEDBACK_TIMEOUT:
                self.status = "waiting_limit_readback"
                return True
            else:
                self.fault = "limit_readback_timeout"
                return True
        if self.limit and (self.limit.get("entity_id") != writer or current != self.limit.get("written")):
            self._retire_limit("writer_or_readback_mismatch")
            self.grant["limit_overridden"] = True
            await self.coordinator._save(immediate=True)
        if self.grant.get("limit_overridden") or current is None or requested is None or current >= min(requested + 1, maximum):
            return False
        previous_limit = deepcopy(self.limit)
        original = self.limit["original"] if self.limit else current
        self.limit = {"vehicle_id": self.grant["vehicle_id"], "vehicle_binding": {"kind": "tesla", "limit_writer_entity": writer}, "entity_id": writer, "writer_identity": self._writer_identity(writer), "original": original, "written": min(requested + 1, maximum), "connection_id": self.grant["connection_id"], "sent": dt_util.utcnow().timestamp(), "pending": True}
        await self.coordinator._save(immediate=True)
        try:
            await self._call("number", "set_value", writer, value=self.limit["written"])
        except DispatchRefused:
            self.limit = previous_limit  # No setter was dispatched.
            raise
        self.status = "waiting_limit_readback"
        return True

    def _writer_identity(self, entity_id: str) -> list[str | None]:
        registry = self.hass.data.get(er.DATA_REGISTRY)
        item = registry.async_get(entity_id) if registry and hasattr(registry, "entities") else None
        return [item.id, item.platform, item.unique_id, item.device_id] if item else [entity_id]

    async def restore_limit(self, obligation: dict[str, Any] | None = None) -> None:
        """Restore a vehicle-level obligation; sleeping cars retain it."""
        item = self.limit if obligation is None else obligation
        if not item:
            return
        if self.foreign_reason():
            self._retire_limit("foreign_controller_owner", item)
            self.fault = "foreign_owner_limit_restore_relinquished"
            await self.coordinator._save(immediate=True)
            await self._notify_limit(item, "foreign_controller_owner")
            return
        if item.get("writer_identity") != self._writer_identity(item["entity_id"]):
            self._retire_limit("writer_identity_changed", item)
            await self.coordinator._save(immediate=True)
            await self._notify_limit(item, "writer_identity_changed")
            return
        source = state(self.hass, item["entity_id"])
        value = finite(source.state) if source else None
        if value is None:
            self.status = "waiting_limit_restore_source"
            return  # Sleeping/unavailable is not evidence of lost ownership.
        if value == item["original"]:
            self.limit_audit = {**item, "restored": True}
            self._forget_limit(item)
        elif value != item["written"]:
            self._retire_limit("external_limit_value", item)
        elif not item.get("restore_pending"):
            item.update(restore_pending=True, restore_sent=dt_util.utcnow().timestamp())
            await self.coordinator._save(immediate=True)
            await self._call("number", "set_value", item["entity_id"], value=item["original"])
            self.status = "waiting_limit_restore_readback"
        elif dt_util.utcnow().timestamp() - item["restore_sent"] > FEEDBACK_TIMEOUT:
            self._retire_limit("restore_readback_timeout", item)
            await self._notify_limit(item, "restore_readback_timeout")
        else:
            return
        await self.coordinator._save(immediate=True)

    async def _restore_dormant_limits(self) -> None:
        for item in list(self.dormant_limits):
            try:
                await self.restore_limit(item)
            except (ValueError, TimeoutError, HomeAssistantError):
                self.status = "waiting_limit_restore_readback"

    async def _notify_limit(self, item: dict[str, Any], reason: str) -> None:
        """A vehicle-limit warning never blocks the unrelated charger's command."""
        if item.get("notified_reason") == reason:
            return
        item["notified_reason"] = reason
        if self.hass.services.has_service("persistent_notification", "create"):
            await self.hass.services.async_call("persistent_notification", "create", {"notification_id": f"{DOMAIN}_limit_{item['vehicle_id']}", "title": "Controleer de Tesla-laadlimiet", "message": f"Automatisch herstel van {item['entity_id']} is niet bevestigd. AdaptiveCharge schreef {item['written']}%; de oorspronkelijke instelling was {item['original']}%. Controleer dit in de Tesla-app."}, blocking=True)
        await self.coordinator._save(immediate=True)

    def schedule(self, result: dict[str, Any], now: float) -> None:
        """One exact next-boundary timer; callbacks only reassess current state."""
        if self._timer:
            self._timer()
            self._timer = None
        if not self.enabled or not self.grant:
            return
        row = result.get("vehicles", {}).get(self.grant["vehicle_id"], {})
        times = [w[k] for group in ("windows","green_windows","pause_windows") for w in (row.get("plan") or {}).get(group, []) for k in ("start", "end") if isinstance(w.get(k), (int, float)) and w[k] > now + 0.01]
        if times:
            self._timer = async_track_point_in_utc_time(self.hass, self._wake, datetime.fromtimestamp(min(times), timezone.utc))

    async def _economic_green_step(self, result: dict[str, Any], now: float, *, releasing: bool = False) -> bool:
        """Manage a proven own OFF and its durable baseline-Green cleanup debt.

        OFF must have been ours (pre-state ON, dispatched OFF, confirmed readback).
        A real unplug ends the request, not the charger's cleanup obligation. That
        obligation may restore only Green on the same pinned charger, never Manual.
        Explicit Stop/foreign control supersedes it and leaves an audited handoff.
        """
        active = result.get("active_vehicle")
        row = result.get("vehicles",{}).get(active,{}) if active else {}
        owned = bool(active and self.owns(active) and row.get("eligible_for_boost"))
        desired = bool(not releasing and not self.fault and self.enabled and self.config.get("economic_enabled") and not self.idle_blocked and
                       (owned and row.get("would_control_mode") == "Pause" or not active and result.get("economic_policy",{}).get("pause")))
        hold = self.green_hold
        if hold and (self.foreign_reason() or self._external_mode or self.idle_blocked or self._green_interrupt):
            reason = hold.get("retire_reason") or "green_pause_transferred_to_foreign_controller"
            await self._retire_green_pause(reason,notify=True)
            return False
        if self.owned_manual:
            return False
        if owned and row.get("would_control_mode") == "Manual" and hold:
            await self._retire_green_pause("pause_transferred_to_explicit_owned_manual")
            return False  # The Start-bound Manual executor now owes the cleanup.
        if hold and hold["connection_id"] != self._connection_id():
            hold.update(release_required=True,release_reason="charger_token_changed")
        if hold and hold.get("release_required"):
            desired = False
        if hold and hold.get("stage") in {"resume_mode","resume_switch"}:
            desired = False  # Finish the one owned cleanup before considering another pause.
        if not desired and hold is None:
            return False
        if hold and hold["binding"] != {k:self.config.get(k) for k in BINDING_KEYS}:
            self.status = "economic_pause_binding_changed_cleanup_retained"
            await self._notify_fault(self.status)
            return True
        if self.interlock(check_grant=False) or self.foreign_reason():
            self.status = "economic_pause_waiting_controller_sources"
            return True
        plug = state(self.hass,self.config.get("plug_entity"))
        if not plug or plug.state != "on" or not self._connection_id():
            self.status = "own_green_cleanup_waiting_attachment" if hold else "economic_pause_waiting_attachment"
            return True
        switch = state(self.hass,self.config.get("charger_switch_entity"))
        try:
            if hold is None:
                # The switch is actual charging, not an enable-permission flag.
                # An already OFF car/app must never be adopted and turned ON later.
                before_power = result.get("physical",{})
                if not switch or switch.state != "on" or not before_power.get("fresh") or not before_power.get("positive") or not self._mode_confirmed("Green"):
                    self.status = "economic_pause_no_running_green_to_own"
                    return False
                hold = {"connection_id":self._connection_id(),"binding":{k:self.config.get(k) for k in BINDING_KEYS},
                        "stage":"pausing","sent":now,"attempts":1,"last_attempt":now,
                        "pre_pause_on":True,"pre_pause_power_kw":before_power.get("kw"),"pre_pause_power_at":before_power.get("received_at"),
                        "off_dispatched":False,"off_confirmed":False,"release_required":False}
                self.green_hold = hold
                await self.coordinator._save(immediate=True)
                if self.green_hold is not hold or self._green_interrupt:
                    return True
                await self._call("switch","turn_off",self.config["charger_switch_entity"])
                self.status = "economic_green_pause_requested"
                return True
            physical = result.get("physical",{})
            post_off = bool(hold.get("off_dispatched") and switch and switch.state == "off" and switch.last_reported.timestamp() >= hold["sent"]-2
                            and physical.get("fresh") and not physical.get("positive") and physical.get("received_at",0) >= hold["sent"]-2)
            if not hold["off_confirmed"] and post_off:
                hold.update(off_confirmed=True,stage="paused")
                await self.coordinator._save(immediate=True)
                if self.green_hold is not hold or self._green_interrupt:
                    return True
            if desired:
                if hold["off_confirmed"]:
                    if switch and switch.state == "on":
                        self.idle_blocked = True
                        await self._retire_green_pause("economic_pause_unexpected_on_preserved",notify=True)
                    else:
                        self.status = "economic_green_paused"
                else:
                    self.status = "economic_green_pause_unconfirmed"
                    if now-hold.get("last_attempt",hold["sent"]) >= 30 and hold.get("attempts",1)<3:
                        hold["last_attempt"],hold["attempts"] = now,hold.get("attempts",1)+1
                        await self.coordinator._save(immediate=True)
                        if self.green_hold is hold and not self._green_interrupt:
                            await self._call("switch","turn_off",self.config["charger_switch_entity"])
                return True
            if switch and switch.state == "on" and self._mode_confirmed("Green"):
                await self._retire_green_pause("green_baseline_restored_readback")
                return True  # No positive write is needed if baseline is already back.
            if not hold["off_confirmed"]:
                self.status = "own_green_cleanup_off_not_confirmed"
                if now-hold["sent"] > FEEDBACK_TIMEOUT:
                    await self._notify_fault(self.status)
                return True  # Preserve the debt; never adopt an unproven OFF.
            if hold["stage"] not in {"resume_mode","resume_switch"}:
                hold.update(stage="resume_mode",sent=now)
                await self.coordinator._save(immediate=True)
                if self.green_hold is not hold or self._green_interrupt:
                    return True
                await self._call("select","select_option",self.config["control_mode_entity"],option="Green")
                self.status = "economic_green_resume_mode"
                return True
            if hold["stage"] == "resume_mode":
                if not self._mode_confirmed("Green",hold["sent"]):
                    self.status = "economic_green_resume_waiting_feedback"
                    return True
                hold.update(stage="resume_switch",sent=now)
                await self.coordinator._save(immediate=True)
                if self.green_hold is not hold or self._green_interrupt:
                    return True
                await self._call("switch","turn_on",self.config["charger_switch_entity"])
                self.status = "economic_green_resume_requested"
                return True
            if switch and switch.state == "on" and self._mode_confirmed("Green"):
                await self._retire_green_pause("economic_green_resumed_confirmed")
            else:
                self.status = "economic_green_resume_unconfirmed"
                if now-hold["sent"] > FEEDBACK_TIMEOUT:
                    await self._notify_fault(self.status)
            return True
        except (ValueError,TimeoutError,HomeAssistantError) as exc:
            self.status = f"own_green_cleanup_retained:{type(exc).__name__}"
            await self.coordinator._save(immediate=True)
            return True

    def _assess_caps(self, result: dict[str,Any], now: float) -> None:
        """Bound unmetered exposure without inventing delivered-energy credit.

        A missing vehicle update is irrelevant to this guard. When the meter is
        missing, maximum possible Manual energy advances a separate safety bound.
        The real goal ledger remains unchanged until actual measurements return.
        """
        if not self.grant or not self.owns(self.grant["vehicle_id"],terminal=True):
            return
        if self.fault == "invalid_stored_exposure_bound":
            return  # Retain valid limit/negative cleanup obligations, never repair authority silently.
        grant = self.grant
        request = self.coordinator.sessions.get(grant["vehicle_id"])
        if request is None:
            return
        if not self.owned_manual and request.terminal and self.fault != "unmetered_energy_exposure_cap":
            return  # Idle terminal grants must not churn the persistent store.
        origin = request.origin_meter if request.origin_meter is not None else request.anchor_meter
        used = max(0,request.last_meter-origin) if request.last_meter is not None and origin is not None else 0.0
        rate = finite(self.config.get("power_kw",22))
        rate = rate if rate is not None and 1 <= rate <= 22 else 22
        previous = finite(grant.get("cap_checked_at"))
        exposure = finite(grant.get("unmetered_exposure_kwh")) or 0.0
        if result.get("meter_kwh") is not None:
            reported = finite(result.get("meter_observed_at")) or now
            since = max(reported,finite(grant.get("manual_since")) or now)
            exposure = rate*max(0,now-since)/3600 if self.owned_manual else 0.0
            grant["last_known_used_kwh"] = used
            if (self.fault == "unmetered_energy_exposure_cap" and reported > grant.get("exposure_fault_report_at",0)
                    and used + rate*600/3600 < grant["max_kwh"]):
                self.fault = None  # Recovered real accounting automatically reassesses the same request.
        elif self.owned_manual:
            exposure += rate*max(0,now-(previous if previous is not None else now))/3600
        if self.owned_manual or exposure or grant.get("unmetered_exposure_kwh"):
            grant["unmetered_exposure_kwh"],grant["cap_checked_at"] = exposure,now
        bounded_used = max(used,(finite(grant.get("last_known_used_kwh")) or used)+exposure)
        seconds = grant.get("manual_seconds",0)+max(0,now-grant["manual_since"]) if grant.get("manual_since") is not None else grant.get("manual_seconds",0)
        hours = finite(self.config.get("maximum_manual_hours",MAX_MANUAL_SECONDS/3600))
        maximum = hours*3600 if hours is not None and 1 <= hours <= 48 else MAX_MANUAL_SECONDS
        if not request.terminal or self.owned_manual or self.pending:
            if used >= grant["max_kwh"] or seconds >= maximum:
                self.fault = "command_energy_or_manual_time_cap"
            elif bounded_used >= grant["max_kwh"]:
                if self.fault != "unmetered_energy_exposure_cap":
                    grant["exposure_fault_report_at"] = finite(result.get("meter_observed_at")) or now
                self.fault = "unmetered_energy_exposure_cap"

    async def step(self, result: dict[str, Any], now: float) -> None:
        """Execute the current request, never a captured old timer payload."""
        await self.drain_events()
        await self._restore_dormant_limits()
        if self.fault == "invalid_stored_green_pause":
            await self._notify_fault()
        self._assess_caps(result,now)
        request = self.coordinator.sessions.get(self.grant["vehicle_id"]) if self.grant else None
        if self.limit and not self.owned_manual and self.pending is None and (not self.enabled or request is None or request.terminal or self.fault):
            try:
                await self.restore_limit()
            except (ValueError, TimeoutError, HomeAssistantError):
                self.status = "waiting_limit_restore_readback"
        if await self._economic_green_step(result,now):
            self.schedule(result,now)
            return
        if self.config.get("charger_fault_recovery") and self.grant and self.owns(self.grant["vehicle_id"]):
            gateway = state(self.hass,self.config.get("charger_connectivity_entity"))
            missing = bool(self.config.get("charger_connectivity_entity") and (not gateway or gateway.state != "on"))
            missing |= any((s := state(self.hass,self.config.get(key))) is None or s.state in {"unknown","unavailable"} for key in ("control_mode_entity","control_feedback_entity","control_report_entity"))
            if missing and not self._stop_requested and not self.fault:
                if not self._charger_outage:
                    self._charger_outage = True
                    await self.coordinator._save(immediate=True)
                self.status = "charger_gateway_recovery_waiting"
                return
            if self._charger_outage and not missing:
                self._charger_outage = False
                self._restored = True
                if self.pending and self.pending.get("kind") == "manual":
                    self.pending = None
                await self.coordinator._save(immediate=True)
        if not self.enabled and not self.owned_manual and not self.limit:
            self.status = "disabled"
            return
        if self.grant and (self._external_mode or self.foreign_reason()):
            before = self.as_dict()
            self.fault = "foreign_controller_requires_new_start" if not self._external_mode else "external_mode_command_requires_new_start"
            self.status = self.fault
            self.owned_manual, self.pending = False, None
            if self.foreign_reason():
                item = self.limit
                self._retire_limit("foreign_controller_owner")
                if item:
                    await self._notify_limit(item, "foreign_controller_owner")
            elif self.limit:
                try:
                    await self.restore_limit()  # External mode owns charger, not the Tesla number.
                except (ValueError, TimeoutError, HomeAssistantError):
                    self.status = "waiting_limit_restore_readback"
            if self.as_dict() != before:
                await self.coordinator._save(immediate=True)
            await self._notify_fault()
            return
        if not self.enabled and not self.owned_manual and not self.limit:
            self.status = "disabled"
            return
        if reason := self.interlock():
            self.status = reason
            if self.owned_manual:
                request = self.coordinator.sessions.get(self.grant["vehicle_id"])
                if reason in TRANSIENT_INTERLOCKS and self.fault is None and not self._stop_requested and not self._cleanup_required and request and not request.terminal:
                    if self._interlock_since is None:
                        self._interlock_since = now
                        await self.coordinator._save(immediate=True)
                    if now - self._interlock_since < FEEDBACK_TIMEOUT:
                        return  # Block positive writes immediately, tolerate a short telemetry gap.
                before = self.as_dict()
                if self.fault is None:
                    self.fault = self._interlock_fault = reason
                self._cleanup_required = True
                if self.as_dict() != before:
                    await self.coordinator._save(immediate=True)
                try:
                    await self._green(now)  # Pinned negative targets, never changed config.
                except (ValueError, TimeoutError, HomeAssistantError):
                    self.status = "owned_stop_unconfirmed"
                await self._notify_fault()
                if self.status != "owned_stop_unconfirmed":
                    self.status = reason
            elif self.limit:
                try:
                    await self.restore_limit()
                except (ValueError, TimeoutError, HomeAssistantError):
                    self.status = "waiting_limit_restore_readback"
            return
        self._interlock_since = None
        if self._interlock_fault and not self.owned_manual and not self.limit and not self.pending:
            if self.fault == self._interlock_fault:
                self.fault = None
            self._interlock_fault = None
            await self.coordinator._save(immediate=True)
        if not self.grant:
            self.status = "enabled_no_request"
            return
        vehicle_id = self.grant["vehicle_id"]
        request = self.coordinator.sessions.get(vehicle_id)
        if not self.owns(vehicle_id, terminal=True):
            self.status = "attachment_or_command_replaced"
            if self.owned_manual or self.pending or self.limit:
                before = self.as_dict()
                self.fault = "stored_command_missing_or_replaced"
                self._cleanup_required = True
                try:
                    await self._green(now)
                    if not self.owned_manual:
                        await self.restore_limit()
                        self._cleanup_required = False
                except (ValueError, TimeoutError, HomeAssistantError):
                    self.status = "owned_stop_unconfirmed"
                if self.as_dict() != before:
                    await self.coordinator._save(immediate=True)
                await self._notify_fault()
            return
        if self._connection_id() is not None:
            self._connection_unknown_since = None
        if request and not request.terminal and request.connection_gap_since is not None:
            # Keep the logical request, but do not carry its Manual intent onto
            # an attachment that became unobservable. Return owned Manual to
            # baseline while same-vehicle evidence recovers automatically.
            if self.owned_manual:
                try:
                    await self._green(now)
                except (ValueError,TimeoutError,HomeAssistantError):
                    self.status = "owned_stop_unconfirmed"
                    return
            self.status = "attachment_recovery_waiting_vehicle_evidence_request_preserved"
            return
        if not self._same_attachment():
            current = self._connection_id()
            if not current:
                self.status = "waiting_connection_identity"
                if request.terminal and not self.owned_manual and not self.pending and not self.limit:
                    return
                before = self.as_dict()
                if self._connection_unknown_since is None:
                    self._connection_unknown_since = now
                if request.terminal or self.fault or self._cleanup_required or now - self._connection_unknown_since >= FEEDBACK_TIMEOUT:
                    self._cleanup_required = True
                    try:
                        await self._green(now)
                        if not self.owned_manual:
                            await self.restore_limit()
                            self._cleanup_required = False
                        if self.pending and now - self.pending["sent"] > FEEDBACK_TIMEOUT:
                            self.fault = "actuator_feedback_timeout"
                    except (ValueError, TimeoutError, HomeAssistantError):
                        self.fault = "owned_stop_unconfirmed"
                    if self.as_dict() != before:
                        await self.coordinator._save(immediate=True)
                    await self._notify_fault()
                return
            self._connection_unknown_since = None
            if request and not request.terminal:
                request.stop("connection_replaced")
            # The enabled charger manager returns its own lingering Manual to
            # baseline. No other request may exist; a new explicit Start cannot
            # adopt this old grant or its cleanup timer.
            if any(not s.terminal and s is not request for s in self.coordinator.sessions.values()):
                self.status = "new_request_owns_charger"
                return
            self._cleanup_connection = current
        row = result.get("vehicles", {}).get(vehicle_id, {})
        native_generation = state(self.hass, self.config.get("generation_entity"))
        saved_generation = self.grant.get("legacy_generation")
        if request and not request.terminal and saved_generation is not None and native_generation and native_generation.state not in {"unknown", "unavailable", saved_generation}:
            request.stop("external_native_generation_changed")
            self._stop_requested = True
            await self.coordinator._save(immediate=True)
        restored_elapsed = self._restored and request and request.departure is not None and request.departure <= now
        was_restored = self._restored
        if not restored_elapsed:
            self._restored = False
        recovery_boost = bool(self.enabled and not self._stop_requested and not restored_elapsed and not self.fault and request and not request.terminal and row.get("eligible_for_boost") and row.get("would_control_mode") == "Manual")
        recovery = was_restored and self.pending is None or bool(self.pending and self.pending.get("recovery"))
        if (recovery_boost and recovery and self.owned_manual and not self._cleanup_required
                and not (self.limit and self.limit.get("restore_pending"))
                and (self.pending is None or self.pending.get("kind") == "manual")
                and self._running_manual_readback(now)):
            # The same owned request is already physically charging. A cloud
            # report predating HA startup must not cause a redundant Manual
            # write followed by a false acknowledgment timeout.
            self.pending = None
            self.grant["start_watch_since"] = now
            if self.grant.get("manual_since") is None:
                self.grant["manual_since"] = now
            was_restored = False
            await self.coordinator._save(immediate=True)
            _LOGGER.info("Restored owned Manual without a new actuator write; awaiting next independent power observation (generation %s)", self.grant["generation"])
        feedback_anchor = self.pending.get("mode_ack_at", self.pending.get("sent")) if self.pending else None
        feedback_window = FEEDBACK_TIMEOUT + (START_SETTLE_SECONDS if self.pending and "mode_ack_at" in self.pending else 0)
        if self.pending and not self._interlock_fault and (finite(feedback_anchor) is None or now - feedback_anchor > feedback_window):
            self.fault = "actuator_feedback_timeout"
        if self.fault:
            self.status = self.fault
            # No positive retry; a pending stop can still gain confirmation.
            if self.owned_manual:
                try:
                    await self._green(now)
                except (ValueError, TimeoutError, HomeAssistantError):
                    self.status = "owned_stop_unconfirmed"
            elif self.limit:
                await self.restore_limit()
            await self._notify_fault()
            return
        if self._cleanup_required:
            try:
                await self._green(now)
            except (ValueError, TimeoutError, HomeAssistantError):
                self.status = "owned_stop_unconfirmed"
            if not self.owned_manual:
                self._cleanup_required = False
                self._stop_requested = False
                await self.coordinator._save(immediate=True)
            return
        if self.limit and self.limit.get("restore_pending"):
            await self.restore_limit()
            if self.limit:
                return
        boost = bool(self.enabled and not self._stop_requested and not restored_elapsed and not self.fault and request and not request.terminal and row.get("eligible_for_boost") and row.get("would_control_mode") == "Manual")
        unpriced = (row.get("plan") or {}).get("reason", "").startswith("unpriced_")
        if boost and unpriced and not self.unpriced_started(request):
            self.grant["unpriced_departure"] = request.departure
            await self.coordinator._save(immediate=True)
        elif not unpriced and "unpriced_departure" in self.grant:
            self.grant.pop("unpriced_departure")
            await self.coordinator._save(immediate=True)
        try:
            if boost and await self._limit(row):
                return
            if boost and was_restored and self.owned_manual and self.pending is None:
                # Exactly one explicit recovery handshake after a real reload;
                # ordinary zero-power/anonymous OFF never enters this path.
                await self._begin("manual", now, recovery=True)
                await self._call("select", "select_option", self.config["control_mode_entity"], option="Manual")
                self.status = "restoring_manual_feedback"
            elif boost and (self.pending is None or self.pending.get("kind") == "manual"):
                await self._manual(now)
            else:
                await self._green(now)
                if not self.owned_manual and (not self.enabled or request is None or request.terminal):
                    await self.restore_limit()
                    if self._cleanup_connection and not self.limit:
                        self.grant, self._cleanup_connection = None, None
                        await self.coordinator._save(immediate=True)
        except (ValueError, TimeoutError, HomeAssistantError) as exc:
            if isinstance(exc, DispatchRefused) and str(exc) in TRANSIENT_INTERLOCKS:
                self.status = str(exc)
                await self.coordinator._save(immediate=True)
                return
            if str(exc) == "fallback_window_not_due":
                if self._mode_confirmed("Green"):
                    self.owned_manual, self.pending = False, None
                    if self.grant:
                        if self.grant.get("manual_since") is not None:
                            self.grant["manual_seconds"] += max(0, now - self.grant["manual_since"])
                        self.grant["manual_since"] = None
                        self.grant["start_watch_since"] = None
                else:
                    self._cleanup_required = self.owned_manual
                self.status = "waiting_fallback_window"
                await self.coordinator._save(immediate=True)
                return
            # Persist a visible fault; never automatically resend a positive call.
            self.fault = str(exc) if isinstance(exc, DispatchRefused) else f"actuator_error:{type(exc).__name__}"
            self.status = self.fault
            await self.coordinator._save(immediate=True)
            await self._notify_fault()
        finally:
            if restored_elapsed and not self.owned_manual:
                self.status = "restored_departure_elapsed_requires_start"
            self.schedule(result, now)

    async def shutdown(self) -> None:
        """Remove timers/listeners and persist; no unrequested Stop on HA restart."""
        if self._closed:
            return
        self._closed = True
        for cancel in (self._timer, self._interval, self._bus):
            if cancel:
                cancel()

    async def prepare_unload(self) -> None:
        """Explicit removal/reconfigure yields our Manual; HA restart retains it."""
        if self.hass.is_stopping:
            return
        self._stop_requested = True
        self._cleanup_required = self.owned_manual
        deadline = asyncio.get_running_loop().time() + FEEDBACK_TIMEOUT + 5
        while True:
            async with self.coordinator._lock:
                await self.drain_events()
                if self._external_mode or self.foreign_reason():
                    self.fault = "external_mode_command_requires_new_start" if self._external_mode else "foreign_controller_requires_new_start"
                    self.owned_manual, self.pending = False, None
                    if self.foreign_reason():
                        item = self.limit
                        self._retire_limit("foreign_controller_owner")
                        if item:
                            await self._notify_limit(item, "foreign_controller_owner")
                    else:
                        await self.restore_limit()
                else:
                    try:
                        if self.green_hold:
                            await self._economic_green_step({"active_vehicle":None},dt_util.utcnow().timestamp(),releasing=True)
                        await self._green(dt_util.utcnow().timestamp())
                        if not self.owned_manual:
                            await self.restore_limit()
                    except (ValueError, TimeoutError, HomeAssistantError):
                        self.status = "owned_stop_unconfirmed"
                await self.coordinator._save(immediate=True)
            if not self.owned_manual and not self.green_hold and (not self.limit or not self.limit.get("restore_pending")):
                break
            if asyncio.get_running_loop().time() >= deadline:
                async with self.coordinator._lock:
                    self.fault = self._interlock_fault = "unload_stop_unconfirmed"
                    await self.coordinator._save(immediate=True)
                    await self._notify_fault()
                break  # Durable cleanup obligation, but no FAILED_UNLOAD/task leak.
            await asyncio.sleep(1)
