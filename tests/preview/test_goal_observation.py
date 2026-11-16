"""Goal and real droop evidence do not depend on battery-energy capture clocks."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from custom_components.adaptive_charge_preview.core.models import VehicleConfig, demand, observe_goal
from custom_components.adaptive_charge_preview.core.session import Session
from .test_core import NOW, snapshot
from .test_control import close, controlled, tick


def id3(soc, now=NOW):
    value = snapshot(soc=soc, energy=soc * .48, range_km=312, now=now)
    return replace(value, battery_kwh=replace(value.battery_kwh, measured_at=None))


def update(request, cfg, observed, now, meter, reference=None, controlled=True):
    calculation = reference or observed
    need = demand(cfg, calculation, request.target_km, now)
    return request.update(now, calculation, need, meter, cfg.droop_pp,
                          meter_id="meter", allow_estimated=controlled,
                          goal=observe_goal(cfg, observed, need, now))


def test_id3_fresh_soc_confirms_goal_without_energy_capture_and_uses_observed_soc():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 9, "connection-a", NOW, 249.6, NOW + 100)
    old = id3(29)
    assert update(request, cfg, old, NOW, 100) > 20
    current = id3(80, NOW + 60)
    # Simulate the coordinator's saved-reference calculation when energy is missing.
    current = replace(current, battery_kwh=replace(current.battery_kwh, value=None))
    assert update(request, cfg, current, NOW + 60, 126.33, old) == 0
    assert request.confirmed_soc == 80  # Never the saved calculation's old 29%.
    assert request.phase == "watching" and request.reason == "vehicle_goal_confirmed"
    assert request.last_meter == 126.33


@pytest.mark.parametrize("timestamp", [None, NOW - 4000, NOW + 180])
def test_received_old_or_untimed_soc_does_not_become_goal_evidence(timestamp):
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    old = id3(29)
    update(request, cfg, old, NOW, 100)
    current = id3(80, NOW + 60)
    current = replace(current, soc=replace(current.soc, measured_at=timestamp))
    update(request, cfg, current, NOW + 60, 102, old)
    assert request.confirmed_soc is None and request.remaining_kwh > 20


def test_goal_watch_restores_and_droop_needs_dwell_without_energy_capture():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, 100)
    request = Session.restore(request.as_dict())
    assert update(request, cfg, id3(79, NOW + 60), NOW + 60, 100) == 0
    assert update(request, cfg, id3(79, NOW + 359), NOW + 359, 100) == 0
    assert request.reason == "return_band_dwell"
    assert update(request, cfg, id3(79, NOW + 360), NOW + 360, 100) == pytest.approx(.48 * 1.15)
    assert request.confirmed_soc is None and request.anchor_meter == 100
    assert update(request, cfg, id3(79, NOW + 380), NOW + 380, 100.1) == pytest.approx(.48 * 1.15 - .1)


def test_tesla_same_soc_range_shortfall_reopens_after_return_band_dwell():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100) == 0
    before = request.as_dict()
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+60), NOW+60, 100) == 0
    assert request.reason == "range_goal_return_band_dwell"
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+359), NOW+359, 100) == 0
    need = update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+360), NOW+360, 100)
    assert need == pytest.approx(10 * 42 / 290 * 1.15)
    assert request.confirmed_soc is None and request.phase == "scheduled"
    for key in ("generation", "started_at", "connection_id", "origin_meter"):
        assert getattr(request, key) == before[key]
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+380), NOW+380, 100.1) == pytest.approx(need-.1)


def test_core_demand_fallback_does_not_invent_timed_range_authority():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    for seconds, km in ((0, 300), (60, 290), (360, 290)):
        observed = snapshot(soc=60, energy=42, range_km=km, now=NOW+seconds)
        remaining = request.update(NOW+seconds, observed, demand(cfg, observed, 300, NOW+seconds),
                                   100, 1, meter_id="meter")
        assert remaining == 0
    assert request.phase == "watching" and request.confirmed_soc == 60


def test_range_shortfall_within_band_or_recovery_does_not_reopen():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100)
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=298, now=NOW+60), NOW+60, 100) == 0
    assert request.below_since is None
    update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+100), NOW+100, 100)
    assert request.below_since == NOW+100
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300, now=NOW+200), NOW+200, 100)
    assert request.below_since is None
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+400), NOW+400, 100) == 0
    assert request.below_since == NOW+400 and request.confirmed_soc == 60


def test_same_soc_range_reopen_uses_saved_ratio_when_energy_is_missing():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 3, "connection-a", NOW, 300, None)
    old = snapshot(soc=60, energy=42, range_km=300)
    old = replace(old, battery_kwh=replace(old.battery_kwh, measured_at=None))
    update(request, cfg, old, NOW, 100)
    current = snapshot(soc=60, energy=42, range_km=290, now=NOW+60)
    current = replace(current, battery_kwh=replace(current.battery_kwh, value=None, measured_at=None))
    assert update(request, cfg, current, NOW+60, 100, old) == 0
    assert update(request, cfg, current, NOW+360, 100, old) == pytest.approx(10 * 42 / 290 * 1.15)
    before = request.as_dict()
    request.stop("explicit_stop")
    assert update(request, cfg, current, NOW+370, 100, old) is None
    assert request.anchor_meter == before["anchor_meter"] and request.terminal


@pytest.mark.parametrize("soh", [90, 50])
def test_id3_untimed_reference_change_alone_cannot_reopen(soh):
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 3, "connection-a", NOW, 156, None)
    assert update(request, cfg, id3(50), NOW, 100) == 0
    current = id3(50, NOW+60)
    current = replace(current, soh=replace(current.soh, value=soh))
    assert update(request, cfg, current, NOW+60, 100) == 0
    assert update(request, cfg, current, NOW+360, 100) == 0
    assert request.confirmed_soc == 50 and request.below_since is None


def test_lagging_capture_after_energy_tolerance_cannot_refill_allowance():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    captured = snapshot(soc=50, energy=35, range_km=250)
    initial = update(request, cfg, captured, NOW, 100)
    delivered = initial - .1
    assert update(request, cfg, captured, NOW+60, 100+delivered) == pytest.approx(.1)
    request.accept_tolerance(50)
    before = request.as_dict()
    for seconds in (90, 390, 700, 1000):
        # Both identical capture and a new timestamp on identical values occur
        # in vehicle integrations. Neither is independent range deterioration.
        observed = captured if seconds < 700 else snapshot(soc=50, energy=35, range_km=250, now=NOW+seconds)
        assert update(request, cfg, observed, NOW+seconds, 100+delivered) == 0
        assert request.phase == "within_tolerance" and request.below_since is None
        for key in ("anchor_meter", "anchor_need", "origin_meter", "goal_applied_at"):
            assert getattr(request, key) == before[key]


def test_range_deficit_exactly_at_band_keeps_confirmed_watch_stable():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    initial = snapshot(soc=60, energy=42, range_km=300)
    update(request, cfg, initial, NOW, 100)
    # Target 300, SOC60, range300*60/61 => exactly one pp estimated deficit.
    for seconds in (60, 360, 720, 1080):
        current = snapshot(soc=60, energy=42, range_km=300*60/61, now=NOW+seconds)
        assert update(request, cfg, current, NOW+seconds, 100) == 0
        assert request.confirmed_soc == 60 and request.below_since is None


def test_range_reopen_retains_green_energy_delivered_during_dwell_and_restore():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100)
    current = snapshot(soc=60, energy=42, range_km=290, now=NOW+60)
    update(request, cfg, current, NOW+60, 100)
    request = Session.restore(request.as_dict())
    assert update(request, cfg, current, NOW+359, 100.2) == 0
    assert update(request, cfg, current, NOW+360, 100.3) == pytest.approx(10 * 42/290 * 1.15 - .3)
    assert request.anchor_meter == 100 and request.origin_meter == 100
    assert update(request, cfg, current, NOW+370, 100.4) == pytest.approx(10 * 42/290 * 1.15 - .4)


def test_green_energy_can_cover_range_deficit_without_state_flap():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100)
    current = snapshot(soc=60, energy=42, range_km=290, now=NOW+60)
    update(request, cfg, current, NOW+60, 100)
    for seconds in (360, 720, 1080):
        assert update(request, cfg, current, NOW+seconds, 101) == 0
        assert request.phase == "watching" and request.below_since is None


def test_tolerance_ignores_range_rounding_but_reopens_for_material_deterioration():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    initial = update(request, cfg, snapshot(soc=50, energy=35, range_km=250), NOW, 100)
    update(request, cfg, snapshot(soc=50, energy=35, range_km=250), NOW+30, 100+initial-.1)
    request.accept_tolerance(50)
    for seconds in (60, 360):
        assert update(request, cfg, snapshot(soc=50, energy=35, range_km=249, now=NOW+seconds), NOW+seconds, 100+initial-.1) == 0
        assert request.below_since is None
    for seconds in (420, 720):
        remaining = update(request, cfg, snapshot(soc=50, energy=35, range_km=244, now=NOW+seconds), NOW+seconds, 100+initial-.1)
    assert remaining > 0 and request.tolerated_soc is None


def test_late_published_range_cannot_start_new_budget_but_existing_budget_continues():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100)
    late = snapshot(soc=60, energy=42, range_km=290, now=NOW+60)
    for seconds in (1260, 1560):
        assert update(request, cfg, late, NOW+seconds, 101) == 0
        assert request.below_since is None and request.anchor_meter is None
        assert request.reason == "range_goal_waiting_recent_capture"
    # New actual range already reflects the Green energy; within the band now.
    assert update(request, cfg, snapshot(soc=60, energy=42, range_km=298, now=NOW+1600), NOW+1600, 101) == 0
    active = Session("t", 3, "connection-a", NOW, 300, None)
    old = snapshot(soc=50, energy=35, range_km=250)
    initial = update(active, cfg, old, NOW, 100)
    assert update(active, cfg, old, NOW+1260, 101) == pytest.approx(initial-1)
    assert active.phase == "scheduled"


def test_soc_reopen_also_credits_green_energy_during_dwell():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 2, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, 100)
    update(request, cfg, id3(78, NOW+60), NOW+60, 100)
    assert update(request, cfg, id3(78, NOW+360), NOW+360, 100.3) == pytest.approx(2*.48*1.15-.3)


def test_soc_absorption_retains_credit_across_repeated_dwell_on_same_capture():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 2, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, 100)
    captured = id3(78, NOW+60)
    update(request, cfg, captured, NOW+60, 100)
    for seconds, delivered in ((360, .8), (361, .8), (661, 1.2), (1000, 1.2)):
        assert update(request, cfg, captured, NOW+seconds, 100+delivered) == 0
        request = Session.restore(request.as_dict())
        assert request.confirmed_soc == 80 and request.anchor_meter is None
        assert request.below_meter == 100


def test_active_b3_watch_restores_without_inventing_a_range_baseline():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 2, "connection-a", NOW, 300, None)
    update(request, cfg, snapshot(soc=60, energy=42, range_km=300), NOW, 100)
    data = request.as_dict()
    data.pop("goal_full_range_km")
    data.pop("below_meter")
    request = Session.restore(data)
    for seconds in (60, 360):
        assert update(request, cfg, snapshot(soc=60, energy=42, range_km=290, now=NOW+seconds), NOW+seconds, 100) == 0
    assert request.confirmed_soc == 60 and request.goal_full_range_km is None


@pytest.mark.parametrize("values", [
    {"goal_full_range_km": 49}, {"goal_full_range_km": 2001},
    {"below_meter": 101, "last_meter": 100}, {"below_meter": 100},
])
def test_restored_range_watch_rejects_invalid_reference_bounds(values):
    stored = Session("t", 2, "connection-a", NOW, 300, None).as_dict()
    with pytest.raises(ValueError, match="range-watch"):
        Session.restore({**stored, **values})


def test_capped_range_goal_never_reopens_above_vehicle_limit():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    request = Session("t", 3, "connection-a", NOW, 300, None)
    current = snapshot(soc=80, energy=56, range_km=280, limit=80)
    for seconds in (0, 60, 360, 720):
        value = replace(current, soc=replace(current.soc, measured_at=NOW+seconds),
                        range_km=replace(current.range_km, measured_at=NOW+seconds))
        assert update(request, cfg, value, NOW+seconds, 100) == 0
    assert request.phase == "watching" and request.confirmed_soc == 80
    assert demand(cfg, current, 300, NOW).limited


def test_missing_or_backdated_droop_never_replenishes_and_resets_dwell():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, 100)
    update(request, cfg, id3(79, NOW + 60), NOW + 60, 100)
    old = id3(78, NOW + 1)
    assert update(request, cfg, old, NOW + 360, 100) == 0
    assert request.confirmed_soc == 80 and request.below_since is None
    missing = replace(id3(78, NOW + 400), soc=replace(old.soc, measured_at=None))
    assert update(request, cfg, missing, NOW + 400, 100) == 0


def test_partial_progress_rebases_once_and_same_soc_does_not_refill_budget():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    old = id3(29)
    update(request, cfg, old, NOW, 100)
    higher = id3(40, NOW + 60)
    remaining = update(request, cfg, higher, NOW + 60, 105)
    assert remaining == pytest.approx(40 * .48 * 1.15)
    assert update(request, cfg, higher, NOW + 100, 106) == pytest.approx(remaining - 1)
    assert update(request, cfg, id3(40, NOW + 120), NOW + 120, 107) == pytest.approx(remaining - 2)
    assert request.anchor_meter == 105


def test_initial_capture_repetition_cannot_reset_the_meter_anchor():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    observed = id3(29)
    initial = update(request, cfg, observed, NOW, 100)
    assert update(request, cfg, observed, NOW + 30, 101) == pytest.approx(initial - 1)
    assert request.anchor_meter == 100


def test_saved_ac_ratio_avoids_mixing_new_soc_with_delayed_energy():
    cfg = VehicleConfig("v", "ID3", "id3")
    observed = replace(id3(40), battery_kwh=replace(id3(29).battery_kwh, value=14))
    need = demand(cfg, observed, 249.6, NOW)
    goal = observe_goal(cfg, observed, need, NOW, estimated_per_pp=14 / 29 * 1.15)
    assert goal.estimated_need_kwh == pytest.approx(40 * 14 / 29 * 1.15)


@pytest.mark.parametrize("saved_range", [390, 250])
def test_id3_goal_threshold_comes_from_live_range_and_soh(saved_range):
    cfg = VehicleConfig("v", "ID3", "id3")
    reference = replace(id3(29), range_km=replace(id3(29).range_km, value=saved_range))
    need = demand(cfg, reference, 222.14, NOW)
    current = replace(id3(70), soh=replace(id3(70).soh, value=89))
    current = replace(current, battery_kwh=replace(current.battery_kwh, value=None))
    goal = observe_goal(cfg, current, need, NOW, estimated_per_pp=.48 * 1.15)
    assert goal.effective_soc == 80 and goal.full_range_km == pytest.approx(277.68)
    assert not goal.reached and goal.threshold_source == "live_vehicle_reference"
    assert observe_goal(cfg, replace(current, soc=replace(current.soc, value=80)), need, NOW).reached


@pytest.mark.parametrize("saved_range", [200, 400])
def test_tesla_limit_capped_goal_uses_live_soc_range_ratio(saved_range):
    cfg = VehicleConfig("t", "Tesla", "tesla")
    reference = snapshot(soc=50, energy=35, range_km=saved_range)
    need = demand(cfg, reference, 999, NOW)
    current = snapshot(soc=70, energy=49, range_km=420)
    goal = observe_goal(cfg, current, need, NOW)
    assert goal.full_range_km == pytest.approx(600) and not goal.reached
    at_limit = snapshot(soc=80, energy=56, range_km=480)
    assert observe_goal(cfg, at_limit, need, NOW).reached


def test_missing_live_threshold_does_not_confirm_from_saved_reference():
    cfg = VehicleConfig("v", "ID3", "id3")
    current = id3(80)
    need = demand(cfg, current, 249.6, NOW)
    assert observe_goal(cfg, replace(current, soh=replace(current.soh, value=None)), need, NOW) is None


@pytest.mark.parametrize("meter", [None, -1])
def test_same_capture_can_be_applied_once_after_invalid_meter_recovers(meter):
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    old = id3(29)
    update(request, cfg, old, NOW, 100)
    newer = id3(40, NOW + 60)
    assert update(request, cfg, newer, NOW + 60, meter) is None
    assert request.anchor_meter == 100
    expected = 40 * .48 * 1.15
    assert update(request, cfg, newer, NOW + 90, 105) == pytest.approx(expected)
    assert request.goal_applied_at == NOW + 60
    assert update(request, cfg, newer, NOW + 100, 106) == pytest.approx(expected - 1)


def test_read_only_tick_does_not_consume_the_capture_needed_after_enable():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(29), NOW, 100)
    newer = id3(40, NOW + 60)
    assert update(request, cfg, newer, NOW + 60, 105, controlled=False) is None
    assert update(request, cfg, newer, NOW + 90, 105) == pytest.approx(40 * .48 * 1.15)


def test_old_energy_reference_does_not_expire_live_goal_or_existing_allowance():
    cfg = VehicleConfig("v", "ID3", "id3")
    reference = id3(29, NOW - 86400 * 30)
    need = demand(cfg, reference, 249.6, NOW)
    goal = observe_goal(cfg, id3(80), need, NOW, estimated_per_pp=.48 * 1.15)
    assert goal.reached and goal.energy_reference == "not_needed_for_reached_goal"
    # Invalid capacity cannot grant energy, even when the live SOC is fresh.
    assert observe_goal(cfg, id3(40), need, NOW, estimated_per_pp=99) is None
    assert observe_goal(cfg, id3(80), need, NOW, estimated_per_pp=99).reached


@pytest.mark.parametrize("key", ["goal_observed_at", "goal_applied_at", "goal_full_range_km", "below_meter"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_new_goal_clock_fields_have_strict_persistence_validation(key, value):
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    with pytest.raises(ValueError):
        Session.restore({**request.as_dict(), key: value})


def test_b2_store_without_new_goal_fields_restores():
    data = Session("v", 1, "connection-a", NOW, 249.6, None).as_dict()
    data.pop("goal_observed_at")
    data.pop("goal_applied_at")
    data.pop("goal_full_range_km")
    data.pop("below_meter")
    restored = Session.restore(data)
    assert restored.goal_observed_at is None and restored.goal_applied_at is None
    assert restored.goal_full_range_km is None and restored.below_meter is None


def test_no_meter_goal_still_pins_meter_identity_before_any_future_refill():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, None)
    current = id3(79, NOW + 360)
    need = demand(cfg, current, 249.6, NOW + 360)
    assert request.update(NOW + 360, current, need, 100, 1, meter_id="replacement",
                          allow_estimated=True, goal=observe_goal(cfg, current, need, NOW + 360)) is None
    assert request.meter_fault


def test_read_only_partial_data_never_grants_estimated_refill():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(80), NOW, 100, controlled=False)
    update(request, cfg, id3(79, NOW + 60), NOW + 60, 100, controlled=False)
    assert update(request, cfg, id3(79, NOW + 360), NOW + 360, 100, controlled=False) == 0
    assert request.confirmed_soc == 80


def test_goal_without_meter_stops_but_never_grants_droop_energy():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    assert update(request, cfg, id3(80), NOW, None) == 0
    assert request.confirmed_soc == 80
    assert update(request, cfg, id3(79, NOW + 360), NOW + 360, None) is None
    assert request.anchor_meter is None


def test_stop_and_disconnect_win_over_partial_goal_data():
    cfg = VehicleConfig("v", "ID3", "id3")
    for reason in ("explicit_stop", "physical_charger_unplug_edge"):
        request = Session("v", 1, "connection-a", NOW, 249.6, None)
        update(request, cfg, id3(80), NOW, 100)
        request.stop(reason)
        assert update(request, cfg, id3(75, NOW + 360), NOW + 360, 100) is None
        assert request.terminal and request.reason == reason


def test_tesla_range_goal_needs_no_energy_capture_but_needs_actual_range():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    observed = snapshot(soc=75, energy=52.5, range_km=300)
    observed = replace(observed, battery_kwh=replace(observed.battery_kwh, measured_at=None))
    need = demand(cfg, observed, 300, NOW)
    assert not need.source_fresh
    assert observe_goal(cfg, observed, need, NOW).reached
    assert observe_goal(cfg, replace(observed, range_km=replace(observed.range_km, measured_at=None)), need, NOW) is None


def test_tesla_divergent_source_clocks_cannot_confirm_goal():
    cfg = VehicleConfig("t", "Tesla", "tesla")
    observed = snapshot(soc=75, energy=52.5, range_km=300)
    need = demand(cfg, observed, 300, NOW)
    inconsistent = replace(observed, range_km=replace(observed.range_km, measured_at=NOW - 121))
    assert observe_goal(cfg, inconsistent, need, NOW) is None


def test_goal_in_same_tick_as_meter_regression_keeps_fault_priority():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 249.6, None)
    update(request, cfg, id3(29), NOW, 100)
    assert update(request, cfg, id3(80, NOW + 60), NOW + 60, 99) is None
    assert request.meter_fault and request.confirmed_soc is None
    assert update(request, cfg, id3(79, NOW + 360), NOW + 360, 101) is None


def test_raise_target_from_watch_keeps_meter_credit_and_requires_new_goal():
    cfg = VehicleConfig("v", "ID3", "id3")
    request = Session("v", 1, "connection-a", NOW, 156, None)
    update(request, cfg, id3(40), NOW, 100)
    assert update(request, cfg, id3(50, NOW + 60), NOW + 60, 105) == 0
    old_need = demand(cfg, id3(50, NOW + 60), 156, NOW + 60)
    new_need = demand(cfg, id3(50, NOW + 60), 187.2, NOW + 60)
    before = request.anchor_need
    request.set_target(187.2, None, new_need, old_need, allow_estimated=True)
    assert request.anchor_meter == 100 and request.origin_meter == 100
    assert request.anchor_need == pytest.approx(before + new_need.needed_kwh - old_need.needed_kwh)
    assert update(request, cfg, id3(50, NOW + 60), NOW + 90, 105) > 0
    assert request.confirmed_soc is None and request.generation == 1


@pytest.mark.asyncio
async def test_real_ha_goal_releases_manual_with_missing_energy_sensor(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path)
    try:
        await tick(c, 3)
        before = c.sessions[vehicle].as_dict()
        hass.states.async_set("sensor.tesla_energy", "unavailable", {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.tesla_soc", 62.5, {"unit_of_measurement": "%"})
        hass.states.async_set("sensor.tesla_range", 250, {"unit_of_measurement": "km"})
        result = await tick(c, 4)
        row = result["vehicles"][vehicle]
        assert row["calculation_from_saved_reference"] and row["goal_observation"]["reached"]
        assert c.sessions[vehicle].confirmed_soc == 62.5 and row["phase"] == "watching"
        assert not c.control.owned_manual and ("mode", "Green") in calls
        for name in ("generation", "started_at", "origin_meter"):
            assert getattr(c.sessions[vehicle], name) == before[name]
        stamp = datetime.now(timezone.utc).timestamp() + 30
        for entity, value, unit in (("sensor.tesla_soc", 62.5, "%"), ("sensor.tesla_range", 240, "km")):
            hass.states.async_set(entity, value, {"unit_of_measurement": unit, "data_captured_at": stamp})
        with patch("custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow", return_value=datetime.fromtimestamp(stamp+300, timezone.utc)):
            row = (await tick(c))["vehicles"][vehicle]
        assert row["reason"] == "range_goal_waiting_recent_capture"
        stamp += 330
        for entity, value, unit in (("sensor.tesla_soc", 62.5, "%"), ("sensor.tesla_range", 240, "km")):
            hass.states.async_set(entity, value, {"unit_of_measurement": unit, "data_captured_at": stamp})
        with patch("custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow", return_value=datetime.fromtimestamp(stamp, timezone.utc)):
            row = (await tick(c))["vehicles"][vehicle]
        assert row["reason"] == "range_goal_return_band_dwell"
        assert row["goal_and_budget_aligned"] and row["estimated_ac_kwh_per_soc_point"] == pytest.approx(.7*1.15)
        assert row["observations"]["battery_kwh"]["value"] is None
        assert not row["demand"]["source_fresh"] and row["calculation_from_saved_reference"]
        assert row["goal_observation"]["range_measured_at"] == stamp
    finally:
        await close(hass, c)


@pytest.mark.asyncio
async def test_new_lower_soc_corrects_initial_budget_without_energy_capture_once(tmp_path):
    hass, c, vehicle, calls = await controlled(tmp_path, stale=True)
    try:
        await tick(c, 3)
        initial = c.sessions[vehicle].anchor_need
        captured = c.sessions[vehicle].started_at + 1
        hass.states.async_set("sensor.tesla_energy", "unavailable", {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.tesla_soc", 20, {"unit_of_measurement": "%", "data_captured_at": captured})
        hass.states.async_set("sensor.tesla_range", 80, {"unit_of_measurement": "km", "data_captured_at": captured})
        await tick(c, 2)
        request = c.sessions[vehicle]
        assert request.anchor_soc == 20 and request.remaining_kwh > initial * 2
        allowance, origin = request.anchor_need, request.origin_meter
        hass.states.async_set("sensor.iq_energy", 101, {"unit_of_measurement": "kWh"})
        await tick(c, 2)
        assert request.anchor_need == allowance and request.remaining_kwh == pytest.approx(allowance - 1)
        assert request.origin_meter == origin and c.control.fault is None
    finally:
        await close(hass, c)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["target", "limit"])
async def test_goal_and_budget_delta_share_live_range_and_saved_ratio(tmp_path, change):
    hass, c, vehicle, calls = await controlled(tmp_path)
    try:
        if change == "limit":
            await c.async_preview_action("set_target", vehicle, {"target_range_km": 300})
        await tick(c, 3)
        request = c.sessions[vehicle]
        before = request.as_dict()
        # Keep the capture/SOC unchanged: no independent SOC-progress rebase can
        # conceal a wrong target/limit delta or reset already delivered credit.
        hass.states.async_set("sensor.tesla_energy", "unavailable", {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.tesla_range", 250, {"unit_of_measurement": "km"})
        hass.states.async_set("sensor.iq_energy", 102, {"unit_of_measurement": "kWh"})
        if change == "target":
            await c.async_preview_action("set_target", vehicle, {"target_range_km": 300})
            delta_pp, effective_soc = 10, 60
        else:
            hass.states.async_set("number.tesla_limit", 55, {"unit_of_measurement": "%"})
            delta_pp, effective_soc = -5, 55
        result = await tick(c, 2)
        row = result["vehicles"][vehicle]
        assert row["goal_and_budget_aligned"] and not row["demand"]["source_fresh"]
        assert row["demand"]["full_range_km"] == row["goal_observation"]["full_range_km"] == 500
        assert row["demand"]["effective_soc"] == row["goal_observation"]["effective_soc"] == effective_soc
        assert row["demand"]["needed_kwh"] == pytest.approx(row["goal_observation"]["estimated_need_kwh"])
        assert row["estimated_ac_kwh_per_soc_point"] == pytest.approx(.7 * 1.15)
        assert request.anchor_need == pytest.approx(before["anchor_need"] + delta_pp * .7 * 1.15)
        assert request.remaining_kwh == pytest.approx(request.anchor_need - 2)
        for key in ("origin_meter", "anchor_meter", "started_at", "generation"):
            assert getattr(request, key) == before[key]
        await tick(c, 2)
        assert request.remaining_kwh == pytest.approx(request.anchor_need - 2)
    finally:
        await close(hass, c)
