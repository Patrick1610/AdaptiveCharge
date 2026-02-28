"""Number platform for AdaptiveCharge."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_CHARGE_BUFFER, DEFAULT_DESIRED_RANGE, DEFAULT_MAX_CURRENT_LIMIT, DEFAULT_RANGE_HYSTERESIS_PCT, DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info

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
            MaxCurrentLimitNumber(coordinator, entry),
            ChargeBufferNumber(coordinator, entry),
            RangeHysteresisNumber(coordinator, entry),
        ]
    )



class DesiredRangeNumber(RestoreEntity, NumberEntity):
    """Number entity for the desired vehicle range in km."""

    _attr_name = "Desired Range (km)"
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
        self._attr_device_info = device_info(entry)

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


class MaxCurrentLimitNumber(RestoreEntity, NumberEntity):
    """Number entity for the maximum charge current in A."""

    _attr_name = "Max Current Limit (A)"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 16
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_current_limit_a"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._coordinator.set_max_current_limit(float(state.state))
            except (ValueError, TypeError):
                self._coordinator.set_max_current_limit(DEFAULT_MAX_CURRENT_LIMIT)

    @property
    def native_value(self) -> float:
        return self._coordinator._max_current_limit

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.set_max_current_limit(value)
        self.async_write_ha_state()


class ChargeBufferNumber(RestoreEntity, NumberEntity):
    """Number entity for the charge buffer percentage."""

    _attr_name = "Charge Buffer (%)"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 25
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_buffer_pct"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._coordinator.set_charge_buffer(float(state.state))
            except (ValueError, TypeError):
                self._coordinator.set_charge_buffer(DEFAULT_CHARGE_BUFFER)

    @property
    def native_value(self) -> float:
        return self._coordinator._charge_buffer

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.set_charge_buffer(value)
        self.async_write_ha_state()


class RangeHysteresisNumber(RestoreEntity, NumberEntity):
    """Number entity for the range hysteresis percentage."""

    _attr_name = "Range Hysteresis (%)"
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_range_hysteresis_pct"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._coordinator.set_range_hysteresis_pct(float(state.state))
            except (ValueError, TypeError):
                self._coordinator.set_range_hysteresis_pct(DEFAULT_RANGE_HYSTERESIS_PCT)

    @property
    def native_value(self) -> float:
        return self._coordinator._range_hysteresis_pct

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.set_range_hysteresis_pct(value)
        self.async_write_ha_state()
