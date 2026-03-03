"""Binary sensor platform for AdaptiveCharge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info, get_version

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ForceChargeSensor(coordinator, entry),
        ChargingActiveSensor(coordinator, entry),
        LowPowerActiveSensor(coordinator, entry),
    ])


class ForceChargeSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating whether force charge is active."""

    _attr_name = "Force Charge"
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_force_charge"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

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
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charging_active"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

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


class LowPowerActiveSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating whether the low-power forced charge is active.

    True when the vehicle battery SoC is below the configured threshold AND
    the solar forecast does not promise enough generation to cover the shortfall.
    """

    _attr_name = "Low Power Active"
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_low_power_active"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("low_power_active", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "battery_pct": data.get("battery_pct"),
            "low_power_threshold_pct": data.get("low_power_threshold_pct"),
            "forecast_kwh": data.get("forecast_kwh"),
            "low_power_forecast_threshold_kwh": data.get("low_power_forecast_threshold_kwh"),
            "force_source": data.get("force_source"),
        }
