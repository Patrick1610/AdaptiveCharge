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
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import CONF_ENABLE_UTILITY_METERS, CONF_UTILITY_DAILY, CONF_UTILITY_MONTHLY, CONF_UTILITY_YEARLY, DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info, get_version

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    options = {**entry.data, **entry.options}
    entities: list[SensorEntity] = [
        SurplusExclEvSensor(coordinator, entry),
        # Mode state machine
        ModeSensor(coordinator, entry),
        # Alignment / skew (disabled by default, data available as attribute)
        InputSkewSensor(coordinator, entry),
        # Import guard
        ImportGuardStateSensor(coordinator, entry),
        # Diagnostics
        LastActionSensor(coordinator, entry),
        LastReasonSensor(coordinator, entry),
        CurrentSettingSensor(coordinator, entry),
        AvailableCurrentDecisionSensor(coordinator, entry),
        AlignmentDiagnosticSensor(coordinator, entry),
        # Integration version (diagnostic)
        VersionSensor(coordinator, entry),
        # Energy tracking
        EnergyChargedSensor(coordinator, entry),
        # Solar-to-EV ratio (used by low-power protection)
        SolarToEvRatioSensor(coordinator, entry),
        # Range thresholds
        RangeUpperLimitSensor(coordinator, entry),
        RangeLowerLimitSensor(coordinator, entry),
    ]

    if options.get(CONF_ENABLE_UTILITY_METERS, False):
        daily = options.get(CONF_UTILITY_DAILY, True)
        monthly = options.get(CONF_UTILITY_MONTHLY, True)
        yearly = options.get(CONF_UTILITY_YEARLY, True)

        if daily:
            entities.extend([
                EnergyChargedDailySensor(coordinator, entry),
            ])
        if monthly:
            entities.extend([
                EnergyChargedMonthlySensor(coordinator, entry),
            ])
        if yearly:
            entities.extend([
                EnergyChargedYearlySensor(coordinator, entry),
            ])

    async_add_entities(entities)


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
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

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

    _attr_name = "Net Surplus Excl EV"
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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
            "alignment_ok": data.get("alignment_ok"),
            "alignment_reason": data.get("alignment_reason"),
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
# Version diagnostic sensor
# ---------------------------------------------------------------------------

class VersionSensor(_BaseAdaptiveChargeSensor):
    """Diagnostic sensor showing the running integration version."""

    _attr_name = "Version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_version"

    @property
    def native_value(self) -> str:
        return get_version(self.coordinator.hass) or "unknown"


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

    _attr_name = "Input Skew"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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

    _attr_name = "Current Setting"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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

    _attr_name = "Available Current Decision"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_registry_enabled_default = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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


# ---------------------------------------------------------------------------
# Energy tracking sensors
# ---------------------------------------------------------------------------

class EnergyChargedSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Cumulative energy charged to the EV (kWh), with solar/import split."""

    _attr_name = "Energy Charged"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_charged_kwh"

    async def async_added_to_hass(self) -> None:
        """Restore cumulative energy on HA restart."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                total = float(state.state)
                attrs = state.attributes or {}
                solar = float(attrs.get("solar_energy_kwh", 0))
                import_e = float(attrs.get("import_energy_kwh", 0))
                self.coordinator.restore_energy_state(
                    total * 1000.0, solar * 1000.0, import_e * 1000.0
                )
                # Seed persistent store energy if not yet migrated
                store = self.coordinator.store
                if not store.migrated:
                    store.seed_from_old_state(
                        energy_total_wh=total * 1000.0,
                        energy_solar_wh=solar * 1000.0,
                        energy_import_wh=import_e * 1000.0,
                    )
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("energy_total_kwh", 0.0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "solar_energy_kwh": data.get("energy_solar_kwh", 0.0),
            "import_energy_kwh": data.get("energy_import_kwh", 0.0),
            "session_energy_kwh": data.get("energy_session_kwh", 0.0),
            "session_solar_kwh": data.get("energy_session_solar_kwh", 0.0),
            "session_import_kwh": data.get("energy_session_import_kwh", 0.0),
            "battery_pct": data.get("battery_pct"),
        }


# ---------------------------------------------------------------------------
# Solar-to-EV ratio sensor
# ---------------------------------------------------------------------------

class SolarToEvRatioSensor(_BaseAdaptiveChargeSensor):
    """Lifetime ratio of solar energy that reached the EV vs total solar produced.

    Computed as: energy_solar_wh / solar_production_total_wh (capped at 1.0).
    Used internally by low-power protection to estimate how much of the remaining
    solar forecast will actually reach the car.
    """

    _attr_name = "Solar to EV Ratio"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_solar_to_ev_ratio"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("solar_to_ev_ratio")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "energy_solar_kwh": data.get("energy_solar_kwh", 0.0),
            "solar_production_kwh": data.get("solar_production_kwh", 0.0),
            "low_power_active": data.get("low_power_active"),
            "low_power_threshold_pct": data.get("low_power_threshold_pct"),
        }


# ---------------------------------------------------------------------------
# Range threshold sensors: upper limit (stop) and lower limit (start)
# ---------------------------------------------------------------------------

def _range_threshold_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Common attributes for range threshold sensors."""
    return {
        "desired_range": data.get("desired_range"),
        "charge_buffer_pct": data.get("charge_buffer"),
        "effective_range": data.get("effective_range"),
        "range_hysteresis_pct": data.get("range_hysteresis_pct"),
        "range_hysteresis_km": data.get("range_hysteresis_km"),
        "current_range": data.get("current_range"),
        "need": data.get("need"),
    }


class RangeUpperLimitSensor(_BaseAdaptiveChargeSensor):
    """Upper charge threshold: charging stops when current range reaches this value.

    upper_limit = effective_range (desired + buffer)
    """

    _attr_name = "Range Upper Limit"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "km"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_range_upper_limit_km"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        effective = self.coordinator.data.get("effective_range")
        if effective is None:
            return None
        # Asymmetric hysteresis: upper limit equals effective_range (desired + buffer)
        return round(effective, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _range_threshold_attributes(self.coordinator.data or {})


class RangeLowerLimitSensor(_BaseAdaptiveChargeSensor):
    """Lower charge threshold: charging starts when current range drops below this value.

    lower_limit = effective_range - hysteresis_km
    """

    _attr_name = "Range Lower Limit"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "km"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_range_lower_limit_km"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        effective = self.coordinator.data.get("effective_range")
        hyst = self.coordinator.data.get("range_hysteresis_km")
        if effective is None or hyst is None:
            return None
        return round(effective - hyst, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _range_threshold_attributes(self.coordinator.data or {})


# ---------------------------------------------------------------------------
# Utility meter sensors (daily / monthly / yearly)
# ---------------------------------------------------------------------------

class _UtilityMeterSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Base utility meter sensor that resets on period boundaries."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_entity_registry_enabled_default = True

    _source_key: str = ""
    _period: str = ""  # "daily", "monthly", "yearly"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._accumulated: float = 0.0
        self._last_source_value: float | None = None
        self._unsub_reset = None

    async def async_added_to_hass(self) -> None:
        """Restore state and schedule resets."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                self._accumulated = float(state.state)
            except (ValueError, TypeError):
                pass
        self._schedule_reset()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel scheduled reset."""
        if self._unsub_reset:
            self._unsub_reset()
            self._unsub_reset = None

    def _schedule_reset(self) -> None:
        """Schedule periodic reset based on period type."""
        if self._unsub_reset:
            self._unsub_reset()
        if self._period == "daily":
            self._unsub_reset = async_track_time_change(
                self.hass, self._async_reset, hour=0, minute=0, second=0,
            )
        elif self._period == "monthly":
            self._unsub_reset = async_track_time_change(
                self.hass, self._async_check_monthly_reset, hour=0, minute=0, second=0,
            )
        elif self._period == "yearly":
            self._unsub_reset = async_track_time_change(
                self.hass, self._async_check_yearly_reset, hour=0, minute=0, second=0,
            )

    @callback
    def _async_reset(self, _now) -> None:
        """Reset the accumulated value."""
        self._accumulated = 0.0
        self._last_source_value = None
        self.async_write_ha_state()

    @callback
    def _async_check_monthly_reset(self, _now) -> None:
        """Reset on the first day of each month."""
        if dt_util.now().day == 1:
            self._async_reset(_now)

    @callback
    def _async_check_yearly_reset(self, _now) -> None:
        """Reset on January 1st."""
        now = dt_util.now()
        if now.month == 1 and now.day == 1:
            self._async_reset(_now)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        source_value = self.coordinator.data.get(self._source_key, 0.0)
        if self._last_source_value is not None:
            delta = source_value - self._last_source_value
            if delta > 0:
                self._accumulated += delta
        self._last_source_value = source_value
        return round(self._accumulated, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source_key}


class EnergyChargedDailySensor(_UtilityMeterSensor):
    """Daily energy charged utility meter."""

    _attr_name = "Energy Charged Daily"
    _source_key = "energy_total_kwh"
    _period = "daily"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_charged_daily"


class EnergyChargedMonthlySensor(_UtilityMeterSensor):
    """Monthly energy charged utility meter."""

    _attr_name = "Energy Charged Monthly"
    _source_key = "energy_total_kwh"
    _period = "monthly"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_charged_monthly"


class EnergyChargedYearlySensor(_UtilityMeterSensor):
    """Yearly energy charged utility meter."""

    _attr_name = "Energy Charged Yearly"
    _source_key = "energy_total_kwh"
    _period = "yearly"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_charged_yearly"
