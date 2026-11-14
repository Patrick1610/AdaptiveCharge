"""Economic decisions, incomplete horizons and real HA ownership regressions."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import product
from random import Random
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.adaptive_charge_preview.core.economics import economic_plan, idle_green_policy, mean_import_price
from custom_components.adaptive_charge_preview.core.models import Tariff
from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
from custom_components.adaptive_charge_preview.sources import planning_capacity, price_rows
from custom_components.adaptive_charge_preview.tariffs import export_tariffs
from .test_control import close, controlled, fast_feedback, tick

ZONE = ZoneInfo("Europe/Amsterdam")
DAY = datetime(2027,6,1,tzinfo=ZONE).timestamp()


def days(start=DAY, count=192):
    return [Tariff(start+i*900,start+(i+1)*900,.25 if i%2==0 else .35) for i in range(count)]


def balance(result):
    return result.expected_solar_kwh+sum(w.additional_kwh for w in result.windows)+result.omitted_kwh+result.shortfall_kwh


def test_weighted_mean_includes_full_today_and_only_complete_tomorrow():
    rows = days()
    assert mean_import_price(rows,DAY+12*3600,"Europe/Amsterdam") == pytest.approx(.3)
    tomorrow = [replace(r,buy=.5) for r in rows[96:]]
    assert mean_import_price(rows[:96]+tomorrow,DAY,"Europe/Amsterdam") == pytest.approx(.4)
    assert mean_import_price(rows[:96]+tomorrow[:-1],DAY,"Europe/Amsterdam") == pytest.approx(.3)
    assert mean_import_price(rows[1:],DAY,"Europe/Amsterdam") is None
    assert mean_import_price(rows+[rows[0]],DAY,"Europe/Amsterdam") is None


@pytest.mark.parametrize("date,hours",[("2027-03-28",23),("2027-10-31",25)])
def test_mean_uses_real_dst_day_lengths(date,hours):
    start=datetime.fromisoformat(date).replace(tzinfo=ZONE)
    a=start.timestamp()
    b=(start+timedelta(days=1)).timestamp()
    c=(start+timedelta(days=2)).timestamp()
    assert (b-a)/3600==hours
    rows=[Tariff(a,b,.2),Tariff(b,c,.4)]
    assert mean_import_price(rows,a,"Europe/Amsterdam")==pytest.approx((hours*.2+24*.4)/(hours+24))


def test_five_cent_offset_allows_25_at_30_mean_and_reserves_uncovered_energy():
    rows=days(count=96)
    result=economic_plan(DAY,DAY+72*3600,11,0,rows,early_offset_cents=5)
    assert result.mean_import==pytest.approx(.3)
    assert result.price_ceiling==pytest.approx(.25)
    assert result.mode=="Manual" and result.windows[0].start==DAY
    assert all(not (DAY+900<=w.start<DAY+1800) for w in result.windows)
    assert balance(result)==pytest.approx(11)
    strict=economic_plan(DAY,DAY+72*3600,11,0,rows,early_offset_cents=6)
    assert strict.mode=="Green" and len(strict.windows)==1
    assert strict.windows[0].end==DAY+72*3600 and strict.windows[0].cost is None


def test_full_horizon_and_urgency_remove_the_preference_ceiling():
    full=economic_plan(DAY,DAY+8*3600,11,0,days(),early_offset_cents=100)
    assert not full.early_threshold_active and full.price_ceiling is None
    assert balance(full)==pytest.approx(11)
    urgent=economic_plan(DAY,DAY+3600,11,0,days(),early_offset_cents=100)
    assert urgent.mode=="Manual" and balance(urgent)==pytest.approx(11)


@pytest.mark.parametrize("sell,expected",[(.22,"Green"),(.30,"Pause")])
def test_green_can_win_both_alternatives_or_be_replaced_by_cheaper_import(sell,expected):
    rows=[Tariff(DAY,DAY+3600,.30,sell,3),Tariff(DAY+3600,DAY+3*3600,.26 if sell==.22 else .20,.1)]
    result=economic_plan(DAY,DAY+3*3600,11,0,rows,reserve_min=0,export_known=True)
    assert result.mode==expected and balance(result)==pytest.approx(11)
    assert result.cost==pytest.approx(3*.22+8*.26 if sell==.22 else 11*.20)
    if expected=="Pause":
        assert result.pause_windows and result.expected_solar_kwh==0
        assert sum(w.kwh for w in result.windows)==pytest.approx(11)


def test_unknown_export_keeps_green_and_never_invents_complete_cost():
    rows=[Tariff(DAY,DAY+3600,.4,0,3),Tariff(DAY+3600,DAY+3*3600,.2)]
    result=economic_plan(DAY,DAY+3*3600,11,0,rows,reserve_min=0)
    assert not result.pause_windows and result.cost is None
    assert result.expected_solar_kwh==pytest.approx(3)
    assert balance(result)==pytest.approx(11)


@pytest.mark.parametrize("seed",range(40))
def test_economic_allocation_conserves_energy_and_respects_capacity(seed):
    rng=Random(seed)
    rows=[Tariff(DAY+i*900,DAY+(i+1)*900,rng.uniform(-.15,.5),rng.uniform(-.2,.4),rng.uniform(0,7)) for i in range(24)]
    needed=rng.uniform(1,25)
    result=economic_plan(DAY,DAY+6*3600,needed,0,rows,power=11,reserve_min=0,minimum_min=1,export_known=True)
    assert balance(result)==pytest.approx(needed,abs=1e-5)
    assert all(w.kwh/(w.end-w.start)*3600<=11.00001 for w in result.windows)
    assert all(a.end<=b.start+.001 for a,b in zip(result.windows,result.windows[1:]))
    assert all(w.end<=DAY+6*3600 for w in result.windows)
    if result.cost is not None:
        assert result.cost==pytest.approx(result.green_cost+sum(w.cost for w in result.windows),abs=1e-5)


def test_small_discrete_oracle_has_no_cheaper_feasible_whole_slot_plan():
    rows=[Tariff(DAY+i*900,DAY+(i+1)*900,b,s,2) for i,(b,s) in enumerate(((.4,.1),(.2,.3),(.3,.15)))]
    for needed in (1,1.5,2,2.5):
        candidates=[]
        for modes in product((0,2,4),repeat=3):
            if abs(sum(p*.25 for p in modes)-needed)<1e-8:
                candidates.append(sum((min(p,2)*r.sell+max(0,p-2)*r.buy)*.25 for p,r in zip(modes,rows)))
        result=economic_plan(DAY,DAY+2700,needed,0,rows,power=4,reserve_min=0,minimum_min=1,export_known=True)
        assert balance(result)==pytest.approx(needed)
        assert result.cost<=min(candidates)+1e-7


def test_idle_comparison_has_no_early_offset_and_missing_data_allows_green():
    rows=[replace(r,sell=.31) for r in days(count=96)]
    assert idle_green_policy(rows,DAY,"Europe/Amsterdam",enabled=True,export_known=True)["pause"]
    assert not idle_green_policy(rows,DAY,"Europe/Amsterdam",enabled=False,export_known=True)["pause"]
    assert not idle_green_policy(rows,DAY,"Europe/Amsterdam",enabled=True,export_known=False)["pause"]
    assert not idle_green_policy([],DAY,"Europe/Amsterdam",enabled=True,export_known=True)["pause"]


async def idle_setup(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    c.sessions[v].stop("fixture_no_request")
    c.control.grant=None
    cfg={**c.entry.data,"economic_enabled":True,"idle_green_price_filter":True,"price_entity":"sensor.price","export_price_key":"sell"}
    object.__setattr__(c.entry,"data",MappingProxyType(cfg))
    now=datetime.now(ZONE)
    start=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    attrs={"data":[{"time":r.start,"price":.2,"sell":.4} for r in days(start,count=96)]}
    hass.states.async_set("sensor.price",.2,attrs)
    hass.states.async_set("switch.ev_charger","on")
    hass.states.async_set("binary_sensor.ev_charging","on")
    hass.states.async_set("sensor.hw_power",3000,{"unit_of_measurement":"W"})
    return hass,c,v,calls,attrs


@pytest.mark.asyncio
async def test_idle_green_pause_and_resume_after_price_loss_never_select_manual(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        await tick(c,2)
        assert c.control.green_hold["stage"]=="paused"
        assert calls==[("switch","off")]
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(c,4)
        assert calls==[("switch","off"),("mode","Green"),("switch","on")]
        assert c.control.green_hold is None and c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_explicit_stop_while_economically_paused_blocks_old_resume(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        await tick(c,2)
        await c.async_preview_action("stop",v,{})
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(c,4)
        assert calls==[("switch","off")]
        assert c.control.idle_blocked and c.control.green_hold is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_disabled_economics_and_disabled_master_issue_no_idle_calls(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"economic_enabled":False}))
        await tick(c,3)
        assert calls==[]
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"economic_enabled":True}))
        await c.control.set_enabled(False)
        await tick(c,3)
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_restart_restores_own_green_pause_and_preserves_explicit_stop(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    restored=None
    try:
        await tick(c,2)
        stored=c._store_data()
        await c.async_shutdown()
        restored=PreviewCoordinator(hass,c.entry)
        restored.store.async_load=AsyncMock(return_value=stored)
        restored.store.async_save=AsyncMock()
        restored.store.async_delay_save=Mock()
        restored.async_request_refresh=AsyncMock()
        await restored.async_initialize()
        await tick(restored,3)
        assert calls==[("switch","off")]
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(restored,4)
        assert calls[-2:]==[("mode","Green"),("switch","on")]
    finally:
        if restored:
            await restored.async_shutdown()
        await close(hass,c)


@pytest.mark.asyncio
async def test_device_token_refresh_keeps_request_but_observed_fast_unplug_ends_it(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True}))
        await tick(c,3)
        before=deepcopy(c.sessions[v].as_dict())
        hass.states.async_set("sensor.connection","charger-restarted")
        await tick(c,2)
        assert not c.sessions[v].terminal and c.sessions[v].connection_aliases
        for key in ("generation","started_at","origin_meter","anchor_need","remaining_kwh"):
            assert getattr(c.sessions[v],key)==before[key]
        hass.states.async_set("binary_sensor.evse_plug","off")
        hass.states.async_set("binary_sensor.evse_plug","on")
        await hass.async_block_till_done()
        await tick(c)
        assert c.sessions[v].terminal and c.sessions[v].reason=="physical_charger_unplug_edge"
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_safe_capacity_and_actual_vehicle_limit_are_read_only(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        cfg={**c.entry.data,"power_kw":11,"safe_power_kw":5.5,"gateway_connectivity_entity":"binary_sensor.gateway"}
        hass.states.async_set("binary_sensor.gateway","on")
        assert planning_capacity(hass,cfg,{})["power_kw"]==11
        hass.states.async_set("binary_sensor.gateway","off")
        assert planning_capacity(hass,cfg,{})["power_kw"]==5.5
        hass.states.async_set("binary_sensor.gateway","on")
        hass.states.async_set("number.car_amps",8,{"unit_of_measurement":"A"})
        assert planning_capacity(hass,cfg,{"current_limit_entity":"number.car_amps"})["power_kw"]==5.5
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_missing_export_column_preserves_valid_import_prices(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        cfg={"price_entity":"sensor.price","export_price_key":"sell"}
        hass.states.async_set("sensor.price",.2,{"data":[{"time":DAY,"price":.2}]})
        rows,warnings=price_rows(hass,cfg,DAY)
        assert len(rows)==1 and rows[0].buy==.2
        assert "export_price_unknown_cost_estimate_incomplete" in warnings
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_a_new_start_uses_saved_reference_when_car_sensors_are_temporarily_missing(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        await c.async_control_stop(v)
        await tick(c,4)
        for name,unit in (("soc","%"),("energy","kWh"),("range","km")):
            hass.states.async_set("sensor.tesla_"+name,"unavailable",{"unit_of_measurement":unit})
        await c.async_preview_action("start",v,{"target_range_km":300,"departure":"now"})
        result=await tick(c,3)
        row=result["vehicles"][v]
        assert not c.sessions[v].terminal and row["calculation_from_saved_reference"]
        assert row["plan_provisional"] and not row["demand"]["confirmed"]
        assert c.sessions[v].anchor_need>0 and c.control.owned_manual
        assert c.control.fault is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_twenty_to_eighty_with_stale_vehicle_data_gets_full_allowance_not_a_30kwh_cutoff(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"maximum_command_kwh":100,"maximum_manual_hours":24}))
        for e,val,unit in (("sensor.tesla_soc",20,"%"),("sensor.tesla_energy",14,"kWh"),("sensor.tesla_range",80,"km")):
            hass.states.async_set(e,val,{"unit_of_measurement":unit})
        hass.states.async_set("sensor.car_capture","unavailable")
        await c.async_preview_action("start",v,{"target_range_km":320,"departure":"now"})
        await tick(c,3)
        assert c.sessions[v].anchor_need==pytest.approx(48.3)
        assert c.control.grant["max_kwh"]==100 and c.control.owned_manual and c.control.fault is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_gateway_restart_false_plug_does_not_retire_command_or_reset_energy(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True,"charger_connectivity_entity":"binary_sensor.gateway"}))
        hass.states.async_set("binary_sensor.gateway","on")
        await tick(c,3)
        before=deepcopy(c.sessions[v].as_dict())
        hass.states.async_set("binary_sensor.gateway","off")
        hass.states.async_set("binary_sensor.evse_plug","off")
        hass.states.async_set("sensor.connection","restart-token")
        await hass.async_block_till_done()
        await tick(c,3)
        assert not c.sessions[v].terminal
        assert c.control.status=="charger_gateway_recovery_waiting"
        hass.states.async_set("binary_sensor.evse_plug","on")
        hass.states.async_set("binary_sensor.gateway","on")
        hass.states.async_set("sensor.car_capture",datetime.now(timezone.utc).isoformat())
        await hass.async_block_till_done()
        await tick(c,3)
        assert not c.sessions[v].terminal and c.control.fault is None
        for key in ("generation","started_at","origin_meter","anchor_need"):
            assert getattr(c.sessions[v],key)==before[key]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_zonneplan_tax_is_subtracted_once_and_expiry_keeps_import_usable(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        config={"export_contract":"zonneplan","zonneplan_tax_eur_kwh":.10,"zonneplan_tax_valid_until":"2027-12-31"}
        rows,warnings=export_tariffs(hass,[Tariff(DAY,DAY+900,.30)],config,DAY)
        assert rows[0].buy==.30 and rows[0].sell==pytest.approx(.20) and not warnings
        rows,warnings=export_tariffs(hass,[Tariff(DAY,DAY+900,.30)],{**config,"zonneplan_tax_valid_until":"2026-12-31"},DAY)
        assert rows[0].buy==.30 and "export_contract_tax_expired" in warnings
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_zonnebonus_daylight_positive_base_and_counter_headroom(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        noon=DAY+12*3600
        hass.states.async_set("sensor.export_ytd",100,{"unit_of_measurement":"kWh","year":2027})
        object.__setattr__(hass.states.get("sensor.export_ytd"),"last_reported",datetime.fromtimestamp(noon,timezone.utc))
        cfg={"export_contract":"zonneplan","zonneplan_tax_eur_kwh":.1,"zonneplan_tax_valid_until":"2027-12-31","zonneplan_bonus_eligible":True,"bonus_export_ytd_entity":"sensor.export_ytd"}
        rows,warnings=export_tariffs(hass,[Tariff(noon,noon+900,.3),Tariff(noon+900,noon+1800,.05)],cfg,noon)
        assert rows[0].sell==pytest.approx(.22) and rows[1].sell==pytest.approx(-.05)
        assert not warnings
        hass.states.async_set("sensor.export_ytd",7500,{"unit_of_measurement":"kWh","year":2027})
        object.__setattr__(hass.states.get("sensor.export_ytd"),"last_reported",datetime.fromtimestamp(noon,timezone.utc))
        rows,_=export_tariffs(hass,[Tariff(noon,noon+900,.3)],cfg,noon)
        assert rows[0].sell==pytest.approx(.2)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_explicit_fallback_power_is_identified_and_missing_meter_preserves_allowance(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True,"fallback_power_entity":"sensor.evse_watts"}))
        await c.async_preview_action("start",v,{"departure":"now"})
        await tick(c,3)
        allowance=c.sessions[v].remaining_kwh
        hass.states.async_set("sensor.hw_power","unavailable",{"unit_of_measurement":"W"})
        hass.states.async_set("sensor.evse_watts",11000,{"unit_of_measurement":"W"})
        hass.states.async_set("sensor.iq_energy","unavailable",{"unit_of_measurement":"kWh"})
        result=await tick(c,2)
        assert result["physical"]["entity_id"]=="sensor.evse_watts" and result["physical"]["fallback_used"]
        assert not result["physical"]["independent"] and result["physical"]["positive"]
        assert c.sessions[v].remaining_kwh==allowance and not c.sessions[v].terminal
        assert result["vehicles"][v]["meter_credit_delayed"] and c.control.owned_manual
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_stable_cached_mode_allows_start_and_separates_readback_from_physical_proof(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True}))
        hass.states.async_set("sensor.ev_report","2020-01-01T00:00:00Z")
        await tick(c,3)
        assert calls==[("mode","Manual"),("switch","on")]
        assert c.control.fault is None and not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_externally_off_green_is_never_adopted_and_never_resumed(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        hass.states.async_set("switch.ev_charger","off")
        hass.states.async_set("sensor.hw_power",0,{"unit_of_measurement":"W"})
        await tick(c,3)
        assert c.control.green_hold is None and calls==[]
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(c,4)
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("event",["fault","token","unplug_replug"])
async def test_own_green_cleanup_survives_fault_or_changed_attachment(event,tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        await tick(c,2)
        assert c.control.green_hold["off_confirmed"]
        if event=="fault":
            c.control.fault="example_nonforeign_fault"
        elif event=="token":
            hass.states.async_set("sensor.connection","refreshed-device-token")
        else:
            hass.states.async_set("binary_sensor.evse_plug","off")
            await hass.async_block_till_done()
            await tick(c)
            assert c.control.green_hold and c.control.green_hold["release_required"]
            hass.states.async_set("sensor.connection","new-physical-attachment")
            hass.states.async_set("binary_sensor.evse_plug","on")
            await hass.async_block_till_done()
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(c,4)
        assert c.control.green_hold is None
        assert ("mode","Manual") not in calls
        assert calls[-2:]==[("mode","Green"),("switch","on")]
        assert c.control.pause_audit
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_user_stop_during_await_is_queued_and_never_loses_off_ownership(tmp_path):
    from homeassistant.core import Context
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        original=c.control._call
        async def interrupt(domain,service,entity_id,**values):
            if service=="turn_off" and c.control.green_hold:
                event=type("InputEvent",(),{"context":Context(user_id="user"),"data":{"domain":"switch","service":"turn_off","service_data":{"entity_id":entity_id}}})()
                c.control._service_seen(event)
                assert c.control.green_hold is not None  # Callback may latch, never delete authority.
            return await original(domain,service,entity_id,**values)
        with patch.object(c.control,"_call",side_effect=interrupt):
            await tick(c)
        await hass.async_block_till_done()
        hass.states.async_set("sensor.price","unavailable",attrs)
        await tick(c,4)
        assert not any(call==("switch","on") for call in calls)
        assert c.control.idle_blocked and c.control.pause_audit
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_unmetered_energy_is_bounded_separately_and_real_recovery_resumes_budget(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"maximum_manual_hours":24}))
        await tick(c,3)
        before=c.sessions[v].remaining_kwh
        checked=c.control.grant["cap_checked_at"]
        c.control._assess_caps({"meter_kwh":None},checked+12*3600)
        assert c.control.fault=="unmetered_energy_exposure_cap"
        assert c.sessions[v].remaining_kwh==before  # No fictitious delivered credit.
        c.sessions[v].consume_meter(101)
        c.control._assess_caps({"meter_kwh":101,"meter_observed_at":checked+12*3600+1},checked+12*3600+1)
        assert c.control.fault is None
        assert c.sessions[v].remaining_kwh==pytest.approx(before-1)
    finally:
        await close(hass,c)


def test_forward_only_feed_gets_disclosed_threshold_not_a_no_charge_rule():
    now=DAY+12*3600
    result=economic_plan(now,DAY+72*3600,11,0,days()[48:96])
    assert result.mean_basis=="known_forward_intervals_fallback"
    assert result.mean_import==pytest.approx(.3)
    assert any(w.cost is not None for w in result.windows)
    assert balance(result)==pytest.approx(11)


@pytest.mark.asyncio
async def test_first_new_fresh_lower_soc_reconciles_initial_provisional_budget_only_once(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,stale=True)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"maximum_command_kwh":100}))
        await tick(c,3)
        old=c.sessions[v].anchor_need
        for e,val,unit in (("sensor.tesla_soc",20,"%"),("sensor.tesla_energy",14,"kWh"),("sensor.tesla_range",80,"km")):
            hass.states.async_set(e,val,{"unit_of_measurement":unit})
        hass.states.async_set("sensor.car_capture",datetime.now(timezone.utc).isoformat())
        hass.states.async_set("sensor.iq_energy",100.5,{"unit_of_measurement":"kWh"})
        await tick(c)
        assert not c.sessions[v].anchor_provisional
        assert c.sessions[v].anchor_need>old and c.sessions[v].origin_meter==100
        anchor=c.sessions[v].anchor_meter
        hass.states.async_set("sensor.car_capture",datetime.now(timezone.utc).isoformat())
        await tick(c)
        assert c.sessions[v].anchor_meter==anchor
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_invalid_pause_restore_preserves_independent_limit_obligation(tmp_path):
    from custom_components.adaptive_charge_preview.control import ChargerControl
    hass,c,v,calls=await controlled(tmp_path)
    try:
        sub=c.entry.subentries[v]
        object.__setattr__(sub,"data",MappingProxyType({**sub.data,"allow_limit_raise":True,"maximum_soc":90}))
        await c.async_preview_action("set_target",v,{"target_range_km":350})
        await tick(c)
        assert c.control.limit is not None
        stored=c.control.as_dict()
        stored["green_hold"]={"stage":"invalid"}
        restored=ChargerControl(c)
        restored.restore(stored)
        assert not restored.enabled and restored.fault=="invalid_stored_green_pause"
        assert restored.grant==c.control.grant and restored.limit==c.control.limit
        assert restored.pause_audit["charger_may_be_off"]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_idle_pause_does_not_defer_a_terminal_requests_limit_restoration(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        sub=c.entry.subentries[v]
        object.__setattr__(sub,"data",MappingProxyType({**sub.data,"allow_limit_raise":True,"maximum_soc":90}))
        await c.async_preview_action("set_target",v,{"target_range_km":350})
        await tick(c)
        assert c.control.limit is not None and not c.control.owned_manual
        c.sessions[v].stop("ended_before_manual")
        now=datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"economic_enabled":True,"idle_green_price_filter":True,"price_entity":"sensor.price","export_price_key":"sell"}))
        hass.states.async_set("sensor.price",.2,{"data":[{"time":r.start,"price":.2,"sell":.4} for r in days(now,count=96)]})
        hass.states.async_set("switch.ev_charger","on")
        hass.states.async_set("sensor.hw_power",3000,{"unit_of_measurement":"W"})
        await tick(c,4)
        assert c.control.limit is None and c.control.limit_audit["restored"]
        assert ("limit",80.0) in calls
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_gateway_loss_alone_does_not_block_safe_manual_start(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True,"gateway_connectivity_entity":"binary_sensor.gateway","safe_power_kw":5.5}))
        hass.states.async_set("binary_sensor.gateway","off")
        result=await tick(c,3)
        assert result["vehicles"][v]["planning_power_kw"]==5.5
        assert calls==[("mode","Manual"),("switch","on")]
        assert not c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_charger_gap_with_new_token_preserves_request_but_waits_for_real_vehicle_evidence(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"charger_fault_recovery":True,"charger_connectivity_entity":"binary_sensor.charger_online"}))
        hass.states.async_set("binary_sensor.charger_online","on")
        await tick(c,3)
        before=c.sessions[v].as_dict()
        hass.states.async_set("binary_sensor.charger_online","off")
        hass.states.async_set("binary_sensor.evse_plug","unavailable")
        await hass.async_block_till_done()
        await tick(c)
        hass.states.async_set("sensor.connection","unknown-attachment-after-outage")
        hass.states.async_set("binary_sensor.evse_plug","on")
        hass.states.async_set("binary_sensor.charger_online","on")
        await hass.async_block_till_done()
        result=await tick(c)
        assert not c.sessions[v].terminal and c.sessions[v].connection_gap_since is not None
        assert c.sessions[v].connection_id==before["connection_id"]
        assert not result["vehicles"][v]["eligible_for_boost"]
        object.__setattr__(c.entry.subentries[v],"data",MappingProxyType({**c.entry.subentries[v].data,"home_entity":"binary_sensor.test_home"}))
        hass.states.async_set("binary_sensor.test_home","off")
        hass.states.async_set("sensor.car_capture",datetime.now(timezone.utc).isoformat())
        await tick(c)
        assert c.sessions[v].connection_gap_since is not None
        hass.states.async_set("binary_sensor.test_home","on")
        await tick(c,3)
        assert c.sessions[v].connection_id=="unknown-attachment-after-outage"
        assert c.sessions[v].generation==before["generation"] and c.sessions[v].origin_meter==before["origin_meter"]
    finally:
        await close(hass,c)


def test_current_quarter_reports_its_elapsed_economic_pause():
    from custom_components.adaptive_charge_preview.core.schedule import planning_attributes
    row={"session":{"terminal":False},"departure":DAY+1800,"plan":{
        "mode":"Green","windows":[],"pause_windows":[{"start":DAY+30,"end":DAY+60}],"green_windows":[]}}
    result=planning_attributes(row,DAY+120)
    assert result["quarters"][0]["economic_pause_minutes"]==.5
    assert result["quarters"][0]["autonomous_green_allowed"] is False


@pytest.mark.asyncio
async def test_negative_recovery_gap_cannot_restore(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        saved=c.sessions[v].as_dict()
        saved["connection_gap_since"]=-1
        with pytest.raises(ValueError):
            type(c.sessions[v]).restore(saved)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_limit_restore_timeout_does_not_fail_refresh_or_skip_green_debt(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    try:
        await tick(c,2)
        c.control.limit={"fixture":"pending"}
        with patch.object(c.control,"restore_limit",side_effect=TimeoutError):
            result=await tick(c)
        assert result and c.control.green_hold is not None
    finally:
        c.control.limit=None
        await close(hass,c)


@pytest.mark.asyncio
async def test_exposure_latches_at_cap_and_idle_grant_does_not_churn(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        c.control.grant["max_kwh"]=3
        now=datetime.now(timezone.utc).timestamp()
        c.control.grant["manual_since"]=now-600
        c.sessions[v].consume_meter(102)
        c.control._assess_caps({"meter_kwh":102,"meter_observed_at":now-600},now)
        assert c.control.fault=="unmetered_energy_exposure_cap"
        c.control.owned_manual=False
        c.control._assess_caps({"meter_kwh":102,"meter_observed_at":now-600},now+10)
        assert c.control.fault=="unmetered_energy_exposure_cap"
        c.control._assess_caps({"meter_kwh":102.01,"meter_observed_at":now+11},now+11)
        assert c.control.fault=="unmetered_energy_exposure_cap"  # Hysteresis, no restart oscillation.
        c.control.fault=None
        c.sessions[v].stop("fixture_done")
        before=c.control.as_dict()
        c.control._assess_caps({"meter_kwh":102.01,"meter_observed_at":now+11},now+1000)
        assert c.control.as_dict()==before
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_exposure_restore_reanchors_downtime_but_preserves_accumulated_bound(tmp_path):
    from custom_components.adaptive_charge_preview.control import ChargerControl
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        stored=c.control.as_dict()
        stored["grant"].update(cap_checked_at=1,unmetered_exposure_kwh=1.25)
        restored=ChargerControl(c)
        restored.restore(stored)
        assert restored.grant["cap_checked_at"]>1 and restored.grant["unmetered_exposure_kwh"]==1.25
        stored["grant"]["unmetered_exposure_kwh"]=-1
        invalid=ChargerControl(c)
        invalid.restore(stored)
        assert not invalid.enabled and invalid.fault=="invalid_stored_exposure_bound"
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_unknown_off_on_after_online_charger_reload_preserves_request(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        before=c.sessions[v].as_dict()
        hass.states.async_set("binary_sensor.evse_plug","unavailable")
        hass.states.async_set("binary_sensor.evse_plug","off")
        await hass.async_block_till_done()
        await tick(c)
        assert not c.sessions[v].terminal
        hass.states.async_set("binary_sensor.evse_plug","on")
        await hass.async_block_till_done()
        await tick(c)
        assert not c.sessions[v].terminal and c.sessions[v].generation==before["generation"]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_unknown_off_persists_on_online_charger_ends_request(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        hass.states.async_set("binary_sensor.evse_plug","unavailable")
        hass.states.async_set("binary_sensor.evse_plug","off")
        await hass.async_block_till_done()
        c._uncertain_unplug_at-=6
        await tick(c)
        assert c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_stop_during_own_green_pause_notifies_that_green_stays_off(tmp_path):
    hass,c,v,calls,attrs=await idle_setup(tmp_path)
    notices=[]
    async def notify(call):
        notices.append(call.data)
    hass.services.async_register("persistent_notification","create",notify)
    try:
        await tick(c,2)
        await c.async_preview_action("stop",v,{})
        await tick(c,2)
        assert notices and any("Green hervat" in n["message"] for n in notices)
        assert not any(a==("switch","on") for a in calls)
    finally:
        await close(hass,c)
