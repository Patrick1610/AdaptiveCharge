"""Behavior regressions from independent review, including conservation checks."""

from dataclasses import replace
import random

import pytest

from custom_components.adaptive_charge_preview.core.models import Tariff, VehicleConfig, demand
from custom_components.adaptive_charge_preview.core.planner import _Slot, _practical, plan
from custom_components.adaptive_charge_preview.core.session import Session
from .test_core import NOW, prices, snapshot


def test_transient_meter_outage_recovers_without_new_allowance():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 300, NOW)
    request = Session("t", 1, "connection-a", NOW, 300, None)
    request.update(NOW, s, d, 100, 1, meter_id="meter-a")
    request.update(NOW + 1, s, d, None, 1, meter_id="meter-a")
    assert request.phase == "waiting_data"
    assert request.update(NOW + 2, s, d, 102, 1, meter_id="meter-a") == pytest.approx(d.needed_kwh - 2)


def test_foreign_meter_cannot_reset_or_debit_budget():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 300, NOW)
    request = Session("t", 1, "connection-a", NOW, 300, None)
    request.update(NOW, s, d, 100, 1, meter_id="meter-a")
    assert request.update(NOW + 1, s, d, 5, 1, meter_id="meter-b") is None
    assert request.meter_fault and request.reason == "meter_identity_changed"
    assert request.anchor_meter == 100


def test_missing_connection_identity_is_visible_and_first_identity_is_adopted():
    c, s = VehicleConfig("t", "Tesla", "tesla"), snapshot()
    d = demand(c, s, 300, NOW)
    request = Session("t", 1, None, NOW, 300, None)
    request.update(NOW, replace(s,connection_id=None), d, 100, 1, meter_id="meter-a")
    assert request.reason == "connection_identity_unknown" and request.anchor_meter is None
    request.update(NOW + 1, s, d, 100, 1, meter_id="meter-a")
    assert request.connection_id == "connection-a"


@pytest.mark.parametrize("field,value", [("anchor_meter",float("nan")),("last_meter",float("inf")),("terminal","yes"),("phase",123),("target_km","300"),("extra","foreign"),("meter_id",123)])
def test_restore_rejects_corrupt_data(field,value):
    data = Session("t",1,"a",NOW,300,None).as_dict()
    data[field] = value
    with pytest.raises(ValueError):
        Session.restore(data)


def test_restore_missing_field_uses_value_error_contract():
    with pytest.raises(ValueError):
        Session.restore({})


def test_target_change_without_source_keeps_original_budget_and_waits():
    request = Session("t",1,"a",NOW,300,None,anchor_meter=100,anchor_need=10)
    with pytest.raises(ValueError):
        request.set_target(400,None,None,None)
    assert request.target_km == 300 and request.anchor_meter == 100 and request.anchor_need == 10
    assert request.reason == "target_changed_awaiting_data"


@pytest.mark.parametrize("stale_side",["new","old"])
def test_target_change_cannot_adjust_budget_from_provisional_vehicle_data(stale_side):
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot()
    old=demand(c,s,300,NOW)
    new=demand(c,s,350,NOW)
    if stale_side=="new":
        new=replace(new,source_fresh=False)
    else:
        old=replace(old,source_fresh=False)
    request=Session("t",1,"a",NOW,300,None,anchor_meter=100,anchor_need=10)
    with pytest.raises(ValueError):
        request.set_target(350,NOW+10000,new,old)
    assert request.target_km==300 and request.departure==NOW+10000
    assert request.anchor_meter==100 and request.anchor_need==10


def test_departure_only_change_needs_no_vehicle_data_or_new_energy_round():
    request=Session("t",1,"a",NOW,300,None,anchor_meter=100,anchor_need=10,phase="watching",confirmed_soc=75)
    request.set_target(300,NOW+10000,None,None)
    assert request.departure==NOW+10000 and request.target_km==300
    assert request.phase=="watching" and request.confirmed_soc==75
    assert request.anchor_meter==100 and request.anchor_need==10


def test_price_gap_does_not_misrepresent_green_as_grid_energy():
    rows=prices(solar=3)
    del rows[4]
    p=plan(NOW,NOW+10800,10,0.8,rows)
    assert p.mode == "Green" and len(p.windows)==1 and "price_coverage_gap" in p.warnings
    assert p.expected_solar_kwh==0 and p.cost is None and p.windows[0].end==NOW+10800


def test_total_omission_cannot_exceed_one_tolerance_band():
    # Solar-filled neighbours cannot absorb additional net energy. The two
    # independent boundary fragments must not each receive a full tolerance.
    slots=[_Slot(0,60,0.1,0,0,11,60),_Slot(60,900,0.2,0,11,0),_Slot(900,960,0.1,0,0,11,60)]
    windows,omitted,warnings=_practical(slots,11,180,0.2)
    assert omitted <= 0.2
    assert sum(w.additional_kwh for w in windows) + omitted == pytest.approx(22/60)
    assert "short_block_required_for_significant_deficit" in warnings


def test_repeated_relocation_produces_ordered_nonoverlapping_windows():
    slots=[]
    for i in range(7):
        slots.append(_Slot(i*900,(i+1)*900,0.1+i*0.1,0,0,11,40 if i in (0,3,6) else 0))
    windows,omitted,_=_practical(slots,11,180,0.01)
    assert all(a.end <= b.start for a,b in zip(windows,windows[1:]))
    assert all(0 <= w.start < w.end <= 7*900 for w in windows)
    assert sum(w.additional_kwh for w in windows)+omitted == pytest.approx(11*120/3600)


def test_partial_predecessor_joins_selected_successor():
    p=plan(NOW,NOW+10800,3,0.1,prices(buys=[0.5,0.5,0.2,0.1]+[0.5]*8))
    assert len(p.windows)==1 and p.windows[0].end==NOW+3600


@pytest.mark.parametrize("seed", range(40))
def test_energy_and_cost_conservation(seed):
    r=random.Random(seed)
    rows=[Tariff(NOW+i*900,NOW+(i+1)*900,r.uniform(-0.2,0.5),r.uniform(-0.1,0.2),r.uniform(0,2)) for i in range(12)]
    if seed % 2:
        rows=rows[:r.randrange(7,11)]
    needed=r.uniform(1,15)
    p=plan(NOW,NOW+10800,needed,0.8,rows)
    if p.reason in {"green_between_boost_windows","selected_boost_window","expected_green_sufficient","small_remainder_within_tolerance","unknown_future_prices_defer_boost"}:
        assert p.expected_solar_kwh + sum(w.additional_kwh for w in p.windows) + p.omitted_kwh + p.deferred_kwh == pytest.approx(needed)
        if p.cost is not None:
            assert p.cost == pytest.approx(p.green_cost + sum(w.cost for w in p.windows))
        else:
            assert p.deferred_kwh > 0 and p.green_cost is None
        assert all(a.end <= b.start for a,b in zip(p.windows,p.windows[1:]))
        assert all(NOW <= w.start < w.end <= NOW+9900 for w in p.windows)
        assert p.omitted_kwh <= 0.8


def test_id3_energy_soh_and_incoherent_times():
    c=VehicleConfig("v","VW","id3")
    s=snapshot(range_km=400)
    d=demand(c,s,300,NOW)
    assert d.needed_kwh == pytest.approx(25*0.7*1.15)
    assert demand(c,replace(s,soh=replace(s.soh,measured_at=NOW-4000)),300,NOW).source_fresh
    assert not demand(c,replace(s,battery_kwh=replace(s.battery_kwh,measured_at=NOW-300)),300,NOW).source_fresh
    with pytest.raises(ValueError):
        demand(c,replace(s,battery_kwh=replace(s.battery_kwh,unit="Wh")),300,NOW)


def test_static_limit_does_not_need_a_forged_capture_time():
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot()
    d=demand(c,replace(s,limit_soc=replace(s.limit_soc,measured_at=NOW-20*86400)),300,NOW)
    assert d.source_fresh


def test_mixed_stale_energy_and_soc_is_provisional_not_a_capacity_exception():
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot(soc=20)
    s=replace(s,soc=replace(s.soc,measured_at=NOW-600))
    d=demand(c,s,300,NOW)
    assert not d.source_fresh and "vehicle_measurement_incoherent" in d.warnings


def test_stale_connection_mismatch_stops_accounting_without_cancelling():
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot()
    d=demand(c,s,300,NOW)
    request=Session("t",1,"connection-a",NOW,300,None)
    request.update(NOW,s,d,100,1,meter_id="meter")
    changed=replace(s,connection_id="connection-b",connection_measured_at=NOW-900)
    assert request.update(NOW+1,changed,d,103,1,meter_id="meter") is None
    assert request.last_meter==100 and request.anchor_meter==100 and not request.terminal
    assert request.reason=="connection_identity_mismatch_unverified"


def test_trailing_unknown_prices_schedule_one_unpriced_block():
    p=plan(NOW,NOW+10800,3,0.8,prices(count=6))
    assert p.mode=="Green" and p.deferred_kwh==0 and "price_coverage_partial" in p.warnings
    assert len(p.windows)==1 and p.windows[0].additional_kwh==pytest.approx(3)
    assert p.windows[0].end==NOW+10800 and p.cost is None


def test_inconsistent_anchor_is_rejected_but_past_departure_is_a_direct_request():
    request=Session("t",1,"a",NOW,300,NOW-60)
    assert Session.restore(request.as_dict()).departure==NOW-60
    with pytest.raises(ValueError,match="Inconsistent"):
        Session.restore({**request.as_dict(),"anchor_meter":100})


def test_default_id3_soh_is_explicit_reference_not_a_fresh_observation():
    from custom_components.adaptive_charge_preview.core.models import VehicleSnapshot
    s=snapshot(range_km=400)
    s=VehicleSnapshot(s.soc,s.battery_kwh,s.range_km,s.limit_soc)
    assert demand(VehicleConfig("v","VW","id3"),s,300,NOW).source_fresh


def test_foreign_identity_gap_never_debits_later_energy_on_reused_id():
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot()
    d=demand(c,s,300,NOW)
    request=Session("t",1,"connection-a",NOW,300,None)
    request.update(NOW,s,d,100,1,meter_id="meter")
    request.update(NOW+1,replace(s,connection_id="connection-b",connection_measured_at=NOW-900),d,105,1,meter_id="meter")
    assert request.update(NOW+2,s,d,106,1,meter_id="meter") is None
    assert request.last_meter==100 and request.identity_gap


def test_unknown_tail_uses_one_block_for_the_entire_remaining_allowance():
    p=plan(NOW,NOW+10800,8,0.8,prices(count=10))
    assert p.deferred_kwh==0 and len(p.windows)==1
    assert sum(w.additional_kwh for w in p.windows)==pytest.approx(8)
    assert p.cost is None and p.green_cost is None
    assert p.mode=="Green" and p.windows[0].end==NOW+10800


@pytest.mark.parametrize("restored",[False,True])
def test_missing_id_is_a_temporary_wait_and_same_connection_preserves_credit(restored):
    c,s=VehicleConfig("t","Tesla","tesla"),snapshot()
    d=demand(c,s,300,NOW)
    request=Session("t",1,"connection-a",NOW,300,None)
    request.update(NOW,s,d,100,1,meter_id="meter")
    request.update(NOW+1,s,d,101,1,meter_id="meter")
    if restored:
        request=Session.restore(request.as_dict())
    assert request.update(NOW+2,replace(s,connection_id=None),d,102,1,meter_id="meter") is None
    assert not request.terminal and not request.identity_gap
    assert request.anchor_meter==100 and request.last_meter==101
    # A stable unique connection ID returning proves the same attachment; count
    # its full metered energy, without resetting the original energy allowance.
    assert request.update(NOW+3,s,d,103,1,meter_id="meter")==pytest.approx(d.needed_kwh-3)
    assert request.anchor_meter==100


def test_submillisecond_price_boundary_does_not_hide_cost_without_warning():
    rows=prices(count=11)
    rows[-1]=replace(rows[-1],end=rows[-1].end-0.0001)
    p=plan(NOW,NOW+10800,3,0.8,rows)
    assert p.deferred_kwh==0 and p.cost is not None


def test_stalled_partial_price_feed_eventually_reaches_urgency():
    assert len(plan(NOW,NOW+10800,3,0.8,prices(count=6)).windows)==1
    p=plan(NOW+10000,NOW+10800,3,0.8,prices(count=6))
    assert p.mode=="Manual" and p.reason=="unpriced_deadline_fallback"


@pytest.mark.parametrize("fault",["meter_fault","identity_gap"])
def test_tolerance_cannot_hide_a_latched_fault(fault):
    request=Session("t",1,"connection-a",NOW,300,None,anchor_meter=100,anchor_need=1,phase="blocked")
    setattr(request,fault,True)
    with pytest.raises(ValueError):
        request.accept_tolerance(50)
    assert request.phase=="blocked"
