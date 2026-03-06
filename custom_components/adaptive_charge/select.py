"""Select platform for AdaptiveCharge."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    PRIORITY_BALANCE,
    PRIORITY_EXPORT,
    PRIORITY_IMPORT,
    PRIORITY_ZERO_PREFER_EXPORT,
    PRIORITY_ZERO_PREFER_IMPORT,
)
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info, get_version

_LOGGER = logging.getLogger(__name__)

_PRIORITY_OPTIONS = [
    PRIORITY_BALANCE,
    PRIORITY_ZERO_PREFER_EXPORT,
    PRIORITY_ZERO_PREFER_IMPORT,
    PRIORITY_EXPORT,
    PRIORITY_IMPORT,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ChargingPrioritySelect(coordinator, entry),
        ]
    )


class ChargingPrioritySelect(RestoreEntity, SelectEntity):
    """Select entity to choose the charging priority mode.

    Options:
      balance             — aim for exactly 0 W net (pure surplus, original default)
      zero_prefer_export  — require surplus above the bias before starting; prefers export
      zero_prefer_import  — allow slight import as neutral; bias toward charging
      export_priority     — don't charge at all; maximise grid export
      import_priority     — charge at maximum current regardless of solar
    """

    _attr_name = "Charging Priority"
    _attr_has_entity_name = True
    _attr_options = _PRIORITY_OPTIONS

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charging_priority"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state in _PRIORITY_OPTIONS:
            self._coordinator.set_charging_priority(state.state)
        # Default is already PRIORITY_BALANCE (set in coordinator __init__)

    @property
    def current_option(self) -> str:
        return self._coordinator.charging_priority

    async def async_select_option(self, option: str) -> None:
        self._coordinator.set_charging_priority(option)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()
