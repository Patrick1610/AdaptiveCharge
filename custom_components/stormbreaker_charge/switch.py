"""Switch platform for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import StormbreakerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: StormbreakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ChargeNowSwitch(coordinator, entry),
            ChargeTonightSwitch(coordinator, entry),
            ChargingEnableSwitch(coordinator, entry),
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


class ChargeNowSwitch(RestoreEntity, SwitchEntity):
    """Switch to force charge immediately."""

    _attr_name = "Charge Now"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_now"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            self._coordinator.set_charge_now(state.state == "on")

    @property
    def is_on(self) -> bool:
        return self._coordinator._charge_now

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_now(True)
        self.async_write_ha_state()
        self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_now(False)
        self.async_write_ha_state()
        self._coordinator.async_request_refresh()


class ChargeTonightSwitch(RestoreEntity, SwitchEntity):
    """Switch to enable charge-tonight scheduling."""

    _attr_name = "Charge Tonight"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_tonight"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            self._coordinator.set_charge_tonight(state.state == "on")

    @property
    def is_on(self) -> bool:
        return self._coordinator._charge_tonight

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_tonight(True)
        self.async_write_ha_state()
        self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_tonight(False)
        self.async_write_ha_state()
        self._coordinator.async_request_refresh()


class ChargingEnableSwitch(RestoreEntity, SwitchEntity):
    """Virtual switch that mirrors the charging enabled state."""

    _attr_name = "Charging Enable"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StormbreakerCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charging_enable"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            self._coordinator.set_charging_enabled(state.state == "on")

    @property
    def is_on(self) -> bool:
        return self._coordinator._charging_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._coordinator._enable_charging()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._coordinator._disable_charging()
        self.async_write_ha_state()
