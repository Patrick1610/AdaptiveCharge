"""Unit tests for AdaptiveCharge persistent storage."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Mirror of storage logic for pure-unit testing without HA imports
# ---------------------------------------------------------------------------

def _empty_counters():
    return {
        "energy_total_wh": 0.0,
        "energy_solar_wh": 0.0,
        "energy_import_wh": 0.0,
        "solar_production_total_wh": 0.0,
        "battery_capacity_estimate_kwh": 0.0,
        "overhead_wall_wh": 0.0,
        "overhead_battery_wh": 0.0,
        "migrated": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyCounters:
    """Test empty counters initialization."""

    def test_empty_counters_has_all_keys(self):
        data = _empty_counters()
        assert "energy_total_wh" in data
        assert "energy_solar_wh" in data
        assert "energy_import_wh" in data
        assert "solar_production_total_wh" in data
        assert "battery_capacity_estimate_kwh" in data
        assert "overhead_wall_wh" in data
        assert "overhead_battery_wh" in data
        assert data["migrated"] is False

    def test_all_counters_start_at_zero(self):
        data = _empty_counters()
        for key, value in data.items():
            if key.endswith("_wh") or key.endswith("_kwh"):
                assert value == 0.0, f"{key} should be 0.0"


class TestMigration:
    """Test migration seeding logic."""

    def test_seed_marks_migrated(self):
        data = _empty_counters()
        data["migrated"] = True
        assert data["migrated"] is True

    def test_new_keys_merged_on_load(self):
        """Simulate loading old stored data missing new keys."""
        old_data = {"energy_total_wh": 999.0}
        merged = _empty_counters()
        merged.update(old_data)
        assert merged["energy_total_wh"] == 999.0
        assert "energy_solar_wh" in merged
        assert merged["energy_solar_wh"] == 0.0


class TestKwhConversion:
    """Test Wh to kWh conversion."""

    def test_kwh_conversion(self):
        data = _empty_counters()
        data["energy_total_wh"] = 1500.0
        kwh = round(data["energy_total_wh"] / 1000.0, 3)
        assert kwh == 1.5

    def test_kwh_zero(self):
        data = _empty_counters()
        kwh = round(data["energy_total_wh"] / 1000.0, 3)
        assert kwh == 0.0


# ---------------------------------------------------------------------------
# Tests: solar production and battery capacity estimate storage helpers
# ---------------------------------------------------------------------------

def _add_solar_production(data: dict, wh: float) -> None:
    """Mirror of AdaptiveChargeStore.add_solar_production."""
    if wh < 0:
        return
    data["solar_production_total_wh"] += wh


def _set_battery_capacity_estimate(data: dict, kwh: float) -> None:
    """Mirror of AdaptiveChargeStore.set_battery_capacity_estimate."""
    data["battery_capacity_estimate_kwh"] = round(kwh, 2)


class TestSolarProductionAccumulation:
    """Tests for the solar production counter."""

    def test_positive_production_accumulates(self):
        data = _empty_counters()
        data["solar_production_total_wh"] = 0.0
        _add_solar_production(data, 500.0)
        _add_solar_production(data, 250.0)
        assert data["solar_production_total_wh"] == 750.0

    def test_zero_production_is_no_op(self):
        """Zero Wh (e.g. night-time tick) should be added without error."""
        data = _empty_counters()
        data["solar_production_total_wh"] = 100.0
        _add_solar_production(data, 0.0)
        assert data["solar_production_total_wh"] == 100.0

    def test_negative_production_rejected(self):
        """Negative values (sensor glitch) must not corrupt the total."""
        data = _empty_counters()
        data["solar_production_total_wh"] = 200.0
        _add_solar_production(data, -50.0)
        assert data["solar_production_total_wh"] == 200.0

    def test_new_key_present_in_empty_counters(self):
        """solar_production_total_wh must be initialised to 0.0."""
        data = _empty_counters()
        assert "solar_production_total_wh" in data
        assert data["solar_production_total_wh"] == 0.0

    def test_new_key_merged_on_old_store_load(self):
        """Old stores without solar_production_total_wh get the key via merge."""
        old_data = {"energy_total_wh": 999.0}
        merged = _empty_counters()
        merged.update(old_data)
        assert "solar_production_total_wh" in merged
        assert merged["solar_production_total_wh"] == 0.0


class TestBatteryCapacityEstimateStorage:
    """Tests for the battery capacity estimate persistence helper."""

    def test_set_and_retrieve(self):
        data = _empty_counters()
        data["battery_capacity_estimate_kwh"] = 0.0
        _set_battery_capacity_estimate(data, 82.75)
        assert data["battery_capacity_estimate_kwh"] == 82.75

    def test_value_rounded_to_2dp(self):
        data = _empty_counters()
        data["battery_capacity_estimate_kwh"] = 0.0
        _set_battery_capacity_estimate(data, 77.123456)
        assert data["battery_capacity_estimate_kwh"] == 77.12

    def test_overwrite_existing_estimate(self):
        data = _empty_counters()
        data["battery_capacity_estimate_kwh"] = 50.0
        _set_battery_capacity_estimate(data, 95.5)
        assert data["battery_capacity_estimate_kwh"] == 95.5

    def test_new_key_present_in_empty_counters(self):
        """battery_capacity_estimate_kwh must be initialised to 0.0."""
        data = _empty_counters()
        assert "battery_capacity_estimate_kwh" in data
        assert data["battery_capacity_estimate_kwh"] == 0.0

    def test_new_key_merged_on_old_store_load(self):
        """Old stores without the key get it via merge with defaults."""
        old_data = {"energy_total_wh": 5000.0}
        merged = _empty_counters()
        merged.update(old_data)
        assert "battery_capacity_estimate_kwh" in merged
        assert merged["battery_capacity_estimate_kwh"] == 0.0


# ---------------------------------------------------------------------------
# Helper: mirror of storage.add_overhead
# ---------------------------------------------------------------------------

def _add_overhead(data: dict, wall_wh: float, battery_wh: float) -> None:
    """Mirror of AdaptiveChargeStore.add_overhead."""
    if wall_wh <= 0 or battery_wh <= 0:
        return
    data["overhead_wall_wh"] += wall_wh
    data["overhead_battery_wh"] += battery_wh


# ---------------------------------------------------------------------------
# Tests: Overhead storage
# ---------------------------------------------------------------------------

class TestOverheadStorage:
    """Tests for the overhead storage methods."""

    def test_add_overhead_accumulates(self):
        """Overhead deltas accumulate correctly."""
        data = _empty_counters()
        _add_overhead(data, 10000.0, 9000.0)
        assert data["overhead_wall_wh"] == 10000.0
        assert data["overhead_battery_wh"] == 9000.0
        _add_overhead(data, 20000.0, 17000.0)
        assert data["overhead_wall_wh"] == 30000.0
        assert data["overhead_battery_wh"] == 26000.0

    def test_add_overhead_rejects_zero_wall(self):
        """Zero wall energy is rejected."""
        data = _empty_counters()
        _add_overhead(data, 0.0, 5000.0)
        assert data["overhead_wall_wh"] == 0.0
        assert data["overhead_battery_wh"] == 0.0

    def test_add_overhead_rejects_negative_wall(self):
        """Negative wall energy is rejected."""
        data = _empty_counters()
        _add_overhead(data, -100.0, 5000.0)
        assert data["overhead_wall_wh"] == 0.0

    def test_add_overhead_rejects_zero_battery(self):
        """Zero battery energy is rejected."""
        data = _empty_counters()
        _add_overhead(data, 5000.0, 0.0)
        assert data["overhead_wall_wh"] == 0.0
        assert data["overhead_battery_wh"] == 0.0

    def test_overhead_keys_merged_from_old_store(self):
        """Old stores without overhead keys get them via merge with defaults."""
        old_data = {"energy_total_wh": 5000.0}
        merged = _empty_counters()
        merged.update(old_data)
        assert "overhead_wall_wh" in merged
        assert "overhead_battery_wh" in merged
        assert merged["overhead_wall_wh"] == 0.0
        assert merged["overhead_battery_wh"] == 0.0
