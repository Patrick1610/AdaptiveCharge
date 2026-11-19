"""AdaptiveCharge: read-only by default, with one explicitly enabled executor."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError, HomeAssistantError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import PreviewCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.DATETIME, Platform.SWITCH, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Load calculation plus optional own executor; never alter native configs."""
    if entry.data.get("control_enabled", False) is not False or entry.options.get("control_enabled", False) is not False:
        raise ConfigEntryError("Enable control using the master, not entry data")
    coordinator = PreviewCoordinator(hass, entry)
    try:
        await coordinator.async_initialize()
    except ValueError as exc:
        raise ConfigEntryError(str(exc)) from exc
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise

    entry.async_on_unload(entry.add_update_listener(_reload_preview))
    _register_actions(hass)


async def _reload_preview(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rebuild only this preview when vehicle bindings are added/removed/edited."""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_actions(hass: HomeAssistant) -> None:
    """Register own commands; only the master-authorized executor can actuate."""
    if hass.services.has_service(DOMAIN, "start"):
        return

    async def handle(call: ServiceCall) -> None:
        vehicle_id = call.data["vehicle_id"]
        entry_id = call.data.get("entry_id")
        matches = [c for key, c in hass.data.get(DOMAIN, {}).items() if isinstance(c, PreviewCoordinator) and (entry_id is None or key == entry_id) and vehicle_id in c.profiles]
        if len(matches) != 1:
            raise ServiceValidationError("Select one configured preview vehicle/charger")
        try:
            await matches[0].async_preview_action(call.service, vehicle_id, dict(call.data))
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc

    schema = vol.Schema({vol.Required("vehicle_id"): cv.string, vol.Optional("entry_id"): cv.string, vol.Optional("target_range_km"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2000)), vol.Optional("departure"): vol.Any(None, cv.string)})
    for action in ("start", "set_target", "stop", "follow_helpers"):
        selected_schema = vol.Schema({vol.Required("vehicle_id"): cv.string, vol.Optional("entry_id"): cv.string}) if action in {"stop", "follow_helpers"} else schema
        hass.services.async_register(DOMAIN, action, handle, schema=selected_schema)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove only this preview's entities/listeners and persist its own state."""
    await entry.runtime_data.control.prepare_unload()
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        raise HomeAssistantError("Preview platforms could not unload")
    await entry.runtime_data.async_shutdown()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        for action in ("start", "set_target", "stop", "follow_helpers"):
            hass.services.async_remove(DOMAIN, action)
        hass.data.pop(DOMAIN, None)
