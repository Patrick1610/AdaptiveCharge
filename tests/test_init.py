"""Unit tests for AdaptiveCharge __init__ domain data handling."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Mirror _get_coordinator logic without importing HA
# ---------------------------------------------------------------------------

class _FakeCoordinator:
    """Minimal stand-in for AdaptiveChargeCoordinator."""
    pass


def _get_coordinator_mirror(domain_data: dict, entry_id: str | None = None):
    """Mirror of __init__._get_coordinator that skips non-coordinator values."""
    if entry_id:
        return domain_data.get(entry_id)
    for value in domain_data.values():
        if isinstance(value, _FakeCoordinator):
            return value
    return None


def _has_coordinator_entries(domain_data: dict) -> bool:
    """Mirror of the unload cleanup check for remaining coordinator entries."""
    return any(isinstance(v, _FakeCoordinator) for v in domain_data.values())


# ---------------------------------------------------------------------------
# Tests: _get_coordinator should never return the version string
# ---------------------------------------------------------------------------

class TestGetCoordinator:
    def test_returns_coordinator_not_version_string(self):
        coord = _FakeCoordinator()
        domain_data = {"version": "3.1.6", "entry_abc": coord}
        assert _get_coordinator_mirror(domain_data) is coord

    def test_returns_none_when_only_version_key(self):
        domain_data = {"version": "3.1.6"}
        assert _get_coordinator_mirror(domain_data) is None

    def test_returns_none_on_empty_dict(self):
        assert _get_coordinator_mirror({}) is None

    def test_returns_specific_entry_by_id(self):
        coord_a = _FakeCoordinator()
        coord_b = _FakeCoordinator()
        domain_data = {"version": "3.1.6", "entry_a": coord_a, "entry_b": coord_b}
        assert _get_coordinator_mirror(domain_data, entry_id="entry_b") is coord_b

    def test_returns_none_for_missing_entry_id(self):
        domain_data = {"version": "3.1.6"}
        assert _get_coordinator_mirror(domain_data, entry_id="nonexistent") is None

    def test_version_string_is_not_returned_even_if_first(self):
        """Version key is inserted first; must not be returned as coordinator."""
        coord = _FakeCoordinator()
        domain_data = {}
        domain_data["version"] = "3.1.6"
        domain_data["entry_x"] = coord
        assert _get_coordinator_mirror(domain_data) is coord


# ---------------------------------------------------------------------------
# Tests: unload cleanup should detect no-more-entries despite "version" key
# ---------------------------------------------------------------------------

class TestUnloadCleanup:
    def test_no_entries_when_only_version_remains(self):
        domain_data = {"version": "3.1.6"}
        assert _has_coordinator_entries(domain_data) is False

    def test_has_entries_when_coordinator_present(self):
        domain_data = {"version": "3.1.6", "entry_abc": _FakeCoordinator()}
        assert _has_coordinator_entries(domain_data) is True

    def test_no_entries_on_empty_dict(self):
        assert _has_coordinator_entries({}) is False

    def test_cleanup_after_popping_last_entry(self):
        """Simulate removing the last coordinator entry; version key remains."""
        coord = _FakeCoordinator()
        domain_data = {"version": "3.1.6", "entry_abc": coord}
        domain_data.pop("entry_abc")
        assert _has_coordinator_entries(domain_data) is False

    def test_still_has_entries_after_popping_one_of_two(self):
        coord_a = _FakeCoordinator()
        coord_b = _FakeCoordinator()
        domain_data = {"version": "3.1.6", "entry_a": coord_a, "entry_b": coord_b}
        domain_data.pop("entry_a")
        assert _has_coordinator_entries(domain_data) is True


# ---------------------------------------------------------------------------
# Mirror utility meter dedup logic — checks both data AND options
# ---------------------------------------------------------------------------

class _FakeConfigEntry:
    """Minimal stand-in for a HA ConfigEntry."""

    def __init__(self, entry_id: str, data: dict, options: dict | None = None):
        self.entry_id = entry_id
        self.data = data
        self.options = options or {}


def _utility_meter_already_exists(
    existing_entries: list[_FakeConfigEntry],
    source_entity_id: str,
    cycle: str,
) -> bool:
    """Mirror of the dedup scan in _async_setup_utility_meters.

    Checks both entry.data and entry.options because HA's
    SchemaConfigFlowHandler stores config in options, not data.
    """
    for entry in existing_entries:
        cfg = {**entry.data, **entry.options}
        if cfg.get("source") == source_entity_id and cfg.get("cycle") == cycle:
            return True
    return False


# ---------------------------------------------------------------------------
# Tests: utility meter dedup must detect entries with config in options
# ---------------------------------------------------------------------------

class TestUtilityMeterDedup:
    """Verify that the dedup logic finds utility meters regardless of whether
    their configuration lives in entry.data or entry.options."""

    def test_detects_match_in_data(self):
        """Classic case: config stored in data dict."""
        entries = [
            _FakeConfigEntry("um1", data={"source": "sensor.energy", "cycle": "daily"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is True

    def test_detects_match_in_options(self):
        """SchemaConfigFlowHandler case: data={}, config in options."""
        entries = [
            _FakeConfigEntry("um1", data={}, options={"source": "sensor.energy", "cycle": "daily"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is True

    def test_no_match_when_empty(self):
        assert _utility_meter_already_exists([], "sensor.energy", "daily") is False

    def test_no_match_different_source(self):
        entries = [
            _FakeConfigEntry("um1", data={}, options={"source": "sensor.other", "cycle": "daily"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is False

    def test_no_match_different_cycle(self):
        entries = [
            _FakeConfigEntry("um1", data={}, options={"source": "sensor.energy", "cycle": "monthly"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is False

    def test_options_overrides_data(self):
        """When both data and options have source, options wins (dict merge)."""
        entries = [
            _FakeConfigEntry(
                "um1",
                data={"source": "sensor.old", "cycle": "daily"},
                options={"source": "sensor.energy", "cycle": "daily"},
            ),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is True
        assert _utility_meter_already_exists(entries, "sensor.old", "daily") is False

    def test_multiple_entries_finds_correct_one(self):
        entries = [
            _FakeConfigEntry("um1", data={}, options={"source": "sensor.energy", "cycle": "daily"}),
            _FakeConfigEntry("um2", data={}, options={"source": "sensor.energy", "cycle": "monthly"}),
            _FakeConfigEntry("um3", data={}, options={"source": "sensor.energy", "cycle": "yearly"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "daily") is True
        assert _utility_meter_already_exists(entries, "sensor.energy", "monthly") is True
        assert _utility_meter_already_exists(entries, "sensor.energy", "yearly") is True
        assert _utility_meter_already_exists(entries, "sensor.energy", "weekly") is False

    def test_data_only_no_options_still_works(self):
        """Backward compat: if a utility meter has config in data, still detected."""
        entries = [
            _FakeConfigEntry("um1", data={"source": "sensor.energy", "cycle": "yearly"}),
        ]
        assert _utility_meter_already_exists(entries, "sensor.energy", "yearly") is True

    def test_tracked_entry_match_in_options(self):
        """Mirror the tracked-entry check (lines 214-225 in __init__.py)."""
        # Simulate a tracked entry whose config lives in options
        um_entry = _FakeConfigEntry(
            "um_tracked", data={}, options={"source": "sensor.energy", "cycle": "daily"}
        )
        um_cfg = {**um_entry.data, **um_entry.options}
        assert um_cfg.get("cycle") == "daily"
        assert um_cfg.get("source") == "sensor.energy"

    def test_tracked_entry_empty_data_old_bug(self):
        """Before the fix, checking only entry.data would miss the match."""
        um_entry = _FakeConfigEntry(
            "um_tracked", data={}, options={"source": "sensor.energy", "cycle": "daily"}
        )
        # Old buggy check (data only):
        assert um_entry.data.get("cycle") is None  # would have been None!
        # Fixed check (merged):
        um_cfg = {**um_entry.data, **um_entry.options}
        assert um_cfg.get("cycle") == "daily"
