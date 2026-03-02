"""The AdaptiveCharge integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_DISABLE_TONIGHT,
    SERVICE_ENABLE_TONIGHT,
    SERVICE_FORCE_START,
    SERVICE_FORCE_STOP,
    SERVICE_SET_DESIRED_RANGE,
)
from .coordinator import AdaptiveChargeCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_DESIRED_RANGE_SCHEMA = vol.Schema(
    {
        vol.Required("range_km"): vol.Coerce(float),
        vol.Optional("entry_id"): cv.string,
    }
)

SERVICE_FORCE_START_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
    }
)


def _get_coordinator(hass: HomeAssistant, call: ServiceCall) -> AdaptiveChargeCoordinator | None:
    """Retrieve coordinator from service call data or default to first entry."""
    entry_id = call.data.get("entry_id")
    domain_data = hass.data.get(DOMAIN, {})
    if entry_id:
        return domain_data.get(entry_id)
    for value in domain_data.values():
        if isinstance(value, AdaptiveChargeCoordinator):
            return value
    return None


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AdaptiveCharge from a config entry."""
    # Remove stale charging_enable switch entity (replaced by binary_sensor.charging_active)
    registry = er.async_get(hass)
    stale_unique_id = f"{entry.entry_id}_charging_enable"
    stale_entity_id = registry.async_get_entity_id("switch", DOMAIN, stale_unique_id)
    if stale_entity_id:
        _LOGGER.info("Removing stale entity %s", stale_entity_id)
        registry.async_remove(stale_entity_id)

    # Load integration version from manifest (dynamic, not hardcoded)
    domain_data = hass.data.setdefault(DOMAIN, {})
    if "version" not in domain_data:
        try:
            integration = await async_get_integration(hass, DOMAIN)
            # Coerce to plain str — integration.version returns an AwesomeVersion
            # object in newer HA.  Passing an AwesomeVersion to DeviceInfo causes
            # AwesomeVersion.__ne__() to be called during device-registry update,
            # which crashes when the previously-stored sw_version is unparseable.
            domain_data["version"] = str(integration.version)
        except Exception:
            _LOGGER.debug("Could not load integration version")
            domain_data["version"] = None

    # Clean up corrupted sw_version="unknown" that was stored in the device
    # registry by v3.1.3/v3.1.4.  "unknown" is not parseable by AwesomeVersion;
    # comparing *any* new sw_version against it raises
    # AwesomeVersionCompareException and blocks every entity from registering.
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if device is not None and device.sw_version is not None:
        try:
            sw = str(device.sw_version)
            if sw == "unknown" or sw == "":
                device_registry.async_update_device(device.id, sw_version=None)
        except Exception:
            _LOGGER.debug("Could not clean up device sw_version")

    coordinator = AdaptiveChargeCoordinator(hass, entry)
    domain_data[entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload integration when options change (so updated entity selections take effect)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # --- Register services (only once) ---
    if not hass.services.has_service(DOMAIN, SERVICE_FORCE_START):

        async def handle_force_start(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call)
            if coord:
                await coord.async_service_force_start()

        async def handle_force_stop(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call)
            if coord:
                await coord.async_service_force_stop()

        async def handle_set_desired_range(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call)
            if coord:
                await coord.async_service_set_desired_range(call.data["range_km"])

        async def handle_enable_tonight(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call)
            if coord:
                await coord.async_service_enable_tonight()

        async def handle_disable_tonight(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call)
            if coord:
                await coord.async_service_disable_tonight()

        hass.services.async_register(
            DOMAIN, SERVICE_FORCE_START, handle_force_start, schema=SERVICE_FORCE_START_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_FORCE_STOP, handle_force_stop, schema=SERVICE_FORCE_START_SCHEMA
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_DESIRED_RANGE,
            handle_set_desired_range,
            schema=SERVICE_SET_DESIRED_RANGE_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ENABLE_TONIGHT, handle_enable_tonight, schema=SERVICE_FORCE_START_SCHEMA
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_TONIGHT,
            handle_disable_tonight,
            schema=SERVICE_FORCE_START_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove services and clean up domain data if no more coordinator entries
        has_entries = any(
            isinstance(v, AdaptiveChargeCoordinator)
            for v in hass.data[DOMAIN].values()
        )
        if not has_entries:
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_START)
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_STOP)
            hass.services.async_remove(DOMAIN, SERVICE_SET_DESIRED_RANGE)
            hass.services.async_remove(DOMAIN, SERVICE_ENABLE_TONIGHT)
            hass.services.async_remove(DOMAIN, SERVICE_DISABLE_TONIGHT)
            hass.data.pop(DOMAIN, None)

    return unload_ok
