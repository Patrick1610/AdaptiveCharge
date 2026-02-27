"""Binary sensor platform for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StormbreakerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: StormbreakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ForceChargeSensor(coordinator, entry),
        ChargingActiveSensor(coordinator, entry),
    ])


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stormbreaker Surplus EV Charge",
        manufacturer="Stormbreaker Surplus",
        model="EV Charge Controller",
        sw_version="1.0.0",
    )


class ForceChargeSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating whether force charge is active."""

    _attr_name = "Force Charge"
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_force_charge"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("force_charge", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "reason": "charge_now switch is on" if data.get("force_charge") else "charge_now switch is off",
            "charge_now": data.get("charge_now"),
            "current_mode": data.get("current_mode"),
            "last_action": data.get("last_action"),
            "last_updated": data.get("last_updated"),
        }


class ChargingActiveSensor(CoordinatorEntity, BinarySensorEntity):
    """Read-only binary sensor showing if the integration is actively controlling charging.

    True only when the coordinator has started charging (not merely 'cable connected').
    """

    _attr_name = "Charging Active"
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charging_active"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("charging_active", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "controller_enabled": data.get("controller_enabled"),
            "current_mode": data.get("current_mode"),
            "last_action": data.get("last_action"),
            "last_reason": data.get("last_reason"),
        }
