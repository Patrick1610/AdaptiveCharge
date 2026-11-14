"""Read-only explanation and plans. Predictions are never physical proof."""

from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.const import UnitOfTime
from .coordinator import PreviewCoordinator
from .entity import PreviewEntity
from .core.models import timestamp
from .core.schedule import planning_attributes


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([PreviewSensor(coordinator)])
    for vehicle_id in coordinator.profiles:
        async_add_entities([PreviewSensor(coordinator, vehicle_id), PlanningSensor(coordinator, vehicle_id)], config_subentry_id=vehicle_id)


class PreviewSensor(PreviewEntity, SensorEntity):
    """Expose the complete current calculation as attributes."""

    _attr_icon = "mdi:calendar-search"
    _unrecorded_attributes = frozenset({"observations", "plan", "session", "demand", "comparison"})

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str | None = None) -> None:
        super().__init__(coordinator, "preview", vehicle_id)
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = ["idle", "waiting_data", "watching", "charging", "paused", "scheduled", "would_boost", "verifying", "within_tolerance", "blocked", "fault", "cancelled", "disconnected"] if vehicle_id else ["read_only", "control_enabled", "stopping"]

    @property
    def native_value(self) -> str | None:
        if not self.vehicle_id:
            return "control_enabled" if self.row.get("control_enabled") else "read_only" if self.row.get("read_only", True) else "stopping"
        return self.row.get("phase", "waiting_data")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.vehicle_id:
            return self.row
        return {key: self.row.get(key) for key in ("control_enabled", "read_only", "control_status", "control_fault", "control_pending", "active_vehicle", "physical", "meter_kwh", "native_token", "limit_audit")}


class PlanningSensor(PreviewEntity, SensorEntity):
    """One duration sensor per vehicle, with the full future quarter schedule."""

    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1
    _unrecorded_attributes = frozenset({"quarters", "windows"})

    def __init__(self, coordinator: PreviewCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator, "planning", vehicle_id)
        self._planning_input = None
        self._planning_data: dict[str, Any] = {}

    @property
    def planning_data(self) -> dict[str, Any]:
        if self._planning_input is not self.coordinator.data:
            self._planning_input = self.coordinator.data
            now = timestamp((self.coordinator.data or {}).get("updated_at"))
            self._planning_data = planning_attributes(self.row, now)
        return self._planning_data

    @property
    def native_value(self) -> float | None:
        return self.planning_data.get("remaining_minutes")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**{key:value for key,value in self.planning_data.items() if key != "remaining_minutes"},
                "updated_at": (self.coordinator.data or {}).get("updated_at")}
