"""Stable preview entity identities grouped under charger and vehicle subentry."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import PreviewCoordinator


class PreviewEntity(CoordinatorEntity[PreviewCoordinator]):
    """Common integration-owned entity; source entities are never edited."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PreviewCoordinator, key: str, vehicle_id: str | None = None) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        ident = vehicle_id or "charger"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{ident}_{key}"
        self._attr_translation_key = key
        title = coordinator.profiles[vehicle_id]["name"] if vehicle_id else coordinator.entry.title
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{ident}")}, name=title, manufacturer="AdaptiveCharge", model="Charger coordinator", sw_version=VERSION, via_device=(DOMAIN, f"{coordinator.entry.entry_id}_charger") if vehicle_id else None)

    @property
    def row(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return data.get("vehicles", {}).get(self.vehicle_id, {}) if self.vehicle_id else data
