"""Sensor platform for AdaptiveCharge."""
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
from .coordinator import AdaptiveChargeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
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
            # Mode state machine
            ModeSensor(coordinator, entry),
            # Alignment / skew sensors
            InputSkewSensor(coordinator, entry),
            NetUpdateIntervalSensor(coordinator, entry),
            EvUpdateIntervalSensor(coordinator, entry),
            # Import guard
            ImportGuardStateSensor(coordinator, entry),
            ImportWattsSensor(coordinator, entry),
            # Diagnostics
            LastActionSensor(coordinator, entry),
            LastReasonSensor(coordinator, entry),
            TargetCurrentSensor(coordinator, entry),
            CurrentSettingSensor(coordinator, entry),
            AvailableCurrentDecisionSensor(coordinator, entry),
        ]
    )


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="AdaptiveCharge",
        manufacturer="AdaptiveCharge",
        model="EV Charge Controller",
        sw_version="2.0.0",
    )


class _BaseAdaptiveChargeSensor(CoordinatorEntity, SensorEntity):
    """Base class for AdaptiveCharge sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AdaptiveChargeCoordinator,
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


class SurplusExclEvSensor(_BaseAdaptiveChargeSensor):
    """Net surplus excluding EV consumption (W)."""

    _attr_name = "Net Surplus Excl EV (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_net_surplus_excl_ev_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("surplus_w", 0.0), 1)


class AvailableCurrentRawSensor(_BaseAdaptiveChargeSensor):
    """Available charge current raw (A). Deprecated: use EMA Current or Available Current Decision."""

    _attr_name = "Available Current Raw (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_excl_ev_a_raw"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("raw_current_a", 0.0), 2)


class AvailableCurrentRawFlooredSensor(_BaseAdaptiveChargeSensor):
    """Available charge current raw floored (A). Deprecated: use Current Setting."""

    _attr_name = "Available Charge Current Raw Floored (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_raw_floored_a"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("raw_floored", 0)


class AvailableCurrentSmoothedSensor(_BaseAdaptiveChargeSensor):
    """Available charge current smoothed (A). Deprecated: use EMA Current."""

    _attr_name = "Available Charge Current Smoothed (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_smoothed_a"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("smoothed_a", 0.0), 2)


class AvailableCurrentSmoothedFlooredSensor(_BaseAdaptiveChargeSensor):
    """Available charge current smoothed and floored (A). Deprecated: use Current Setting."""

    _attr_name = "Available Charge Current Smoothed Floored (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_smoothed_floored_a"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("smoothed_floored", 0)


class ComputedNetWSensor(_BaseAdaptiveChargeSensor):
    """Computed net power (W) used internally."""

    _attr_name = "Computed Net Power (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_computed_net_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("net_w", 0.0), 1)


class ComputedEvWSensor(_BaseAdaptiveChargeSensor):
    """Computed EV power (W) used internally."""

    _attr_name = "Computed EV Power (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_computed_ev_w"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("ev_w", 0.0), 1)


class VoltageUsedSensor(_BaseAdaptiveChargeSensor):
    """Voltage used for current calculation (V)."""

    _attr_name = "Voltage Used (V)"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_voltage_used_v"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("voltage", 230.0), 1)


class SolarDoneStatusSensor(_BaseAdaptiveChargeSensor):
    """Solar done status (on/off as string)."""

    _attr_name = "Solar Done Status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
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


class EMACurrentSensor(_BaseAdaptiveChargeSensor):
    """EMA-filtered charge current used for control decisions (A)."""

    _attr_name = "EMA Current (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ema_current_a"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("ema_current_a", 0.0)


class AlignmentDiagnosticSensor(_BaseAdaptiveChargeSensor):
    """Diagnostic sensor exposing alignment engine state."""

    _attr_name = "Alignment Diagnostics"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
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
            "settling_active": data.get("settling_active"),
            "confidence_level": data.get("confidence_level"),
            "measurement_coherence": data.get("measurement_coherence"),
            "estimated_skew_seconds": data.get("estimated_skew_seconds"),
            "estimated_lag_seconds": data.get("estimated_lag_seconds"),
            "net_update_interval_s": data.get("net_update_interval_s"),
            "ev_update_interval_s": data.get("ev_update_interval_s"),
            "voltage_update_interval_s": data.get("voltage_update_interval_s"),
            "net_update_interval_p95": data.get("net_update_interval_p95"),
            "ev_update_interval_p95": data.get("ev_update_interval_p95"),
            "last_sample_age_net_s": data.get("last_sample_age_net_s"),
            "last_sample_age_ev_s": data.get("last_sample_age_ev_s"),
            "last_applied_current_a": data.get("last_applied_current_a"),
            "committed_current": data.get("committed_current"),
            "last_control_reason": data.get("last_control_reason"),
            "ema_current_a": data.get("ema_current_a"),
        }


# ---------------------------------------------------------------------------
# Mode state machine sensor
# ---------------------------------------------------------------------------

class ModeSensor(_BaseAdaptiveChargeSensor):
    """Current charging mode (surplus / force_max / night_target / stopped / off)."""

    _attr_name = "Mode"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_mode"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        if not data.get("controller_enabled"):
            return "off"
        return data.get("current_mode", "stopped")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "mode_reason": data.get("mode_reason", ""),
            "mode_source": data.get("mode_source", ""),
            "mode_since": data.get("mode_since", ""),
            "last_transition": data.get("last_transition", ""),
        }


# ---------------------------------------------------------------------------
# Measurement alignment / skew sensors
# ---------------------------------------------------------------------------

class InputSkewSensor(_BaseAdaptiveChargeSensor):
    """Skew between net and EV sensor update timestamps (seconds)."""

    _attr_name = "Input Skew (s)"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_input_skew_seconds"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        skew = self.coordinator.data.get("estimated_skew_seconds")
        return round(skew, 2) if skew is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "alignment_ok": data.get("alignment_ok"),
            "alignment_reason": data.get("alignment_reason"),
            "net_update_interval_s": data.get("net_update_interval_s"),
            "ev_update_interval_s": data.get("ev_update_interval_s"),
        }


class NetUpdateIntervalSensor(_BaseAdaptiveChargeSensor):
    """Estimated update interval of the net power sensor (seconds)."""

    _attr_name = "Net Update Interval (s)"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_net_update_interval_seconds"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get("net_update_interval_s")
        return round(val, 2) if val is not None else None


class EvUpdateIntervalSensor(_BaseAdaptiveChargeSensor):
    """Estimated update interval of the EV power sensor (seconds)."""

    _attr_name = "EV Update Interval (s)"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_update_interval_seconds"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get("ev_update_interval_s")
        return round(val, 2) if val is not None else None


# ---------------------------------------------------------------------------
# Import guard sensors
# ---------------------------------------------------------------------------

class ImportGuardStateSensor(_BaseAdaptiveChargeSensor):
    """Import guard state: 'ok', 'reducing', or 'stopped'."""

    _attr_name = "Import Guard State"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_import_guard_state"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("import_guard_state", "ok")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "import_guard_reason": data.get("import_guard_reason", ""),
            "time_in_import_state": data.get("time_in_import_state", 0.0),
            "import_watts": data.get("import_watts", 0.0),
        }


class ImportWattsSensor(_BaseAdaptiveChargeSensor):
    """Grid import power used by the import guard (W, 0 when exporting)."""

    _attr_name = "Import Watts (W)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_import_watts"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.get("import_watts", 0.0), 1)


# ---------------------------------------------------------------------------
# Diagnostics sensors
# ---------------------------------------------------------------------------

class LastActionSensor(_BaseAdaptiveChargeSensor):
    """Last control action taken by the coordinator."""

    _attr_name = "Last Action"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_action"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("last_action") or "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "last_action_ts": data.get("last_action_ts"),
            "last_current_set_ts": data.get("last_current_set_ts"),
            "last_switch_toggle_ts": data.get("last_switch_toggle_ts"),
        }


class LastReasonSensor(_BaseAdaptiveChargeSensor):
    """Reason for the last control action."""

    _attr_name = "Last Reason"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_reason"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("last_reason") or "none"


class TargetCurrentSensor(_BaseAdaptiveChargeSensor):
    """Decision-level target current (A) before idempotency/rate limiting."""

    _attr_name = "Target Current (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_target_current"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("target_current", 0.0)


class CurrentSettingSensor(_BaseAdaptiveChargeSensor):
    """Last current value actually sent to the charger (A)."""

    _attr_name = "Current Setting (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_setting"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("current_setting")


class AvailableCurrentDecisionSensor(_BaseAdaptiveChargeSensor):
    """EMA-smoothed available current used for control decisions (A)."""

    _attr_name = "Available Current Decision (A)"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_available_current_decision"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("available_current", 0.0)
