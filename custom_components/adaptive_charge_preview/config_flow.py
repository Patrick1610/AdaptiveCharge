"""One charger entry with vehicle subentries; control defaults off."""

from __future__ import annotations

from math import isfinite
from typing import Any
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.data_entry_flow import Optional as VisibleOption, section
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .const import AI_OPTIMISATION, DOMAIN, SELF_CONSUMPTION
from .sources import SourceBindingError, charger_settings, uses_plug_identity, validate_source_bindings


CHARGER_SECTIONS = {
    "price_source": ("price_entity",),
    "charger_connections": (
        "meter_entity", "plug_entity",
        "control_mode_entity", "charger_switch_entity", "control_feedback_entity",
        "control_report_entity", "system_profile_entity", "fallback_power_entity",
        "capacity_entity", "gateway_connectivity_entity", "charger_connectivity_entity",
    ),
    "planning_settings": (
        "power_kw", "safe_power_kw", "reserve_min", "minimum_min",
        "forecast_entity", "house_entity", "house_kw", "economic_enabled",
        "idle_green_price_filter", "early_offset_cents", "charger_fault_recovery",
        "maximum_command_kwh", "maximum_manual_hours",
    ),
    "tariff_settings": (
        "price_interval_min", "price_attribute", "price_time_key", "import_price_key",
        "export_contract", "export_price_key", "zonneplan_tax_eur_kwh",
        "zonneplan_tax_valid_until", "zonneplan_import_fee_eur_kwh",
        "zonneplan_export_fee_eur_kwh", "zonneplan_bonus_eligible",
        "bonus_export_ytd_entity", "bonus_export_year", "maximum_export_kw",
    ),
    "legacy_controller": (
        "legacy_master_entity", "session_entity", "active_entity",
        "pending_entity", "generation_entity",
    ),
    "live_connection_identity": ("connection_entity", "connection_attribute"),
}

CHARGER_OPTIONAL_ENTITIES = {
    "price_entity": ["sensor"],
    "connection_entity": ["sensor", "binary_sensor", "switch"],
    "forecast_entity": ["sensor"],
    "house_entity": ["sensor"],
    "session_entity": ["input_text"],
    "active_entity": ["input_boolean"],
    "generation_entity": ["counter"],
    "pending_entity": ["input_text"],
    "legacy_master_entity": ["input_boolean"],
    "control_mode_entity": ["select"],
    "charger_switch_entity": ["switch"],
    "control_feedback_entity": ["sensor"],
    "control_report_entity": ["sensor"],
    "capacity_entity": ["sensor"],
    "gateway_connectivity_entity": ["binary_sensor"],
    "fallback_power_entity": ["sensor"],
    "charger_connectivity_entity": ["binary_sensor"],
    "system_profile_entity": ["sensor", "select"],
    "bonus_export_ytd_entity": ["sensor"],
}

SHOW_ZONNEPLAN = {"field": "export_contract", "value": "zonneplan"}
SHOW_BONUS = {"field": "zonneplan_bonus_eligible", "value": True}


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


class PlanningPowerSelector(selector.NumberSelector):
    """Keep the zero-or-positive-range rule editable in HA's numeric form."""

    def __call__(self, data: Any) -> float:
        value = super().__call__(data)
        if not isfinite(value) or 0 < value < 1:
            raise vol.Invalid("Use 0 or a planning power from 1 to 22 kW")
        return value


def charger_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Present the common settings first and fold the installation details."""
    data = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required("enphase_profile", default=data.get("enphase_profile", SELF_CONSUMPTION)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[SELF_CONSUMPTION, AI_OPTIMISATION],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="enphase_profile",
            )
        ),
        vol.Required("name", default=data.get("name", "Enphase")): str,
        vol.Required("meter_entity", default=data.get("meter_entity", vol.UNDEFINED)): entity(["sensor"]),
        vol.Required("power_entity", default=data.get("power_entity", vol.UNDEFINED)): entity(["sensor"]),
        vol.Required("plug_entity", default=data.get("plug_entity", vol.UNDEFINED)): entity(["binary_sensor"]),
    }
    for key, domains in CHARGER_OPTIONAL_ENTITIES.items():
        marker = VisibleOption(key, visible=SHOW_BONUS) if key == "bonus_export_ytd_entity" else vol.Optional(key)
        fields[marker] = entity(domains)
    for key, default, lower, upper in (("power_kw", 11, 1, 22), ("reserve_min", 15, 0, 60), ("minimum_min", 3, 1, 30), ("house_kw", 0.5, 0, 30), ("price_interval_min", 15, 15, 60)):
        fields[vol.Required(key, default=data.get(key, default))] = vol.All(vol.Coerce(float), vol.Range(min=lower, max=upper))
    for key, default in (("price_attribute", "data"), ("price_time_key", "time"), ("import_price_key", "price")):
        fields[vol.Required(key, default=data.get(key, default))] = str
    for key in ("connection_attribute", "export_price_key"):
        fields[vol.Optional(key, default="")] = str
    fields[vol.Required("maximum_command_kwh", default=data.get("maximum_command_kwh", 30))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=100))
    for key in ("economic_enabled", "idle_green_price_filter", "charger_fault_recovery"):
        fields[vol.Required(key, default=data.get(key,False))] = bool
    fields[vol.Required("early_offset_cents", default=data.get("early_offset_cents",0))] = selector.NumberSelector(selector.NumberSelectorConfig(min=0,max=100,step=.1,mode=selector.NumberSelectorMode.BOX,unit_of_measurement="ct/kWh"))
    fields[vol.Required("export_contract", default=data.get("export_contract", "unknown"))] = selector.SelectSelector(
        selector.SelectSelectorConfig(options=["unknown", "column", "zonneplan"], translation_key="export_contract")
    )
    for key,default,lower,upper in (("safe_power_kw",5.5,1,22),("maximum_manual_hours",8,1,48)):
        fields[vol.Required(key, default=data.get(key,default))] = vol.All(vol.Coerce(float),vol.Range(min=lower,max=upper))
    for key, default, lower, upper in (
        ("zonneplan_tax_eur_kwh", 0, 0, 1),
        ("zonneplan_import_fee_eur_kwh", .02, 0, 1),
        ("zonneplan_export_fee_eur_kwh", .02, -1, 1),
    ):
        fields[VisibleOption(key, default=data.get(key, default), visible=SHOW_ZONNEPLAN)] = vol.All(vol.Coerce(float), vol.Range(min=lower, max=upper))
    fields[VisibleOption("zonneplan_tax_valid_until", default=data.get("zonneplan_tax_valid_until", ""), visible=SHOW_ZONNEPLAN)] = str
    fields[VisibleOption("zonneplan_bonus_eligible", default=data.get("zonneplan_bonus_eligible", False), visible=SHOW_ZONNEPLAN)] = bool
    fields[VisibleOption("maximum_export_kw", default=data.get("maximum_export_kw", 22), visible=SHOW_BONUS)] = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))
    fields[VisibleOption("bonus_export_year", default=data.get("bonus_export_year", 0), visible=SHOW_BONUS)] = vol.All(vol.Coerce(int), vol.Range(min=0, max=2200))
    markers = {marker.schema: marker for marker in fields}
    for group, names in CHARGER_SECTIONS.items():
        required = group == "charger_connections" and not (data.get("meter_entity") and data.get("plug_entity"))
        schema = vol.Schema({markers[name]: fields.pop(markers[name]) for name in names})
        if group == "live_connection_identity" and uses_plug_identity(data):
            continue
        if required:
            group_key = vol.Required(group)
        else:
            # Home Assistant uses the whole default object if a section provides one.
            default = schema({}) if defaults is None else vol.UNDEFINED
            group_key = vol.Optional(group, default=default)
        fields[group_key] = section(schema, {"collapsed": group != "price_source" and not required})
    return vol.Schema(fields)


def charger_form_values(data: dict[str, Any]) -> dict[str, Any]:
    """Convert stored flat bindings to suggested values for the visible sections."""
    values = dict(data)
    for group, names in CHARGER_SECTIONS.items():
        values[group] = {name: values.pop(name) for name in names if name in values}
    return values


def charger_data(user_input: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Apply edits to flat entry data without dropping omitted sections or hidden fields."""
    data = dict(stored)
    data.update({name: value for name, value in user_input.items() if name not in CHARGER_SECTIONS})
    for group, names in CHARGER_SECTIONS.items():
        if group not in user_input:
            continue
        edits = user_input[group]
        data.update(edits)
        cleared = (CHARGER_OPTIONAL_ENTITIES.keys() & names) - edits.keys()
        if data.get("export_contract") != "zonneplan" or not data.get("zonneplan_bonus_eligible"):
            cleared.discard("bonus_export_ytd_entity")
        for name in cleared:
            data.pop(name, None)
    return data


def charger_source_errors(hass: Any, data: dict[str, Any]) -> dict[str, str]:
    """Show validation in the form even when the offending section is folded."""
    return {"base" if any(key in names for names in CHARGER_SECTIONS.values()) else key: message
            for key, message in source_errors(hass, data).items()}


def vehicle_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Only a configured limit-writer capability can suggest a higher limit."""
    data = defaults or {}
    # Keep stored values stable: the visible choices describe input semantics.
    fields: dict[Any, Any] = {
        vol.Required("name", default=data.get("name", "")): str,
        vol.Required("kind", default=data.get("kind", "tesla")): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["tesla", "id3"],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="range_calculation",
            )
        ),
    }
    for key, domains in {"soc_entity": ["sensor"], "battery_entity": ["sensor"], "range_entity": ["sensor"], "limit_entity": ["sensor", "number"], "cable_entity": ["binary_sensor"]}.items():
        fields[vol.Required(key, default=data.get(key, vol.UNDEFINED))] = entity(domains)
    for key, domains in {"home_entity": ["binary_sensor", "device_tracker"], "soh_entity": ["sensor"], "measurement_time_entity": ["sensor"], "limit_writer_entity": ["number"], "target_entity": ["input_number", "number"], "departure_entity": ["input_datetime", "datetime"], "native_plan_entity": ["sensor"], "vehicle_charge_switch_entity": ["switch"]}.items():
        fields[vol.Optional(key)] = entity(domains)
    fields[vol.Required("droop_pp", default=data.get("droop_pp", 1))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10))
    fields[vol.Required("margin", default=data.get("margin", 0.15))] = vol.All(vol.Coerce(float), vol.Range(min=0, max=0.5))
    fields[vol.Required("planning_power_kw", default=data.get("planning_power_kw", 0))] = PlanningPowerSelector(selector.NumberSelectorConfig(min=0, max=22, step=0.1, mode=selector.NumberSelectorMode.BOX))
    fields[vol.Required("allow_limit_raise", default=data.get("allow_limit_raise", False))] = bool
    fields[vol.Required("maximum_soc", default=data.get("maximum_soc", 100))] = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))
    return vol.Schema(fields)


class PreviewConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a separately installed observer keyed by its charger meter."""

    VERSION = 1
    MINOR_VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {"vehicle": VehicleSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        data = charger_settings(self.hass, charger_data(user_input, {})) if user_input is not None else {}
        errors = charger_source_errors(self.hass, data)
        if user_input is not None and not errors:
            charger_key = identity(self.hass, data["meter_entity"])
            await self.async_set_unique_id(charger_key)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=data["name"], data={**data, "charger_key": charger_key, "control_enabled": False})
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(charger_schema(), user_input), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        stored = charger_settings(self.hass, dict(entry.data))
        data = charger_settings(self.hass, charger_data(user_input, stored)) if user_input is not None else stored
        errors = charger_source_errors(self.hass, data) if user_input is not None else {}
        if user_input is not None and not errors:
            charger_key = identity(self.hass, data["meter_entity"])
            if not same_binding(entry.data.get("charger_key", entry.unique_id or ""), charger_key, entry.data["meter_entity"], data["meter_entity"]):
                return self.async_abort(reason="charger_identity_changed")
            if any(e.entry_id != entry.entry_id and e.unique_id == charger_key for e in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            if getattr(entry, "runtime_data", None) and entry.runtime_data.control.binding_change_blocked(data):
                return self.async_abort(reason="finish_control_cleanup_first")
            return self.async_update_and_abort(entry, unique_id=charger_key, title=data["name"], data={**data, "charger_key": charger_key, "control_enabled": False})
        return self.async_show_form(step_id="reconfigure", data_schema=self.add_suggested_values_to_schema(charger_schema(data), charger_form_values(data)), errors=errors)


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
