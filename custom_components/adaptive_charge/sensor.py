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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info

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
            # Mode state machine
            ModeSensor(coordinator, entry),
            # Alignment / skew
            InputSkewSensor(coordinator, entry),
            # Import guard
            ImportGuardStateSensor(coordinator, entry),
            # Diagnostics
            LastActionSensor(coordinator, entry),
            LastReasonSensor(coordinator, entry),
            CurrentSettingSensor(coordinator, entry),
            AvailableCurrentDecisionSensor(coordinator, entry),
            AlignmentDiagnosticSensor(coordinator, entry),
        ]
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
        self._attr_device_info = device_info(entry)

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "net_w": data.get("net_w"),
            "ev_w": data.get("ev_w"),
            "voltage": data.get("voltage"),
            "raw_current_a": data.get("raw_current_a"),
            "force_charge": data.get("force_charge"),
        }


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
            "solar_done": data.get("solar_done"),
            "solar_w": data.get("solar_w"),
        }


# ---------------------------------------------------------------------------
# Measurement alignment / skew sensor
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


# ---------------------------------------------------------------------------
# Import guard sensor
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "target_current": data.get("target_current"),
            "force_charge": data.get("force_charge"),
        }
