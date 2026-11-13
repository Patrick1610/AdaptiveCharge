"""One charger entry with vehicle subentries; control defaults off."""

from __future__ import annotations

from typing import Any
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .const import DOMAIN
from .sources import SourceBindingError, validate_source_bindings


def identity(hass: Any, entity_id: str) -> str:
    """Prefer a stable device/registry identity over a renameable entity ID."""
    registry = er.async_get(hass)
    registered = registry.async_get(entity_id) if hasattr(registry, "entities") else None
    if registered is not None:
        return f"device:{registered.device_id}" if registered.device_id else f"entity:{registered.id}"
    return f"unregistered:{entity_id}"


def same_binding(old_key: str, new_key: str, old_entity: str, new_entity: str) -> bool:
    """Allow registry/device association to appear for an unchanged source."""
    return old_key == new_key or old_entity == new_entity and old_key.split(":", 1)[0] != new_key.split(":", 1)[0]


def source_errors(hass: Any, data: dict[str, Any] | None) -> dict[str, str]:
    """Report invalid self-input in the form before a config entry is saved."""
    try:
        validate_source_bindings(hass, [data or {}])
    except SourceBindingError as exc:
        return {exc.field: "preview_source"}
    return {}


def entity(domains: list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))


def charger_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Shared input bindings and calculation defaults; no credentials/writers."""
    data = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required("name", default=data.get("name", "Enphase Preview")): str,
        vol.Required("meter_entity", default=data.get("meter_entity", vol.UNDEFINED)): entity(["sensor"]),
        vol.Required("power_entity", default=data.get("power_entity", vol.UNDEFINED)): entity(["sensor"]),
        vol.Required("plug_entity", default=data.get("plug_entity", vol.UNDEFINED)): entity(["binary_sensor"]),
    }
    for key, domains in {"connection_entity": ["sensor", "binary_sensor", "switch"], "mode_entity": ["select", "sensor"], "charging_entity": ["binary_sensor"], "price_entity": ["sensor"], "forecast_entity": ["sensor"], "house_entity": ["sensor"], "session_entity": ["input_text"], "active_entity": ["input_boolean"], "generation_entity": ["counter"], "pending_entity": ["input_text"], "legacy_master_entity": ["input_boolean"], "control_mode_entity": ["select"], "charger_switch_entity": ["switch"], "control_feedback_entity": ["sensor"], "control_report_entity": ["sensor"]}.items():
        fields[vol.Optional(key)] = entity(domains)
    for key, default, lower, upper in (("power_kw", 11, 1, 22), ("reserve_min", 15, 0, 60), ("minimum_min", 3, 1, 30), ("house_kw", 0.5, 0, 30), ("price_interval_min", 15, 15, 60)):
        fields[vol.Required(key, default=data.get(key, default))] = vol.All(vol.Coerce(float), vol.Range(min=lower, max=upper))
    for key, default in (("connection_attribute", ""), ("price_attribute", "data"), ("price_time_key", "time"), ("import_price_key", "price"), ("export_price_key", "")):
        fields[vol.Required(key, default=data.get(key, default))] = str
    fields[vol.Required("maximum_command_kwh", default=data.get("maximum_command_kwh", 30))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=100))
    fields[vol.Required("require_prices_for_control", default=data.get("require_prices_for_control", True))] = bool
    return vol.Schema(fields)


def vehicle_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Only a configured limit-writer capability can suggest a higher limit."""
    data = defaults or {}
    fields: dict[Any, Any] = {vol.Required("name", default=data.get("name", "")): str, vol.Required("kind", default=data.get("kind", "tesla")): vol.In(["tesla", "id3"])}
    for key, domains in {"soc_entity": ["sensor"], "battery_entity": ["sensor"], "range_entity": ["sensor"], "limit_entity": ["sensor", "number"], "cable_entity": ["binary_sensor"]}.items():
        fields[vol.Required(key, default=data.get(key, vol.UNDEFINED))] = entity(domains)
    for key, domains in {"home_entity": ["binary_sensor", "device_tracker"], "soh_entity": ["sensor"], "measurement_time_entity": ["sensor"], "limit_writer_entity": ["number"], "target_entity": ["input_number", "number"], "departure_entity": ["input_datetime", "datetime"], "native_plan_entity": ["sensor"], "vehicle_charge_switch_entity": ["switch"]}.items():
        fields[vol.Optional(key)] = entity(domains)
    fields[vol.Required("droop_pp", default=data.get("droop_pp", 1))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10))
    fields[vol.Required("margin", default=data.get("margin", 0.15))] = vol.All(vol.Coerce(float), vol.Range(min=0, max=0.5))
    fields[vol.Required("allow_limit_raise", default=data.get("allow_limit_raise", False))] = bool
    fields[vol.Required("maximum_soc", default=data.get("maximum_soc", 100))] = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))
    return vol.Schema(fields)


class PreviewConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a separately installed observer keyed by its charger meter."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {"vehicle": VehicleSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = source_errors(self.hass, user_input)
        if user_input is not None and not errors:
            charger_key = identity(self.hass, user_input["meter_entity"])
            await self.async_set_unique_id(charger_key)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input["name"], data={**user_input, "charger_key": charger_key, "control_enabled": False})
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(charger_schema(), user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors = source_errors(self.hass, user_input)
        if user_input is not None and not errors:
            charger_key = identity(self.hass, user_input["meter_entity"])
            if not same_binding(entry.data.get("charger_key", entry.unique_id or ""), charger_key, entry.data["meter_entity"], user_input["meter_entity"]):
                return self.async_abort(reason="charger_identity_changed")
            if any(e.entry_id != entry.entry_id and e.unique_id == charger_key for e in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            if getattr(entry, "runtime_data", None) and entry.runtime_data.control.binding_change_blocked(user_input):
                return self.async_abort(reason="finish_control_cleanup_first")
            return self.async_update_and_abort(entry, unique_id=charger_key, title=user_input["name"], data={**user_input, "charger_key": charger_key, "control_enabled": False})
        defaults = user_input or dict(entry.data)
        return self.async_show_form(step_id="reconfigure", data_schema=self.add_suggested_values_to_schema(charger_schema(defaults), defaults), errors=errors)


class VehicleSubentryFlow(ConfigSubentryFlow):
    """Add/edit a vehicle without duplicating the charger controller."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = source_errors(self.hass, user_input)
        if user_input is not None and not errors:
            unique_id = identity(self.hass, user_input["soc_entity"])
            if any(s.unique_id == unique_id for s in self._get_entry().subentries.values()):
                return self.async_abort(reason="vehicle_already_configured")
            return self.async_create_entry(title=user_input["name"], data=user_input, unique_id=unique_id)
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(vehicle_schema(), user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry, subentry = self._get_entry(), self._get_reconfigure_subentry()
        errors = source_errors(self.hass, user_input)
        if user_input is not None and not errors:
            unique_id = identity(self.hass, user_input["soc_entity"])
            if not same_binding(subentry.unique_id or "", unique_id, subentry.data["soc_entity"], user_input["soc_entity"]):
                return self.async_abort(reason="vehicle_identity_changed")
            if any(s.subentry_id != subentry.subentry_id and s.unique_id == unique_id for s in entry.subentries.values()):
                return self.async_abort(reason="vehicle_already_configured")
            if getattr(entry, "runtime_data", None) and entry.runtime_data.control.binding_change_blocked(user_input, subentry.subentry_id):
                return self.async_abort(reason="finish_control_cleanup_first")
            return self.async_update_and_abort(entry, subentry, unique_id=unique_id, title=user_input["name"], data=user_input)
        defaults = user_input or dict(subentry.data)
        return self.async_show_form(step_id="reconfigure", data_schema=self.add_suggested_values_to_schema(vehicle_schema(defaults), defaults), errors=errors)
