"""A visible, immutable control-disabled indicator."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ControlDisabled(entry.runtime_data)])


class ControlDisabled(PreviewEntity, BinarySensorEntity):
    """No switch or option can turn this preview into an actuator."""

    _attr_is_on = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PreviewCoordinator) -> None:
        super().__init__(coordinator, "control_enabled")
