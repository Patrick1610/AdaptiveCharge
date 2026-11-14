"""Exercise the actual preview core, not mirror copies of implementation logic."""

from dataclasses import replace

import pytest

from custom_components.adaptive_charge_preview.core.models import Measurement, Tariff, VehicleConfig, VehicleSnapshot, demand, finite, timestamp
from custom_components.adaptive_charge_preview.core.planner import plan
from custom_components.adaptive_charge_preview.core.session import Session

NOW = 1794484800.0


def snapshot(soc=50.0, energy=35.0, range_km=200.0, limit=80.0, now=NOW, **kwargs):
    def m(v, unit):
        return Measurement(v, unit=unit, measured_at=now, received_at=now)
    return VehicleSnapshot(m(soc,"%"), m(energy,"kWh"), m(range_km,"km"), m(limit,"%"), m(100.0,"%"), connected=True, home=True, connection_id="connection-a", connection_measured_at=now, **kwargs)


def prices(count=12, buys=None, solar=0):
    return [Tariff(NOW + i * 900, NOW + (i + 1) * 900, (buys or [0.2] * count)[i], 0.05, solar) for i in range(count)]


@pytest.mark.parametrize("value", [True, False, "nan", float("inf"), None, "unknown", "unavailable"])
def test_invalid_measurement(value):
    assert finite(value) is None


def test_source_time_is_not_reception_time():
    assert not Measurement(50, received_at=NOW).fresh(NOW)
    assert timestamp("2026-11-12T14:00:00") is None
    assert timestamp("2026-11-12T14:00:00+01:00") is not None


def test_tesla_and_id3_goals_use_different_sources():
    t = demand(VehicleConfig("t", "Tesla", "tesla"), snapshot(), 300, NOW)
    vw = demand(VehicleConfig("v", "VW", "id3"), snapshot(range_km=400), 300, NOW)
    assert t.effective_soc == vw.effective_soc == 75
    assert t.needed_kwh == pytest.approx(20.125)
    assert not t.confirmed


def test_limit_cap_and_optional_writer():
    capped = demand(VehicleConfig("v", "VW", "id3"), snapshot(range_km=400), 364, NOW)
    writable = demand(VehicleConfig("t", "Tesla", "tesla", limit_writer="number.limit"), snapshot(), 364, NOW)
    assert capped.effective_soc == 80 and capped.limited and capped.suggested_limit is None
    assert writable.requested_soc == 91 and writable.effective_soc == 80 and writable.suggested_limit == 92


def test_stale_soc_never_confirms_goal():
    s = snapshot(soc=80)
    s = replace(s, soc=replace(s.soc, measured_at=NOW - 4000))
    result = demand(VehicleConfig("t", "Tesla", "tesla"), s, 100, NOW)
    assert not result.confirmed and not result.source_fresh


def test_cheapest_quarters_and_green_between():
    result = plan(NOW, NOW + 10800, 5.5, 0.8, prices(buys=[0.9, 0.9, 0.1, 0.1] + [0.5] * 8))
    assert result.mode == "Green"
    assert len(result.windows) == 1
    assert result.windows[0].start == NOW + 1800
    assert result.windows[0].end == NOW + 3600
    assert result.cost == pytest.approx(0.55)


def test_green_solar_reduces_additional_grid_energy():
    result = plan(NOW, NOW + 10800, 10, 0.8, prices(solar=2))
    assert result.expected_solar_kwh == pytest.approx(5.5)
    assert sum(w.additional_kwh for w in result.windows) == pytest.approx(4.5)
    assert result.cost == pytest.approx(5.5 * 0.05 + 4.5 * 0.2)


def test_attach_short_fragment_to_cheapest_neighbor_before_block():
    p = [0.5, 0.2, 0.1, 0.3, 0.5, 0.05] + [0.5] * 6
    result = plan(NOW, NOW + 10800, 3.0, 0.8, prices(buys=p))
    assert len(result.windows) == 1
    assert result.windows[0].start == pytest.approx(NOW + 5 * 900 - (0.25 / 11 * 3600))
    assert result.windows[0].end == NOW + 6 * 900
    assert sum(w.additional_kwh for w in result.windows) == pytest.approx(3)


def test_small_total_remainder_is_tolerance_not_goal():
    result = plan(NOW, NOW + 10800, 0.4, 0.8, prices())
    assert result.reason == "small_remainder_within_tolerance"
    assert result.omitted_kwh == 0.4 and not result.windows


def test_significant_short_total_is_allowed_with_warning():
    result = plan(NOW, NOW + 10800, 0.4, 0.1, prices())
    assert result.windows and result.windows[0].kwh == pytest.approx(0.4)
    assert "short_block_required_for_significant_deficit" in result.warnings


def test_three_minutes_exactly_is_allowed():
    result = plan(NOW, NOW + 10800, 0.55, 0.8, prices())
    assert result.windows and not result.omitted_kwh


@pytest.mark.parametrize("rows", [[], [Tariff(NOW, NOW + 1800, 0.1), Tariff(NOW + 900, NOW + 2700, 0.2)]])
def test_price_gaps_or_conflicts_do_not_create_prices(rows):
    early = plan(NOW, NOW + 10800, 5, 0.8, rows)
    urgent = plan(NOW + 10000, NOW + 10800, 5, 0.8, rows)
    assert early.mode == "Green" and early.warnings
    assert urgent.mode == "Manual" and urgent.reason == "unpriced_deadline_fallback"
    assert len(early.windows)==1 and early.windows[0].end==NOW+10800
    assert early.cost is None and early.windows[0].cost is None


def test_direct_request_and_negative_prices():
    assert plan(NOW, None, 2, 0.8, []).mode == "Manual"
    result = plan(NOW, NOW + 10800, 2, 0.8, prices(buys=[0.5] * 4 + [-0.1] + [0.5] * 7))
    assert result.windows[0].start == NOW + 3600 and result.cost < 0


def test_meter_credits_survive_goal_changes_and_green():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    old = demand(c, s, 300, NOW)
    session = Session("t", 1, "connection-a", NOW, 300, NOW + 10000)
    assert session.update(NOW, s, old, 100, 1, meter_id="sensor.test") == pytest.approx(20.125)
    assert session.update(NOW + 30, s, old, 102, 1, meter_id="sensor.test") == pytest.approx(18.125)
    low = demand(c, s, 250, NOW)
    session.set_target(250, NOW + 10000, low, old)
    assert session.update(NOW + 60, s, low, 102, 1, meter_id="sensor.test") == pytest.approx(low.needed_kwh - 2)
    session.set_target(300, NOW + 10000, old, low)
    assert session.update(NOW + 90, s, old, 102, 1, meter_id="sensor.test") == pytest.approx(18.125)


def test_same_old_soc_does_not_regrant_exhausted_budget():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 210, NOW)
    session = Session("t", 1, "connection-a", NOW, 210, None)
    session.update(NOW, s, d, 100, 1, meter_id="sensor.test")
    assert session.update(NOW + 20, s, d, 103, 1, meter_id="sensor.test") == 0
    assert session.phase == "verifying" and session.remaining_kwh < 0
    assert session.update(NOW + 30, s, d, 103, 1, meter_id="sensor.test") == 0


def test_disconnect_and_new_connection_end_old_request():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 300, NOW)
    for changed in (replace(s, connected=False), replace(s, connection_id="connection-b")):
        session = Session("t", 1, "connection-a", NOW, 300, None)
        session.update(NOW, changed, d, 100, 1, meter_id="sensor.test")
        assert session.terminal


def test_old_disconnect_does_not_cancel_new_connection():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    session = Session("t", 1, "connection-a", NOW, 300, None)
    s = replace(s, connected=False, connection_measured_at=NOW - 900)
    session.update(NOW, s, demand(c, s, 300, NOW), 100, 1, meter_id="sensor.test")
    assert not session.terminal and session.phase == "waiting_data"


def test_droop_requires_five_minutes_with_fresh_sources():
    c = VehicleConfig("t", "Tesla", "tesla", droop_pp=2)
    s = snapshot(soc=75, range_km=300)
    session = Session("t", 1, "connection-a", NOW, 300, None)
    session.update(NOW, s, demand(c, s, 300, NOW), 100, 2, meter_id="sensor.test")
    for seconds in (60, 359, 360):
        s = snapshot(soc=73, range_km=292, now=NOW + seconds)
        result = session.update(NOW + seconds, s, demand(c, s, 300, NOW + seconds), 100, 2, meter_id="sensor.test")
        assert (result > 0) == (seconds == 360)


def test_restart_and_explicit_stop_do_not_resurrect_command():
    s = Session("t", 9, "connection-a", NOW, 300, None)
    s.stop()
    restored = Session.restore(s.as_dict())
    assert restored.terminal and restored.phase == "idle"
    with pytest.raises(ValueError):
        Session.restore({**s.as_dict(), "target_km": float("nan")})


def test_meter_reset_blocks_instead_of_resetting_budget():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 300, NOW)
    request = Session("t", 1, "connection-a", NOW, 300, None)
    request.update(NOW, s, d, 100, 1, meter_id="sensor.test")
    request.update(NOW + 1, s, d, 10, 1, meter_id="sensor.test")
    assert request.phase == "blocked"
    assert request.update(NOW + 2, s, d, 101, 1, meter_id="sensor.test") is None
