"""Preview-owned diagnostics; no credentials or unrelated HA state are read."""

from copy import deepcopy
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Only configured observations and the current explanation are exported."""
    control = entry.runtime_data.control
    return {"control_enabled": control.enabled, "read_only": not control.enabled and not control.owned_manual and not control.has_limits, "preview": deepcopy(entry.runtime_data.data)}
