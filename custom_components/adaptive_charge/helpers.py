"""Shared helpers for AdaptiveCharge."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def get_version(hass: HomeAssistant) -> str | None:
    """Return the integration version stored during setup, or None if unavailable."""
    domain_data = hass.data.get(DOMAIN) or {}
    version = domain_data.get("version")
    # Return None for missing or placeholder values — "unknown" is not a valid
    # AwesomeVersion string and causes HA's device registry comparison to fail.
    return version if version and version != "unknown" else None


def device_info(entry: ConfigEntry, version: str | None = None) -> DeviceInfo:
    """Return shared DeviceInfo for all AdaptiveCharge entities."""
    # Only include sw_version when we have a real version string.  Passing
    # "unknown" (or None) causes HA's awesomeversion comparison to raise
    # AwesomeVersionCompareException and blocks every entity from registering.
    extra: dict = {"sw_version": version} if version else {}
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="AdaptiveCharge",
        model="EV Charge Controller",
        **extra,
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
