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
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EV_BATTERY_ENERGY_SENSOR, CONF_EXPERT_MODE, DOMAIN
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
        # Forecast-to-EV capture factor (dedicated forecasting diagnostic)
        ForecastToEvCaptureFactorSensor(coordinator, entry),
        # Range thresholds
        RangeUpperLimitSensor(coordinator, entry),
        RangeLowerLimitSensor(coordinator, entry),
    ]

    # EV battery-side sensors (only when the battery energy sensor is configured)
    if options.get(CONF_EV_BATTERY_ENERGY_SENSOR):
        entities.extend([
            ChargingOverheadSensor(coordinator, entry),
            ChargingOverheadAvgSensor(coordinator, entry),
            BatteryEnergyDeltaSensor(coordinator, entry),
            EnergyNeededFullSensor(coordinator, entry),
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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_alignment_diagnostics"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled by default only when expert mode is active."""
        options = {**self._entry.data, **self._entry.options}
        return bool(options.get(CONF_EXPERT_MODE, False))

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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_input_skew_seconds"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled by default only when expert mode is active."""
        options = {**self._entry.data, **self._entry.options}
        return bool(options.get(CONF_EXPERT_MODE, False))

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
    def native_value(self) -> int:
        if self.coordinator.data is None:
            return 0
        return self.coordinator.data.get("current_setting") or 0


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
            "session_battery_delta_kwh": data.get("session_battery_delta_kwh"),
            "charging_overhead_pct": data.get("charging_overhead_pct"),
            "battery_pct": data.get("battery_pct"),
        }


# ---------------------------------------------------------------------------
# Solar-to-EV ratio sensor
# ---------------------------------------------------------------------------

class SolarToEvRatioSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Lifetime solar-to-EV ratio (dashboard KPI).

    Computed as: (energy_solar_wh / solar_production_total_wh) × 100, capped at 100 %.

    This is a **lifetime** metric that shows the cumulative percentage of solar
    production that was delivered to the EV across all sessions.  It is intended
    as a dashboard KPI for long-term tracking.

    Used by low-power forecast logic as a simple expected-yield factor:
    ``expected_ev_kwh = forecast_kwh × solar_to_ev_ratio``.
    """

    _attr_name = "Solar to EV Ratio"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_solar_to_ev_ratio"
        self._last_known_pct: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known value on HA restart/reload."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                self._last_known_pct = float(state.state)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is not None:
            ratio = self.coordinator.data.get("solar_to_ev_ratio")
            if ratio is not None:
                pct = round(ratio * 100, 2)
                self._last_known_pct = pct
                return pct
        # Keep graph continuity: use restored value while coordinator is
        # starting; if no history exists yet, return 0.0 instead of None.
        return self._last_known_pct if self._last_known_pct is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "energy_solar_kwh": data.get("energy_solar_kwh", 0.0),
            "solar_production_kwh": data.get("solar_production_kwh", 0.0),
            "solar_capture_factor": data.get("solar_capture_factor"),
            "low_power_active": data.get("low_power_active"),
            "low_power_threshold_pct": data.get("low_power_threshold_pct"),
        }


# ---------------------------------------------------------------------------
# Forecast-to-EV Capture Factor sensor
# ---------------------------------------------------------------------------

class ForecastToEvCaptureFactorSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Forecast-to-EV Capture Factor: fraction of EV-relevant solar opportunity captured.

    Semantics (independent from Solar-to-EV Ratio):
      forecast_to_ev_capture_factor = actual_ev_solar_capture / ev_relevant_opportunity

    Where:
      actual_ev_solar_capture  = cumulative solar energy that reached the EV
                                  while the EV was actively charging
      ev_relevant_opportunity  = cumulative solar surplus available to the EV
                                  only when: vehicle is present, battery < 90%,
                                  cable connected (if configured), and charging
                                  priority is not PRIORITY_EXPORT

    Intended use:
      estimated_usable_forecast = remaining_forecast_today_kwh × factor

    This is a stable cumulative factor (not a rapidly oscillating value).
    Range: 0.0–1.0.  Returns 0.0 when insufficient history exists.
    """

    _attr_name = "Forecast to EV Capture Factor"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunny-alert"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_forecast_to_ev_capture_factor"
        self._last_known_factor: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known value on HA restart/reload."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                self._last_known_factor = float(state.state)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float:
        if self.coordinator.data is not None:
            factor = self.coordinator.data.get("forecast_to_ev_capture_factor")
            if factor is not None:
                self._last_known_factor = factor
                return factor
        return self._last_known_factor if self._last_known_factor is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "forecast_capture_solar_kwh": data.get("forecast_capture_solar_kwh", 0.0),
            "forecast_capture_opportunity_kwh": data.get("forecast_capture_opportunity_kwh", 0.0),
            "solar_to_ev_ratio_pct": data.get("solar_to_ev_ratio"),
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
# Charging overhead sensor (requires EV battery energy sensor)
# ---------------------------------------------------------------------------

class ChargingOverheadSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Current-session charging overhead: (1 − battery_received / wall_energy) × 100.

    Measures the AC→DC conversion losses for the ongoing or most recently
    completed session.  Uses the wall energy snapshot (captured at the last
    battery sensor reading) and the battery delta for this session only.
    Only available when the EV battery energy sensor is configured.
    """

    _attr_name = "Charging Overhead"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_overhead_pct"
        self._last_known_pct: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known value on HA restart/reload."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                self._last_known_pct = float(state.state)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is not None:
            value = self.coordinator.data.get("charging_overhead_pct")
            if value is not None:
                self._last_known_pct = value
                return value
        # Keep graph continuity: show last session's overhead when cable is
        # disconnected; fallback to 0.0 when no history exists yet.
        return self._last_known_pct if self._last_known_pct is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "energy_session_kwh": data.get("energy_session_kwh", 0.0),
            "session_battery_delta_kwh": data.get("session_battery_delta_kwh"),
            "ev_battery_energy_kwh": data.get("ev_battery_energy_kwh"),
        }


class ChargingOverheadAvgSensor(RestoreEntity, _BaseAdaptiveChargeSensor):
    """Lifetime rolling average charging overhead: (1 − battery_received / wall_energy) × 100.

    Accumulates wall energy vs. battery energy delta across all completed
    sessions and updates once per session at cable disconnect.  Provides a
    stable long-run efficiency estimate even when a single session is too
    short to be representative.
    Only available when the EV battery energy sensor is configured.
    """

    _attr_name = "Charging Overhead Average"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_overhead_avg_pct"
        self._last_known_pct: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known value on HA restart/reload."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in ("unknown", "unavailable", ""):
            try:
                self._last_known_pct = float(state.state)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is not None:
            value = self.coordinator.data.get("charging_overhead_avg_pct")
            if value is not None:
                self._last_known_pct = value
                return value
        return self._last_known_pct if self._last_known_pct is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "charging_overhead_pct": data.get("charging_overhead_pct"),
        }



class EnergyNeededFullSensor(_BaseAdaptiveChargeSensor):
    """Estimated wall energy needed to reach 100% SoC including overhead."""

    _attr_name = "Energy Needed Full"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-arrow-up"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_needed_full_kwh"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("energy_needed_full_kwh")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "battery_pct": data.get("battery_pct"),
            "battery_capacity_kwh": data.get("battery_capacity_kwh"),
            "estimated_battery_capacity_kwh": data.get("estimated_battery_capacity_kwh"),
            "charging_overhead_pct": data.get("charging_overhead_pct"),
            "charging_overhead_avg_pct": data.get("charging_overhead_avg_pct"),
        }


# ---------------------------------------------------------------------------
# Battery energy delta sensor (requires EV battery energy sensor)
# ---------------------------------------------------------------------------

class BatteryEnergyDeltaSensor(_BaseAdaptiveChargeSensor):
    """Actual energy received by the battery in the current charging session.

    Computed from the EV battery energy remaining sensor: current − session_start.
    More accurate than wall-measured energy because it excludes AC→DC losses.
    """

    _attr_name = "Battery Energy Delta"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-plus-variant"

    def __init__(self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_energy_delta_kwh"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("session_battery_delta_kwh")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        base = super().extra_state_attributes
        return {
            **base,
            "energy_session_kwh": data.get("energy_session_kwh", 0.0),
            "charging_overhead_pct": data.get("charging_overhead_pct"),
            "ev_battery_energy_kwh": data.get("ev_battery_energy_kwh"),
        }
