"""Number platform for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_DESIRED_RANGE, DEFAULT_MAX_CURRENT_LIMIT, DOMAIN
from .coordinator import StormbreakerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: StormbreakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DesiredRangeNumber(coordinator, entry),
            MaxCurrentLimitNumber(coordinator, entry),
        ]
    )


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stormbreaker Surplus EV Charge",
        manufacturer="Stormbreaker Surplus",
        model="EV Charge Controller",
        sw_version="1.0.0",
    )


class DesiredRangeNumber(RestoreEntity, NumberEntity):
    """Number entity for the desired vehicle range in km."""

    _attr_name = "Desired Range (km)"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "km"
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_desired_range_km"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._coordinator.set_desired_range(float(state.state))
            except (ValueError, TypeError):
                self._coordinator.set_desired_range(DEFAULT_DESIRED_RANGE)

    @property
    def native_value(self) -> float:
        return self._coordinator._desired_range

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.set_desired_range(value)
        self.async_write_ha_state()


class MaxCurrentLimitNumber(RestoreEntity, NumberEntity):
    """Number entity for the maximum charge current in A."""

    _attr_name = "Max Current Limit (A)"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 16
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_current_limit_a"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._coordinator.set_max_current_limit(float(state.state))
            except (ValueError, TypeError):
                self._coordinator.set_max_current_limit(DEFAULT_MAX_CURRENT_LIMIT)

    @property
    def native_value(self) -> float:
        return self._coordinator._max_current_limit

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.set_max_current_limit(value)
        self.async_write_ha_state()
