"""Per-vehicle commands; actual hardware authority comes from the shared master."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Use real entities so dashboard actions need no templated vehicle IDs."""
    for vehicle_id in entry.runtime_data.profiles:
        async_add_entities([CommandButton(entry.runtime_data, vehicle_id, action) for action in ("start", "stop")], config_subentry_id=vehicle_id)


class CommandButton(PreviewEntity, ButtonEntity):
    """Start/Stop address the selected stable subentry only."""

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str, action: str) -> None:
        super().__init__(coordinator, action, vehicle_id)
        self.action = action
        self._attr_icon = "mdi:play-circle" if action == "start" else "mdi:stop-circle"

    async def async_press(self) -> None:
        """Persist the command before any positive or negative hardware effect."""
        try:
            await self.coordinator.async_preview_action(self.action, self.vehicle_id, {})
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc
