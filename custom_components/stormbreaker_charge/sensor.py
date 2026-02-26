"""Sensor platform for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfPower
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
    """Set up sensor entities."""
    coordinator: StormbreakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SurplusExclEvSensor(coordinator, entry),
            AvailableCurrentRawSensor(coordinator, entry),
            AvailableCurrentRawFlooredSensor(coordinator, entry),
            AvailableCurrentSmoothedSensor(coordinator, entry),
            AvailableCurrentSmoothedFlooredSensor(coordinator, entry),
            EMACurrentSensor(coordinator, entry),
            ComputedNetWSensor(coordinator, entry),
            ComputedEvWSensor(coordinator, entry),
            VoltageUsedSensor(coordinator, entry),
            SolarDoneStatusSensor(coordinator, entry),
            AlignmentDiagnosticSensor(coordinator, entry),
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


class _BaseStormbreakerSensor(CoordinatorEntity, SensorEntity):
    """Base class for Stormbreaker sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StormbreakerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "mode": data.get("current_mode"),
            "last_action": data.get("last_action"),
            "last_updated": data.get("last_updated"),
            "charging_on": data.get("charging_on"),
            "sample_count": data.get("sample_count"),
        }


class SurplusExclEvSensor(_BaseStormbreakerSensor):
    """Net surplus excluding EV consumption (W)."""

    _attr_name = "Net Surplus Excl EV (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_net_surplus_excl_ev_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("surplus_w", 0.0), 1)


class AvailableCurrentRawSensor(_BaseStormbreakerSensor):
    """Available charge current raw (A)."""

    _attr_name = "Available Current Raw (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_excl_ev_a_raw"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("raw_current_a", 0.0), 2)


class AvailableCurrentRawFlooredSensor(_BaseStormbreakerSensor):
    """Available charge current raw floored (A)."""

    _attr_name = "Available Charge Current Raw Floored (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_raw_floored_a"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("raw_floored", 0)


class AvailableCurrentSmoothedSensor(_BaseStormbreakerSensor):
    """Available charge current smoothed (A)."""

    _attr_name = "Available Charge Current Smoothed (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_smoothed_a"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("smoothed_a", 0.0), 2)


class AvailableCurrentSmoothedFlooredSensor(_BaseStormbreakerSensor):
    """Available charge current smoothed and floored (A)."""

    _attr_name = "Available Charge Current Smoothed Floored (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_smoothed_floored_a"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("smoothed_floored", 0)


class ComputedNetWSensor(_BaseStormbreakerSensor):
    """Computed net power (W) used internally."""

    _attr_name = "Computed Net Power (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_computed_net_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("net_w", 0.0), 1)


class ComputedEvWSensor(_BaseStormbreakerSensor):
    """Computed EV power (W) used internally."""

    _attr_name = "Computed EV Power (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_computed_ev_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("ev_w", 0.0), 1)


class VoltageUsedSensor(_BaseStormbreakerSensor):
    """Voltage used for current calculation (V)."""

    _attr_name = "Voltage Used (V)"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_voltage_used_v"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("voltage", 230.0), 1)


class SolarDoneStatusSensor(_BaseStormbreakerSensor):
    """Solar done status (on/off as string)."""

    _attr_name = "Solar Done Status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_solar_done_status"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return "on" if self.coordinator.data.get("solar_done") else "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "solar_w": data.get("solar_w"),
        }


class EMACurrentSensor(_BaseStormbreakerSensor):
    """EMA-filtered charge current used for control decisions (A)."""

    _attr_name = "EMA Current (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ema_current_a"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("ema_current_a", 0.0)


class AlignmentDiagnosticSensor(_BaseStormbreakerSensor):
    """Diagnostic sensor exposing alignment engine state."""

    _attr_name = "Alignment Diagnostics"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: StormbreakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_alignment_diagnostics"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("confidence_level", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "alignment_active": data.get("alignment_active"),
            "confidence_level": data.get("confidence_level"),
            "estimated_lag_seconds": data.get("estimated_lag_seconds"),
            "net_update_interval_p95": data.get("net_update_interval_p95"),
            "ev_update_interval_p95": data.get("ev_update_interval_p95"),
            "committed_current": data.get("committed_current"),
            "last_commit_reason": data.get("last_commit_reason"),
            "ema_current_a": data.get("ema_current_a"),
        }
