"""Preview-owned diagnostics; no credentials or unrelated HA state are read."""

from copy import deepcopy
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Only configured observations and the current explanation are exported."""
    return {"control_enabled": False, "read_only": True, "preview": deepcopy(entry.runtime_data.data)}
