"""Unit tests for AdaptiveCharge persistent storage."""
from __future__ import annotations

from datetime import date

import pytest


# ---------------------------------------------------------------------------
# Mirror of storage logic for pure-unit testing without HA imports
# ---------------------------------------------------------------------------

def _today_str():
    return date.today().isoformat()


def _this_month_str():
    today = date.today()
    return date(today.year, today.month, 1).isoformat()


def _this_year_str():
    return date(date.today().year, 1, 1).isoformat()


_CAUSE_SUFFIXES = ("absence", "cable", "low_surplus", "quantization")
_PERIODS = ("daily", "monthly", "yearly")


def _empty_counters():
    return {
        "missed_solar_total_wh": 0.0,
        "missed_solar_absence_wh": 0.0,
        "missed_solar_cable_wh": 0.0,
        "missed_solar_low_surplus_wh": 0.0,
        "missed_solar_quantization_wh": 0.0,
        "missed_solar_daily_wh": 0.0,
        "missed_solar_absence_daily_wh": 0.0,
        "missed_solar_cable_daily_wh": 0.0,
        "missed_solar_low_surplus_daily_wh": 0.0,
        "missed_solar_quantization_daily_wh": 0.0,
        "daily_period_start": _today_str(),
        "missed_solar_monthly_wh": 0.0,
        "missed_solar_absence_monthly_wh": 0.0,
        "missed_solar_cable_monthly_wh": 0.0,
        "missed_solar_low_surplus_monthly_wh": 0.0,
        "missed_solar_quantization_monthly_wh": 0.0,
        "monthly_period_start": _this_month_str(),
        "missed_solar_yearly_wh": 0.0,
        "missed_solar_absence_yearly_wh": 0.0,
        "missed_solar_cable_yearly_wh": 0.0,
        "missed_solar_low_surplus_yearly_wh": 0.0,
        "missed_solar_quantization_yearly_wh": 0.0,
        "yearly_period_start": _this_year_str(),
        "energy_total_wh": 0.0,
        "energy_solar_wh": 0.0,
        "energy_import_wh": 0.0,
        "migrated": False,
    }


def _add_missed_solar(data, total_wh, cause=None):
    """Mirror of AdaptiveChargeStore.add_missed_solar."""
    if total_wh <= 0:
        return
    data["missed_solar_total_wh"] += total_wh
    for period in _PERIODS:
        data[f"missed_solar_{period}_wh"] += total_wh
    if cause and cause in _CAUSE_SUFFIXES:
        data[f"missed_solar_{cause}_wh"] += total_wh
        for period in _PERIODS:
            data[f"missed_solar_{cause}_{period}_wh"] += total_wh


def _reset_period(data, period):
    """Mirror of AdaptiveChargeStore._reset_period."""
    data[f"missed_solar_{period}_wh"] = 0.0
    for cause in _CAUSE_SUFFIXES:
        data[f"missed_solar_{cause}_{period}_wh"] = 0.0


def _check_rollovers(data, today=None, month=None, year=None):
    """Mirror of rollover logic."""
    today = today or _today_str()
    month = month or _this_month_str()
    year = year or _this_year_str()

    if data.get("daily_period_start") != today:
        _reset_period(data, "daily")
        data["daily_period_start"] = today

    if data.get("monthly_period_start") != month:
        _reset_period(data, "monthly")
        data["monthly_period_start"] = month

    if data.get("yearly_period_start") != year:
        _reset_period(data, "yearly")
        data["yearly_period_start"] = year


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyCounters:
    """Test empty counters initialization."""

    def test_empty_counters_has_all_keys(self):
        data = _empty_counters()
        assert "missed_solar_total_wh" in data
        assert "missed_solar_daily_wh" in data
        assert "missed_solar_monthly_wh" in data
        assert "missed_solar_yearly_wh" in data
        assert "missed_solar_absence_wh" in data
        assert "daily_period_start" in data
        assert "monthly_period_start" in data
        assert "yearly_period_start" in data
        assert data["migrated"] is False

    def test_all_counters_start_at_zero(self):
        data = _empty_counters()
        for key, value in data.items():
            if key.endswith("_wh"):
                assert value == 0.0, f"{key} should be 0.0"


class TestAddMissedSolar:
    """Test add_missed_solar logic."""

    def test_adds_to_total_and_periods(self):
        data = _empty_counters()
        _add_missed_solar(data, 100.0)
        assert data["missed_solar_total_wh"] == 100.0
        assert data["missed_solar_daily_wh"] == 100.0
        assert data["missed_solar_monthly_wh"] == 100.0
        assert data["missed_solar_yearly_wh"] == 100.0

    def test_adds_to_cause_buckets(self):
        data = _empty_counters()
        _add_missed_solar(data, 50.0, "absence")
        assert data["missed_solar_total_wh"] == 50.0
        assert data["missed_solar_absence_wh"] == 50.0
        assert data["missed_solar_absence_daily_wh"] == 50.0
        assert data["missed_solar_cable_wh"] == 0.0

    def test_accumulates_correctly(self):
        data = _empty_counters()
        _add_missed_solar(data, 100.0, "cable")
        _add_missed_solar(data, 50.0, "absence")
        assert data["missed_solar_total_wh"] == 150.0
        assert data["missed_solar_cable_wh"] == 100.0
        assert data["missed_solar_absence_wh"] == 50.0
        assert data["missed_solar_daily_wh"] == 150.0

    def test_zero_or_negative_ignored(self):
        data = _empty_counters()
        _add_missed_solar(data, 0.0)
        _add_missed_solar(data, -10.0)
        assert data["missed_solar_total_wh"] == 0.0

    def test_unknown_cause_only_adds_total(self):
        data = _empty_counters()
        _add_missed_solar(data, 100.0, "unknown_cause")
        assert data["missed_solar_total_wh"] == 100.0
        assert data["missed_solar_absence_wh"] == 0.0
        assert data["missed_solar_cable_wh"] == 0.0


class TestRollover:
    """Test period rollover logic."""

    def test_daily_rollover_resets_daily_counters(self):
        data = _empty_counters()
        _add_missed_solar(data, 500.0, "absence")
        assert data["missed_solar_daily_wh"] == 500.0

        # Simulate new day
        data["daily_period_start"] = "2025-01-01"
        _check_rollovers(data, today="2025-01-02")
        assert data["missed_solar_daily_wh"] == 0.0
        assert data["missed_solar_absence_daily_wh"] == 0.0
        assert data["daily_period_start"] == "2025-01-02"
        # Total is NOT reset
        assert data["missed_solar_total_wh"] == 500.0
        # Monthly/yearly NOT reset
        assert data["missed_solar_monthly_wh"] == 500.0

    def test_monthly_rollover_resets_monthly_counters(self):
        data = _empty_counters()
        _add_missed_solar(data, 1000.0, "cable")
        data["monthly_period_start"] = "2025-01-01"
        _check_rollovers(data, month="2025-02-01")
        assert data["missed_solar_monthly_wh"] == 0.0
        assert data["missed_solar_cable_monthly_wh"] == 0.0
        assert data["missed_solar_total_wh"] == 1000.0
        assert data["missed_solar_yearly_wh"] == 1000.0

    def test_yearly_rollover_resets_yearly_counters(self):
        data = _empty_counters()
        _add_missed_solar(data, 2000.0, "low_surplus")
        data["yearly_period_start"] = "2024-01-01"
        _check_rollovers(data, year="2025-01-01")
        assert data["missed_solar_yearly_wh"] == 0.0
        assert data["missed_solar_low_surplus_yearly_wh"] == 0.0
        assert data["missed_solar_total_wh"] == 2000.0

    def test_no_rollover_when_same_period(self):
        data = _empty_counters()
        _add_missed_solar(data, 100.0)
        today = _today_str()
        data["daily_period_start"] = today
        _check_rollovers(data, today=today)
        assert data["missed_solar_daily_wh"] == 100.0

    def test_multiple_period_rollovers_at_once(self):
        """Simulate HA being down across a year boundary."""
        data = _empty_counters()
        _add_missed_solar(data, 5000.0, "quantization")
        data["daily_period_start"] = "2024-12-31"
        data["monthly_period_start"] = "2024-12-01"
        data["yearly_period_start"] = "2024-01-01"
        _check_rollovers(data, today="2025-01-02", month="2025-01-01", year="2025-01-01")
        assert data["missed_solar_daily_wh"] == 0.0
        assert data["missed_solar_monthly_wh"] == 0.0
        assert data["missed_solar_yearly_wh"] == 0.0
        assert data["missed_solar_total_wh"] == 5000.0


class TestMigration:
    """Test migration seeding logic."""

    def test_seed_sets_values_and_marks_migrated(self):
        data = _empty_counters()
        data["missed_solar_total_wh"] = 1234.0
        data["missed_solar_absence_wh"] = 100.0
        data["missed_solar_cable_wh"] = 200.0
        data["missed_solar_low_surplus_wh"] = 300.0
        data["missed_solar_quantization_wh"] = 400.0
        data["migrated"] = True
        assert data["missed_solar_total_wh"] == 1234.0
        assert data["migrated"] is True

    def test_new_keys_merged_on_load(self):
        """Simulate loading old stored data missing new keys."""
        old_data = {"missed_solar_total_wh": 999.0}
        merged = _empty_counters()
        merged.update(old_data)
        assert merged["missed_solar_total_wh"] == 999.0
        assert "missed_solar_daily_wh" in merged
        assert merged["missed_solar_daily_wh"] == 0.0


class TestKwhConversion:
    """Test Wh to kWh conversion."""

    def test_kwh_conversion(self):
        data = _empty_counters()
        data["missed_solar_total_wh"] = 1500.0
        kwh = round(data["missed_solar_total_wh"] / 1000.0, 3)
        assert kwh == 1.5

    def test_kwh_zero(self):
        data = _empty_counters()
        kwh = round(data["missed_solar_total_wh"] / 1000.0, 3)
        assert kwh == 0.0
