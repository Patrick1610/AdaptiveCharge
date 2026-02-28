"""Time platform for AdaptiveCharge."""
from __future__ import annotations

import logging
from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEFAULT_NIGHT_OFF_HOUR,
    DEFAULT_NIGHT_OFF_MINUTE,
    DEFAULT_TONIGHT_START_HOUR,
    DEFAULT_TONIGHT_START_MINUTE,
    DOMAIN,
)
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up time entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EarliestChargeStartTime(coordinator, entry),
            NightOffTime(coordinator, entry),
        ]
    )


class EarliestChargeStartTime(RestoreEntity, TimeEntity):
    """Time entity for the earliest tonight charge start hour."""

    _attr_name = "Earliest Charge Start"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_earliest_charge_start"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unavailable", "unknown", ""):
            try:
                parts = state.state.split(":")
                h, m = int(parts[0]), int(parts[1])
                self._coordinator.set_tonight_start_time(h, m)
            except (ValueError, TypeError, IndexError):
                self._coordinator.set_tonight_start_time(
                    DEFAULT_TONIGHT_START_HOUR, DEFAULT_TONIGHT_START_MINUTE
                )

    @property
    def native_value(self) -> dt_time | None:
        return dt_time(
            self._coordinator._tonight_start_hour,
            self._coordinator._tonight_start_minute,
        )

    async def async_set_value(self, value: dt_time) -> None:
        self._coordinator.set_tonight_start_time(value.hour, value.minute)
        self.async_write_ha_state()


class NightOffTime(RestoreEntity, TimeEntity):
    """Time entity for the nightly charge_tonight reset time."""

    _attr_name = "Night Off Time"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_night_off_time"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unavailable", "unknown", ""):
            try:
                parts = state.state.split(":")
                h, m = int(parts[0]), int(parts[1])
                self._coordinator.set_night_off_time(h, m)
            except (ValueError, TypeError, IndexError):
                self._coordinator.set_night_off_time(
                    DEFAULT_NIGHT_OFF_HOUR, DEFAULT_NIGHT_OFF_MINUTE
                )

    @property
    def native_value(self) -> dt_time | None:
        return dt_time(
            self._coordinator._night_off_hour,
            self._coordinator._night_off_minute,
        )

    async def async_set_value(self, value: dt_time) -> None:
        self._coordinator.set_night_off_time(value.hour, value.minute)
        self.async_write_ha_state()
