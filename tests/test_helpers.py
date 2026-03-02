"""Unit tests for AdaptiveCharge helpers — version and DeviceInfo handling.

HA classes (HomeAssistant, ConfigEntry, DeviceInfo) are unavailable in the test
environment, so we mirror the pure-Python logic from helpers.py here.  This
matches the pattern used throughout the test suite (e.g. test_init.py).
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Mirror get_version logic without importing HA
# ---------------------------------------------------------------------------

def _get_version_mirror(domain_data: dict | None) -> str | None:
    """Mirror of helpers.get_version.

    Returns the real version string, or None when the version is missing,
    None, or the placeholder string "unknown".  "unknown" must not be
    returned because it is not a parseable AwesomeVersion format and causes
    HA's device registry comparison to raise AwesomeVersionCompareException.
    """
    data = domain_data or {}
    version = data.get("version")
    return version if version and version != "unknown" else None


def _device_info_has_sw_version(version: str | None) -> bool:
    """Mirror of helpers.device_info sw_version decision.

    Returns True when sw_version would be included in the DeviceInfo dict,
    False when it would be omitted (i.e. version is None or empty).
    """
    extra: dict = {"sw_version": version} if version else {}
    return "sw_version" in extra


# ---------------------------------------------------------------------------
# Tests: get_version returns a real version or None (never "unknown")
# ---------------------------------------------------------------------------

class TestGetVersion:
    def test_returns_real_version(self):
        domain_data = {"version": "3.1.5"}
        assert _get_version_mirror(domain_data) == "3.1.5"

    def test_returns_none_for_unknown(self):
        """'unknown' must not be returned — it crashes awesomeversion."""
        domain_data = {"version": "unknown"}
        assert _get_version_mirror(domain_data) is None

    def test_returns_none_when_key_missing(self):
        domain_data = {}
        assert _get_version_mirror(domain_data) is None

    def test_returns_none_when_value_is_none(self):
        domain_data = {"version": None}
        assert _get_version_mirror(domain_data) is None

    def test_returns_none_for_empty_domain_data(self):
        assert _get_version_mirror(None) is None

    def test_returns_none_for_coordinator_only_data(self):
        """domain_data has coordinator entry but no version key."""
        domain_data = {"entry_abc": object()}
        assert _get_version_mirror(domain_data) is None

    def test_semver_string_passes_through(self):
        for v in ("3.0.0", "2.1.1", "1.0.0"):
            assert _get_version_mirror({"version": v}) == v


# ---------------------------------------------------------------------------
# Tests: device_info must omit sw_version when version is None/"unknown"
# ---------------------------------------------------------------------------

class TestDeviceInfoSwVersion:
    def test_sw_version_included_when_real(self):
        """A valid version string must be included in DeviceInfo."""
        assert _device_info_has_sw_version("3.1.5") is True

    def test_sw_version_excluded_when_none(self):
        """None must NOT be passed to DeviceInfo — awesomeversion can't compare it."""
        assert _device_info_has_sw_version(None) is False

    def test_sw_version_excluded_when_empty_string(self):
        assert _device_info_has_sw_version("") is False

    def test_no_sw_version_when_get_version_returns_none(self):
        """Full round-trip: version='unknown' in domain_data → no sw_version."""
        version = _get_version_mirror({"version": "unknown"})
        assert _device_info_has_sw_version(version) is False

    def test_sw_version_set_when_valid_version_in_domain_data(self):
        """Full round-trip: valid version → sw_version included."""
        version = _get_version_mirror({"version": "3.1.5"})
        assert _device_info_has_sw_version(version) is True
