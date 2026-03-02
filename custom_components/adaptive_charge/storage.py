"""Persistent storage for AdaptiveCharge counters.

Uses homeassistant.helpers.storage.Store to persist missed-solar and energy
counters to `.storage/adaptive_charge.counters` so that:
  - Data survives HA restart / reload / reboot.
  - Disabled entities can later be enabled and show correct values immediately.
  - Rollover (daily/monthly/yearly) is detected even if HA was down at midnight.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "adaptive_charge.counters"

# Minimum interval (seconds) between disk writes to avoid I/O spam.
_FLUSH_THROTTLE_S = 30.0


def _today_str() -> str:
    """Return today's date as ISO string."""
    return date.today().isoformat()


def _this_month_str() -> str:
    """Return first day of current month as ISO string."""
    today = date.today()
    return date(today.year, today.month, 1).isoformat()


def _this_year_str() -> str:
    """Return first day of current year as ISO string."""
    return date(date.today().year, 1, 1).isoformat()


def _empty_counters() -> dict[str, Any]:
    """Return a fresh counters dict with all fields initialised."""
    return {
        # Cumulative totals (never reset)
        "missed_solar_total_wh": 0.0,
        "missed_solar_absence_wh": 0.0,
        "missed_solar_cable_wh": 0.0,
        "missed_solar_low_surplus_wh": 0.0,
        "missed_solar_quantization_wh": 0.0,
        # Daily period
        "missed_solar_daily_wh": 0.0,
        "missed_solar_absence_daily_wh": 0.0,
        "missed_solar_cable_daily_wh": 0.0,
        "missed_solar_low_surplus_daily_wh": 0.0,
        "missed_solar_quantization_daily_wh": 0.0,
        "daily_period_start": _today_str(),
        # Monthly period
        "missed_solar_monthly_wh": 0.0,
        "missed_solar_absence_monthly_wh": 0.0,
        "missed_solar_cable_monthly_wh": 0.0,
        "missed_solar_low_surplus_monthly_wh": 0.0,
        "missed_solar_quantization_monthly_wh": 0.0,
        "monthly_period_start": _this_month_str(),
        # Yearly period
        "missed_solar_yearly_wh": 0.0,
        "missed_solar_absence_yearly_wh": 0.0,
        "missed_solar_cable_yearly_wh": 0.0,
        "missed_solar_low_surplus_yearly_wh": 0.0,
        "missed_solar_quantization_yearly_wh": 0.0,
        "yearly_period_start": _this_year_str(),
        # Energy charged totals
        "energy_total_wh": 0.0,
        "energy_solar_wh": 0.0,
        "energy_import_wh": 0.0,
        # Migration flag
        "migrated": False,
    }


# Suffixes used to construct per-cause keys.
_CAUSE_SUFFIXES = ("absence", "cable", "low_surplus", "quantization")

# Period names used for rollover.
_PERIODS = ("daily", "monthly", "yearly")


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
        # Check for rollovers that happened while HA was offline.
        self._check_rollovers()

    async def async_save(self) -> None:
        """Force-write counters to disk (called on shutdown / unload)."""
        await self._store.async_save(self._data)
        self._dirty = False
        self._last_flush = time.monotonic()
        _LOGGER.debug("AdaptiveCharge store flushed for %s", self._entry_id)

    def schedule_flush(self) -> None:
        """Mark data as dirty and schedule a throttled flush."""
        self._dirty = True
        now = time.monotonic()
        if now - self._last_flush >= _FLUSH_THROTTLE_S:
            task = self._hass.async_create_task(self.async_save(), eager_start=False)
            task.add_done_callback(self._flush_done_callback)

    @staticmethod
    def _flush_done_callback(task) -> None:
        """Log any errors from the async flush task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning("AdaptiveCharge store flush failed: %s", exc)

    # ------------------------------------------------------------------
    # Rollover logic
    # ------------------------------------------------------------------

    def _check_rollovers(self) -> None:
        """Detect and apply any period rollovers (day/month/year)."""
        today = _today_str()
        month = _this_month_str()
        year = _this_year_str()

        if self._data.get("daily_period_start") != today:
            _LOGGER.debug("AdaptiveCharge store: daily rollover detected")
            self._reset_period("daily")
            self._data["daily_period_start"] = today

        if self._data.get("monthly_period_start") != month:
            _LOGGER.debug("AdaptiveCharge store: monthly rollover detected")
            self._reset_period("monthly")
            self._data["monthly_period_start"] = month

        if self._data.get("yearly_period_start") != year:
            _LOGGER.debug("AdaptiveCharge store: yearly rollover detected")
            self._reset_period("yearly")
            self._data["yearly_period_start"] = year

    def _reset_period(self, period: str) -> None:
        """Reset all counters for the given period to 0."""
        self._data[f"missed_solar_{period}_wh"] = 0.0
        for cause in _CAUSE_SUFFIXES:
            self._data[f"missed_solar_{cause}_{period}_wh"] = 0.0
        self._dirty = True

    def check_rollovers(self) -> None:
        """Public rollover check — call periodically (e.g. every tick)."""
        self._check_rollovers()

    # ------------------------------------------------------------------
    # Counter updates
    # ------------------------------------------------------------------

    def add_missed_solar(
        self,
        total_wh: float,
        cause: str | None = None,
    ) -> None:
        """Add *total_wh* to total + all periods, optionally to a cause bucket."""
        if total_wh <= 0:
            return
        self._data["missed_solar_total_wh"] += total_wh
        for period in _PERIODS:
            self._data[f"missed_solar_{period}_wh"] += total_wh
        if cause and cause in _CAUSE_SUFFIXES:
            self._data[f"missed_solar_{cause}_wh"] += total_wh
            for period in _PERIODS:
                self._data[f"missed_solar_{cause}_{period}_wh"] += total_wh
        self.schedule_flush()

    def add_energy_charged(self, total_wh: float, solar_wh: float, import_wh: float) -> None:
        """Add energy charged deltas to persistent totals."""
        if total_wh <= 0:
            return
        self._data["energy_total_wh"] += total_wh
        self._data["energy_solar_wh"] += solar_wh
        self._data["energy_import_wh"] += import_wh
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
        total_wh: float,
        absence_wh: float,
        cable_wh: float,
        low_surplus_wh: float,
        quantization_wh: float,
        energy_total_wh: float = 0.0,
        energy_solar_wh: float = 0.0,
        energy_import_wh: float = 0.0,
    ) -> None:
        """Seed counters from old entity states (best-effort migration)."""
        self._data["missed_solar_total_wh"] = total_wh
        self._data["missed_solar_absence_wh"] = absence_wh
        self._data["missed_solar_cable_wh"] = cable_wh
        self._data["missed_solar_low_surplus_wh"] = low_surplus_wh
        self._data["missed_solar_quantization_wh"] = quantization_wh
        self._data["energy_total_wh"] = energy_total_wh
        self._data["energy_solar_wh"] = energy_solar_wh
        self._data["energy_import_wh"] = energy_import_wh
        self._data["migrated"] = True
        _LOGGER.info(
            "AdaptiveCharge store: migrated counters from old entities "
            "(total=%.1f Wh, energy=%.1f Wh)",
            total_wh,
            energy_total_wh,
        )
        self.schedule_flush()

    def mark_migrated(self) -> None:
        """Mark migration as done (even if nothing was seeded)."""
        self._data["migrated"] = True
        self.schedule_flush()
