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
        sw_version="3.0.0",
    )


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples: "45.0s", "5m 23s", "2h 15m 30s", "1d 3h 15m".
    """
    if seconds < 0:
        seconds = 0.0
    total = int(seconds)
    if total < 60:
        return f"{seconds:.1f}s"
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days > 0:
        parts = [f"{days}d", f"{hours}h", f"{minutes}m"]
        return " ".join(p for p in parts if not p.startswith("0"))
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"
