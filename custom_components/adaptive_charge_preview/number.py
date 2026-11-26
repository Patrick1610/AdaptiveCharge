"""Editable local preview values, never external helper values."""

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for vehicle_id in entry.runtime_data.profiles:
        async_add_entities([PreviewNumber(entry.runtime_data, vehicle_id, "target_range_km"), PreviewNumber(entry.runtime_data, vehicle_id, "droop_pp")], config_subentry_id=vehicle_id)


class PreviewNumber(PreviewEntity, NumberEntity):
    """Set a simulation override; the existing command isn't changed."""

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str, key: str) -> None:
        super().__init__(coordinator, key, vehicle_id)
        self.key = key
        self._attr_native_min_value = 0.1
        self._attr_native_max_value = 2000 if key == "target_range_km" else 10
        self._attr_native_step = 1 if key == "target_range_km" else 0.1
        self._attr_native_unit_of_measurement = "km" if key == "target_range_km" else "%"

    @property
    def native_value(self) -> float | None:
        if self.key == "target_range_km":
            return self.row.get("target_km")
        return self.coordinator.profile(self.vehicle_id).get("droop_pp", 1)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_preview_action("set_target", self.vehicle_id, {self.key: value})
