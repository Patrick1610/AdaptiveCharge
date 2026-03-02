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
    # Coerce to str() because integration.version may be an AwesomeVersion object.
    if not version:
        return None
    version_str = str(version)
    return version_str if version_str != "unknown" else None


def device_info(entry: ConfigEntry, version: str | None = None) -> DeviceInfo:
    """Return shared DeviceInfo for all AdaptiveCharge entities.

    ``sw_version`` is intentionally **never** included.  When a device already
    exists in the registry with a corrupted or non-parseable ``sw_version``
    (e.g. from a previous release), passing *any* new value triggers an
    ``AwesomeVersionCompareException`` inside the device-registry update path
    and prevents every entity from registering.  The integration version is
    already surfaced via the dedicated *Version* sensor entity.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="AdaptiveCharge",
        model="EV Charge Controller",
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
