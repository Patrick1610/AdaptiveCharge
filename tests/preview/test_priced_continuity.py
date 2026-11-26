"""A running partial quarter must not move its start past the present."""

from datetime import datetime, timezone
from types import MappingProxyType
from copy import copy
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.adaptive_charge_preview.core.models import Tariff
from custom_components.adaptive_charge_preview.core.planner import plan
from .test_core import NOW
from .test_control import close, controlled, fast_feedback, live_prices, tick


@pytest.mark.parametrize('elapsed', [1, 60, 180, 240, 300])
@pytest.mark.parametrize('solar', [0,.5])
def test_meter_progress_cannot_postpone_an_already_running_partial_quarter(elapsed,solar):
    rows = [Tariff(NOW+i*900, NOW+(i+1)*900, price,solar_kw=solar) for i,price in enumerate([.4,.1,.8])]
    initial = 2+solar*2700/3600
    waiting = plan(NOW, NOW+2700, initial, 0, rows, power=5.5, reserve_min=0)
    start = waiting.windows[0].start
    assert waiting.mode == 'Green' and start > NOW
    now = start+elapsed
    remaining = initial-solar*(start-NOW)/3600-5.6*elapsed/3600
    continued = plan(now, NOW+2700, remaining, 0, rows, power=5.5, reserve_min=0, continue_current=True)
    assert continued.mode == 'Manual' and continued.windows[0].start == now
    assert len(continued.windows) == 1  # No timer can wake inside an artificial gap.
    extra = remaining-continued.expected_solar_kwh
    assert sum(w.additional_kwh for w in continued.windows) == pytest.approx(extra)
    head = (5.5-solar)*(NOW+900-now)/3600
    assert continued.cost == pytest.approx(head*.4+(extra-head)*.1)


def test_continuation_does_not_select_an_unneeded_expensive_current_quarter():
    rows = [Tariff(NOW+i*900, NOW+(i+1)*900, price) for i,price in enumerate([.4,.1,.8])]
    result = plan(NOW, NOW+2700, 1, 0, rows, power=5.5, reserve_min=0, continue_current=True)
    assert result.mode == 'Green' and result.windows[0].start == NOW+900
    assert sum(w.additional_kwh for w in result.windows) == pytest.approx(1)


@pytest.mark.parametrize('gap,expected',[(179,'Manual'),(180,'Green'),(181,'Green')])
def test_only_short_pause_is_bridged_and_reallocated_without_extra_energy(gap,expected):
    rows=[Tariff(NOW+i*900,NOW+(i+1)*900,p) for i,p in enumerate([.4,.1,.8])]
    now=NOW+900-gap
    result=plan(now,NOW+2700,1,0,rows,power=5.5,reserve_min=0,continue_current=True)
    assert result.mode==expected and len(result.windows)==1
    assert sum(w.additional_kwh for w in result.windows)==pytest.approx(1)
    if expected=='Manual':
        head=5.5*gap/3600
        assert result.windows[0].start==now
        assert result.cost==pytest.approx(.4*head+.1*(1-head))
    else:
        assert result.windows[0].start==NOW+900 and result.cost==pytest.approx(.1)


def test_bridge_cannot_spend_more_than_remaining_allowance():
    rows=[Tariff(NOW+i*900,NOW+(i+1)*900,p) for i,p in enumerate([.4,.1,.8])]
    result=plan(NOW+780,NOW+2700,.01,0,rows,power=5.5,reserve_min=0,continue_current=True)
    assert result.mode=='Green'
    assert sum(w.additional_kwh for w in result.windows)==pytest.approx(.01)


@pytest.mark.parametrize('remaining,band,reason', [(0,0,'energy_estimated_sufficient'),(.1,.8,'small_remainder_within_tolerance')])
def test_running_block_cannot_override_energy_or_tolerance_stop(remaining,band,reason):
    rows = [Tariff(NOW+i*900,NOW+(i+1)*900,.2) for i in range(3)]
    result = plan(NOW,NOW+2700,remaining,band,rows,power=5.5,reserve_min=0,continue_current=True)
    assert result.mode == 'Green' and result.reason == reason and not result.windows


@pytest.mark.asyncio
@pytest.mark.parametrize('blocker', ['disabled','stop','cleanup','fault','foreign_mode','foreign_controller','pending_stop','pending_green','replacement','attachment'])
async def test_continuation_never_extends_revoked_or_foreign_ownership(tmp_path,blocker):
    hass,c,v,_ = await controlled(tmp_path)
    try:
        await tick(c,3)
        request = c.sessions[v]
        assert c.control.continuing_manual(request)
        if blocker == 'disabled': c.control.enabled = False
        elif blocker == 'stop': await c.async_control_stop(v)
        elif blocker == 'cleanup': c.control._cleanup_required = True
        elif blocker == 'fault': c.control.fault = 'command_energy_or_manual_time_cap'
        elif blocker == 'foreign_mode': c.control._external_mode = True
        elif blocker == 'foreign_controller': hass.states.async_set('input_boolean.legacy_master','on')
        elif blocker.startswith('pending_'): c.control.pending = {'kind':blocker[8:]}
        elif blocker == 'replacement': request.generation += 1
        else: hass.states.async_set('sensor.connection','connection-b')
        assert not c.control.continuing_manual(request)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_continuation_accepts_transient_interlock_but_not_changed_binding_or_stale_object(tmp_path):
    hass,c,v,_ = await controlled(tmp_path)
    try:
        await tick(c,3)
        request=c.sessions[v]
        assert not c.control.continuing_manual(copy(request))
        with patch.object(c.control,'interlock',return_value='waiting_actuator_source'):
            assert c.control.continuing_manual(request)
        with patch.object(c.control,'interlock',return_value='control_binding_changed_requires_new_start'):
            assert not c.control.continuing_manual(request)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_soc', [False,True])
async def test_price_outage_redeadlines_priced_block_without_cancelling_or_new_budget(tmp_path,missing_soc):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        end=datetime.now(timezone.utc).timestamp()+10800
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end,timezone.utc).isoformat()})
        attrs=live_prices(hass,c); attrs['data'][0]['end']=end+3600
        hass.states.async_set('sensor.prices',.25,attrs)
        await tick(c,3)
        owner=(c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)
        hass.states.async_set('sensor.prices','unavailable',attrs)
        if missing_soc: hass.states.async_set('sensor.tesla_soc','unavailable',{'unit_of_measurement':'%'})
        result=await tick(c,4)
        assert result['vehicles'][v]['plan']['reason']=='unpriced_deadline_fallback'
        assert result['vehicles'][v]['would_control_mode']=='Green' and not c.control.owned_manual
        assert not c.sessions[v].terminal and calls[-2:]==[('switch','off'),('mode','Green')]
        assert result['vehicles'][v]['plan']['windows'][0]['start']==pytest.approx(end-c.sessions[v].remaining_kwh/11*3600)
        assert owner==(c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_soc',[False,True])
async def test_actual_timer_and_executor_cross_quarter_with_faster_meter_without_off(tmp_path,missing_soc):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        await tick(c,3)
        await hass.async_block_till_done()
        await tick(c)
        before=list(calls); request=c.sessions[v]
        base=(int(datetime.now(timezone.utc).timestamp()//900)+1)*900
        rows=[Tariff(base+i*900,base+(i+1)*900,p) for i,p in enumerate([.4,.1,.8])]
        waiting=plan(base,base+2700,2,0,rows,power=5.5,reserve_min=0)
        start=waiting.windows[0].start
        sub=c.entry.subentries[v]
        object.__setattr__(sub,'data',MappingProxyType({**sub.data,'planning_power_kw':5.5}))
        object.__setattr__(c.entry,'data',MappingProxyType({**c.entry.data,'price_entity':'sensor.prices','reserve_min':0}))
        hass.states.async_set('sensor.prices',.4,{'data':[{'time':r.start,'end':r.end,'price':r.buy} for r in rows]})
        c.values[v]['departure']=base+2700
        request.set_departure(base+2700)
        # Include the old published window end (the timer's former gap wake),
        # the quarter boundary itself and a tick in the next cheap quarter.
        old_end=base+900-300*(5.6/5.5-1)
        times=[start+1,start+60,start+180,start+300,old_end,base+900,base+960]
        timers=[]
        refreshed=[]
        async def refresh():
            result=await c._async_update_data()
            refreshed.append(result)
            return result
        no_refresh=c.async_request_refresh
        for now in times:
            fake=datetime.fromtimestamp(now,timezone.utc)
            with patch('custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow',return_value=fake), patch('custom_components.adaptive_charge_preview.control.async_track_point_in_utc_time',side_effect=lambda h,cb,t: timers.append((cb,t)) or (lambda:None)):
                hass.states.async_set('sensor.car_capture',fake.isoformat())
                hass.states.async_set('sensor.ev_report',fake.isoformat())
                hass.states.async_set('sensor.hw_power',5600,{'unit_of_measurement':'W'})
                remaining=2-5.6*(now-start)/3600
                hass.states.async_set('sensor.iq_energy',request.origin_meter+request.anchor_need-remaining,{'unit_of_measurement':'kWh'})
                if missing_soc: hass.states.async_set('sensor.tesla_soc','unavailable',{'unit_of_measurement':'%'})
                for source in hass.states.async_all(): source.last_reported=fake
                await hass.async_block_till_done()
                assert c.control.continuing_manual(request), (c.control.enabled,c.control.owned_manual,c.control.pending,c.control._stop_requested,c.control._cleanup_required,c.control.fault,c.control._external_mode,c.control.interlock(),c.control.foreign_reason(),c.control.owns(v),c.sessions[v] is request)
                if now==old_end:
                    c.async_request_refresh=AsyncMock(side_effect=refresh)
                    c.control._wake(fake)  # The old published boundary callback.
                    await hass.async_block_till_done()
                    result=refreshed[-1]
                    c.async_request_refresh=no_refresh
                else:
                    result=await tick(c)
                await hass.async_block_till_done()
                windows=result['vehicles'][v]['plan']['windows']
                assert len(windows)==1 and windows[0]['start']==pytest.approx(now)
                row=result['vehicles'][v]
                assert row['would_control_mode']=='Manual' and calls==before, str({'at':now-base,'phase':row['phase'],'reason':row['reason'],'plan':row['plan']['reason'],'status':c.control.status,'fault':c.control.fault,'interlock':c.control.interlock(),'pending':c.control.pending,'remaining':request.remaining_kwh,'same':c.control._same_attachment(),'foreign':c.control._external_mode,'calls':calls})
                assert timers[-1][1].timestamp()==pytest.approx(windows[0]['end'])
                assert not request.terminal and request.remaining_kwh==pytest.approx(remaining)
        # The actual published end is now the end of the entire allocation,
        # where an energy stop is appropriate rather than another short pause.
        now=timers[-1][1].timestamp()
        fake=datetime.fromtimestamp(now,timezone.utc)
        with patch('custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow',return_value=fake):
            hass.states.async_set('sensor.car_capture',fake.isoformat())
            hass.states.async_set('sensor.ev_report',fake.isoformat())
            remaining=2-5.6*(now-start)/3600
            assert remaining<=0
            hass.states.async_set('sensor.iq_energy',request.origin_meter+request.anchor_need-remaining,{'unit_of_measurement':'kWh'})
            for source in hass.states.async_all(): source.last_reported=fake
            await hass.async_block_till_done()
            c.async_request_refresh=AsyncMock(side_effect=refresh)
            c.control._wake(fake)
            await hass.async_block_till_done()
            c.async_request_refresh=no_refresh
            assert calls==before+[('switch','off')]
            assert request.remaining_kwh==pytest.approx(remaining) and request.remaining_kwh<=0
            assert request.phase=='verifying' and request.confirmed_soc is None
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_soc', [False, True])
async def test_real_executor_keeps_current_selected_quarter_through_meter_updates(tmp_path, missing_soc):
    hass,c,v,calls = await controlled(tmp_path)
    try:
        await tick(c,3)
        request = c.sessions[v]
        owner = (request.generation, request.started_at, request.origin_meter)
        before = list(calls)
        now = datetime.now(timezone.utc).timestamp()
        boundary = (int(now//900)+1)*900
        needed = 2.75+11*.5*(boundary-now)/3600
        object.__setattr__(c.entry,'data',MappingProxyType({**c.entry.data,'price_entity':'sensor.prices','reserve_min':0}))
        hass.states.async_set('sensor.prices',.4,{'data':[
            {'time':now-60,'end':boundary,'price':.4},
            {'time':boundary,'end':boundary+900,'price':.1},
            {'time':boundary+900,'end':boundary+1800,'price':.8},
        ]})
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(boundary+1800,timezone.utc).isoformat()})
        hass.states.async_set('sensor.iq_energy',request.origin_meter+request.anchor_need-needed,{'unit_of_measurement':'kWh'})
        if missing_soc:
            hass.states.async_set('sensor.tesla_soc','unavailable',{'unit_of_measurement':'%'})
        for increment in [0,.01,.02]:
            hass.states.async_set('sensor.iq_energy',request.origin_meter+request.anchor_need-needed+increment,{'unit_of_measurement':'kWh'})
            result = await tick(c,3)
            assert result['vehicles'][v]['would_control_mode'] == 'Manual'
            assert calls == before  # No OFF/Green/second On while the selected block runs.
            assert request.remaining_kwh == pytest.approx(needed-increment)
            assert (request.generation,request.started_at,request.origin_meter) == owner
        await c.async_control_stop(v)
        await tick(c,4)
        stopped = list(calls)
        await tick(c,3)
        assert calls == stopped and request.terminal
    finally:
        await close(hass,c)
