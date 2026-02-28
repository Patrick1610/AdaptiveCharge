"""Shared helpers for AdaptiveCharge."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return shared DeviceInfo for all AdaptiveCharge entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="AdaptiveCharge",
        manufacturer="AdaptiveCharge",
        model="EV Charge Controller",
        sw_version="2.1.1",
    )
