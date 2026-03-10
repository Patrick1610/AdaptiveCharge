"""Persistent storage for AdaptiveCharge counters.

Uses homeassistant.helpers.storage.Store to persist energy
counters to `.storage/adaptive_charge.counters` so that:
  - Data survives HA restart / reload / reboot.
  - Disabled entities can later be enabled and show correct values immediately.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "adaptive_charge.counters"

# Minimum interval (seconds) between disk writes to avoid I/O spam.
_FLUSH_THROTTLE_S = 30.0


def _empty_counters() -> dict[str, Any]:
    """Return a fresh counters dict with all fields initialised."""
    return {
        # Energy charged totals
        "energy_total_wh": 0.0,
        "energy_solar_wh": 0.0,
        "energy_import_wh": 0.0,
        # Solar production total (used for solar-to-EV ratio)
        "solar_production_total_wh": 0.0,
        # Auto-detected battery capacity (EMA over sessions)
        "battery_capacity_estimate_kwh": 0.0,
        # Charging overhead (wall vs battery-received)
        "overhead_wall_wh": 0.0,
        "overhead_battery_wh": 0.0,
        # Solar capture factor (rolling EMA, operational control)
        "solar_capture_factor": 0.0,
        # Migration flag
        "migrated": False,
    }


class AdaptiveChargeStore:
    """In-memory counters backed by a throttled JSON file in .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry_id}")
        self._data: dict[str, Any] = _empty_counters()
        self._dirty = False
        self._last_flush: float = 0.0
        self._flush_unsub: Any | None = None
        self._flush_task: Any | None = None  # In-flight flush guard

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load counters from disk (or initialise empty)."""
        stored = await self._store.async_load()
        if stored:
            # Merge with defaults so new keys are always present.
            merged = _empty_counters()
            merged.update(stored)
            self._data = merged
            _LOGGER.debug("AdaptiveCharge store loaded for %s", self._entry_id)
        else:
            self._data = _empty_counters()
            _LOGGER.debug("AdaptiveCharge store initialised (empty) for %s", self._entry_id)

    async def async_save(self) -> None:
        """Force-write counters to disk (called on shutdown / unload)."""
        await self._store.async_save(self._data)
        self._dirty = False
        self._last_flush = time.monotonic()
        _LOGGER.debug("AdaptiveCharge store flushed for %s", self._entry_id)

    def schedule_flush(self) -> None:
        """Mark data as dirty and schedule a throttled flush.

        Uses an in-flight guard to prevent parallel save scheduling: if a
        flush task is already running, we skip creating a new one.
        """
        self._dirty = True
        now = time.monotonic()
        if now - self._last_flush >= _FLUSH_THROTTLE_S:
            # Guard: skip if a flush task is already in-flight
            if self._flush_task is not None and not self._flush_task.done():
                return
            self._flush_task = self._hass.async_create_task(self.async_save(), eager_start=False)
            self._flush_task.add_done_callback(self._flush_done_callback)

    @staticmethod
    def _flush_done_callback(task) -> None:
        """Log any errors from the async flush task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning("AdaptiveCharge store flush failed: %s", exc)

    # ------------------------------------------------------------------
    # Counter updates
    # ------------------------------------------------------------------

    def add_energy_charged(self, total_wh: float, solar_wh: float, import_wh: float) -> None:
        """Add energy charged deltas to persistent totals."""
        if total_wh <= 0:
            return
        self._data["energy_total_wh"] += total_wh
        self._data["energy_solar_wh"] += solar_wh
        self._data["energy_import_wh"] += import_wh
        self.schedule_flush()

    def add_solar_production(self, wh: float) -> None:
        """Add solar production delta to persistent total and daily counter."""
        if wh < 0:
            return
        self._data["solar_production_total_wh"] += wh
        self.schedule_flush()

    def set_battery_capacity_estimate(self, kwh: float) -> None:
        """Persist an updated battery capacity estimate."""
        self._data["battery_capacity_estimate_kwh"] = round(kwh, 2)
        self.schedule_flush()

    def add_overhead(self, wall_wh: float, battery_wh: float) -> None:
        """Accumulate wall vs battery-received energy for overhead calculation."""
        if wall_wh <= 0 or battery_wh <= 0:
            return
        self._data["overhead_wall_wh"] += wall_wh
        self._data["overhead_battery_wh"] += battery_wh
        self.schedule_flush()

    def set_solar_capture_factor(self, factor: float) -> None:
        """Persist the rolling solar capture factor (0–1)."""
        self._data["solar_capture_factor"] = round(max(0.0, min(factor, 1.0)), 4)
        self.schedule_flush()

    # ------------------------------------------------------------------
    # Read accessors  (kWh for sensor consumption)
    # ------------------------------------------------------------------

    def get(self, key: str, default: float = 0.0) -> float:
        """Return a counter value."""
        return self._data.get(key, default)

    def get_kwh(self, key: str) -> float:
        """Return a counter in kWh (stored internally as Wh)."""
        return round(self._data.get(key, 0.0) / 1000.0, 3)

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    @property
    def migrated(self) -> bool:
        """Return True if initial migration from old entities has been done."""
        return bool(self._data.get("migrated", False))

    def seed_from_old_state(
        self,
        energy_total_wh: float = 0.0,
        energy_solar_wh: float = 0.0,
        energy_import_wh: float = 0.0,
    ) -> None:
        """Seed counters from old entity states (best-effort migration)."""
        self._data["energy_total_wh"] = energy_total_wh
        self._data["energy_solar_wh"] = energy_solar_wh
        self._data["energy_import_wh"] = energy_import_wh
        self._data["migrated"] = True
        _LOGGER.info(
            "AdaptiveCharge store: migrated counters from old entities "
            "(energy=%.1f Wh)",
            energy_total_wh,
        )
        self.schedule_flush()

    def mark_migrated(self) -> None:
        """Mark migration as done (even if nothing was seeded)."""
        self._data["migrated"] = True
        self.schedule_flush()
