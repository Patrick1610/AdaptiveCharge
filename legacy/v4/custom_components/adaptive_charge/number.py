"""Number platform for AdaptiveCharge."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_DESIRED_RANGE, DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info, get_version

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DesiredRangeNumber(coordinator, entry),
        ]
    )



class DesiredRangeNumber(RestoreEntity, NumberEntity):
    """Number entity for the desired vehicle range in km."""

    _attr_name = "Desired Range"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "km"
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_desired_range_km"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

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
