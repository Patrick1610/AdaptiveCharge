"""A persisted charger master; OFF keeps all ordinary observations read-only."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """One master for the shared charger, never one competing master per car."""
    async_add_entities([ControlMaster(entry.runtime_data)])


class ControlMaster(PreviewEntity, SwitchEntity):
    """Enable authority; actual Start and all safety checks remain separate."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PreviewCoordinator) -> None:
        super().__init__(coordinator, "control_master")

    @property
    def is_on(self) -> bool:
        return self.coordinator.control.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.async_set_control_enabled(True)
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_control_enabled(False)
