"""Own-namespace preview departure; does not write the live departure helper."""

from datetime import datetime, timezone
from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .coordinator import PreviewCoordinator
from .entity import PreviewEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for vehicle_id in entry.runtime_data.profiles:
        async_add_entities([PreviewDeparture(entry.runtime_data, vehicle_id)], config_subentry_id=vehicle_id)


class PreviewDeparture(PreviewEntity, DateTimeEntity):
    """Store UTC instants while HA renders local time."""

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator, "departure", vehicle_id)

    @property
    def native_value(self) -> datetime | None:
        value = self.row.get("departure")
        return datetime.fromtimestamp(value, timezone.utc) if value is not None else None

    async def async_set_value(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("Preview departure requires a timezone")
        await self.coordinator.async_preview_action("set_target", self.vehicle_id, {"departure": value.isoformat()})
