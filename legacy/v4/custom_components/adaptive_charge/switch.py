"""Switch platform for AdaptiveCharge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import AdaptiveChargeCoordinator
from .helpers import device_info, get_version

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: AdaptiveChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ControllerEnabledSwitch(coordinator, entry),
            ChargeNowSwitch(coordinator, entry),
            ChargeTonightSwitch(coordinator, entry),
        ]
    )



class ControllerEnabledSwitch(RestoreEntity, SwitchEntity):
    """Master switch that enables or disables the charge controller.

    Default: OFF on first install (safe).  Turning it OFF while charging
    triggers a controlled shutdown sequence.
    """

    _attr_name = "Controller Enabled"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_controller_enabled"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

    async def async_added_to_hass(self) -> None:
        """Restore previous state; default to off on first install."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            self._coordinator.set_controller_enabled(state.state == "on")
        # else: stays False (default off — safe)

    @property
    def is_on(self) -> bool:
        return self._coordinator._controller_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._coordinator.set_controller_enabled(True)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._coordinator.set_controller_enabled(False)
        self.async_write_ha_state()


class ChargeNowSwitch(RestoreEntity, SwitchEntity):
    """Switch to force charge immediately."""

    _attr_name = "Charge Now"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_now"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

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
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_now(False)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()


class ChargeTonightSwitch(RestoreEntity, SwitchEntity):
    """Switch to enable charge-tonight scheduling."""

    _attr_name = "Charge Tonight"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveChargeCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_tonight"
        self._attr_device_info = device_info(entry, get_version(coordinator.hass))

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            self._coordinator.set_charge_tonight(state.state == "on")
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self._coordinator._charge_tonight

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._coordinator.data or {}
        return {
            "tonight_reason": data.get("tonight_reason", ""),
            "tonight_condition": data.get("tonight_condition"),
            "tonight_reentry": data.get("tonight_reentry"),
            "need": data.get("need"),
            "solar_done": data.get("solar_done"),
            "presence": data.get("presence"),
            "cable_connected": data.get("cable_connected"),
            "force_source": data.get("force_source", ""),
            "effective_range": data.get("effective_range"),
            "current_range": data.get("current_range"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_tonight(True)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._coordinator.set_charge_tonight(False)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()



