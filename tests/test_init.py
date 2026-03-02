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
