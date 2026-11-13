"""Read-only explanation and plans. Predictions are never physical proof."""

from typing import Any
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([PreviewSensor(coordinator)])
    for vehicle_id in coordinator.profiles:
        async_add_entities([PreviewSensor(coordinator, vehicle_id)], config_subentry_id=vehicle_id)


class PreviewSensor(PreviewEntity, SensorEntity):
    """Expose the complete current calculation as attributes."""

    _attr_icon = "mdi:calendar-search"
    _unrecorded_attributes = frozenset({"observations", "plan", "session", "demand", "comparison"})

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str | None = None) -> None:
        super().__init__(coordinator, "preview", vehicle_id)

    @property
    def native_value(self) -> str | None:
        return self.row.get("phase", "read_only" if not self.vehicle_id else "waiting_data")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.vehicle_id:
            return self.row
        return {key: self.row.get(key) for key in ("control_enabled", "read_only", "active_vehicle", "physical", "meter_kwh", "native_token")}
