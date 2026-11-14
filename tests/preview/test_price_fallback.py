"""Real planner/executor regressions for deadline-first tariff outages."""

from datetime import datetime, timezone
from copy import deepcopy
from types import MappingProxyType
from unittest.mock import Mock, patch

import pytest

from custom_components.adaptive_charge_preview.core.models import Tariff
from custom_components.adaptive_charge_preview.core.planner import _Slot, _practical, deadline_fallback, plan
from .test_core import NOW, prices
from .test_control import close, controlled, fast_feedback, live_prices, tick


def test_three_hours_to_0615_starts_0315_without_the_price_reserve():
    result = plan(0, 6.25 * 3600, 16.5, 0, [], power=5.5, reserve_min=15)
    assert len(result.windows) == 1 and result.mode == 'Green'
    assert result.windows[0].start == 3.25 * 3600
    assert result.windows[0].end == 6.25 * 3600
    assert result.windows[0].kwh == pytest.approx(16.5)
    assert result.cost is None and result.windows[0].cost is None


@pytest.mark.parametrize('rows', [[], prices(count=2), [Tariff(NOW, NOW+900, float('nan'))],
                                  [Tariff(NOW+900, NOW+7200, .2)],
                                  [Tariff(NOW, NOW+3600, .2), Tariff(NOW+900, NOW+7200, .3)]])
def test_missing_partial_invalid_and_overlapping_prices_all_keep_one_block(rows):
    result = plan(NOW, NOW+7200, 5.5, 0, rows, power=5.5)
    assert len(result.windows) == 1 and result.windows[0].start == NOW+3600
    assert result.windows[0].end == NOW+7200
    assert 'unpriced_deadline_fallback' in result.warnings
    assert result.expected_solar_kwh == 0 and result.cost is None


def test_unpriced_boundary_and_shortfall_still_start_without_prices():
    assert deadline_fallback(NOW+3600, NOW+7200, 5.5, 5.5).mode == 'Manual'
    result = deadline_fallback(NOW, NOW+600, 5.5, 5.5)
    assert result.mode == 'Manual' and result.windows[0].end == NOW+600
    assert result.windows[0].kwh == pytest.approx(5.5/6)
    assert 'departure_energy_shortfall' in result.warnings
    assert result.shortfall_kwh == pytest.approx(5.5*5/6)


@pytest.mark.parametrize('needed', [10, 12])
def test_known_prices_keep_known_urgent_cost_when_reserve_capacity_is_short(needed):
    result=plan(NOW,NOW+3600,needed,0,prices(count=6),power=11,reserve_min=15)
    assert result.reason=='departure_urgency' and result.mode=='Manual'
    assert result.cost==pytest.approx(min(11,needed)*.2)
    assert sum(w.additional_kwh for w in result.windows)+result.shortfall_kwh==pytest.approx(needed)
    assert result.shortfall_kwh==pytest.approx(max(0,needed-11))
    assert 'unpriced_deadline_fallback' not in result.warnings


def test_urgency_with_only_reserve_prices_missing_uses_honest_fallback():
    result=plan(NOW,NOW+3600,10,0,prices(count=3),power=11,reserve_min=15)
    assert result.reason=='unpriced_deadline_fallback'
    assert result.windows[0].end==NOW+3600 and result.cost is None
    assert 'price_coverage_partial' in result.warnings


def test_started_fallback_does_not_create_tiny_gaps_when_meter_runs_ahead():
    # Faster-than-configured charging reduces remaining energy before the
    # deadline. This must not split an already started contiguous block.
    waiting = plan(NOW, NOW+3600, 5, 0, [], power=5.5)
    started = plan(NOW, NOW+3600, 5, 0, [], power=5.5, fallback_started=True)
    assert waiting.mode == 'Green' and started.mode == 'Manual'
    assert started.windows[0].start == NOW
    assert started.windows[0].kwh == pytest.approx(5)


def test_price_return_replaces_fallback_even_after_that_block_started():
    result = plan(NOW, NOW+10800, 1, 0, prices(buys=[.6,.1]+[.8]*10), power=5.5, fallback_started=True)
    assert result.mode == 'Green' and result.windows[0].start == NOW+900
    assert result.cost == pytest.approx(.1) and 'unpriced_deadline_fallback' not in result.warnings


@pytest.mark.parametrize('seconds', [1, 45, 179.9])
def test_short_tail_uses_cheapest_partial_neighbor_and_preserves_energy(seconds):
    slots = [_Slot(0,900,.3,0,0,11,300), _Slot(900,1800,.4,0,0,11),
             _Slot(1800,2700,.1,0,0,11,seconds)]
    windows, omitted, warnings = _practical(slots,11,180,0)
    assert len(windows)==1 and windows[0].start==0
    assert windows[0].end==pytest.approx(300+seconds)
    assert windows[0].kwh==pytest.approx(11*(300+seconds)/3600)
    assert windows[0].cost==pytest.approx(.3*11*(300+seconds)/3600)
    assert omitted==0 and not warnings


def test_short_tail_chooses_cheapest_adjacent_quarter_not_closest():
    slots = [_Slot(i*900,(i+1)*900,p,0,0,11,900 if i==2 else 45 if i==6 else 0)
             for i,p in enumerate([.9,.3,.1,.2,.9,.9,.01])]
    windows, omitted, _ = _practical(slots,11,180,0)
    assert len(windows)==1 and windows[0].start==1800
    assert windows[0].end==2745 and omitted==0


def test_partial_head_does_not_move_to_a_disconnected_qualifying_tail():
    slots=[_Slot(0,900,.1,0,0,11,300),_Slot(900,1800,.2,0,0,11,900),
           _Slot(1800,2700,.9,0,0,11),_Slot(2700,3600,.01,0,0,11,45)]
    windows,omitted,_=_practical(slots,11,180,0)
    assert [(w.start,w.end) for w in windows]==[(0,345),(900,1800)]
    assert slots[0].chosen_start==0 and omitted==0


@pytest.mark.asyncio
async def test_fallback_has_exact_timer_and_needs_no_new_start_to_become_due(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        now=datetime.now(timezone.utc).timestamp()
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(now+10800,timezone.utc).isoformat()})
        owner=(c.sessions[v].generation,c.sessions[v].started_at)
        timers=[]
        def capture(hass,callback,when):
            timers.append((callback,when))
            return Mock()
        with patch('custom_components.adaptive_charge_preview.control.async_track_point_in_utc_time',side_effect=capture):
            result=await tick(c)
            window=result['vehicles'][v]['plan']['windows'][0]
            assert timers[-1][1].timestamp()==pytest.approx(window['start']) and calls==[]
            assert window['end']==pytest.approx(now+10800)
            await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(now+10,timezone.utc).isoformat()})
            await tick(c,3)
            assert calls==[('mode','Manual'),('switch','on')]
            assert (c.sessions[v].generation,c.sessions[v].started_at)==owner
            await c.async_control_stop(v)
            await tick(c,4)
            before=list(calls)
            timers[0][0](datetime.now(timezone.utc))
            await tick(c,3)
            assert calls==before and c.sessions[v].terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_started_fallback_keeps_same_allocation_and_replans_on_price_return(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        await c._observe()
        now=datetime.now(timezone.utc).timestamp()
        needed=c.sessions[v].remaining_kwh
        end=now+needed/11*3600-1
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end,timezone.utc).isoformat()})
        await tick(c,3)
        assert c.control.owned_manual and c.control.unpriced_started(c.sessions[v])
        origin=c.sessions[v].origin_meter
        before=list(calls)
        hass.states.async_set('sensor.iq_energy',origin+needed-1,{'unit_of_measurement':'kWh'})
        result=await tick(c,3)
        assert calls==before and result['vehicles'][v]['would_control_mode']=='Manual'
        # A one-second helper round-trip cannot break the already running block.
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end+1,timezone.utc).isoformat()})
        await tick(c,3)
        assert calls==before and c.control.unpriced_started(c.sessions[v])
        attrs=live_prices(hass,c)
        attrs['data']=[{'time':now-60,'end':now+900,'price':.9}, {'time':now+900,'end':now+1800,'price':.1}, {'time':now+1800,'end':end+3600,'price':.8}]
        hass.states.async_set('sensor.prices',.9,attrs)
        await tick(c,4)
        assert calls[-2:]==[('switch','off'),('mode','Green')]
        assert not c.control.unpriced_started(c.sessions[v])
        assert not c.sessions[v].terminal and c.sessions[v].origin_meter==origin
        assert c.sessions[v].remaining_kwh==pytest.approx(1)
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_material_departure_change_redeadlines_without_new_allowance(tmp_path):
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        await c._observe()
        now=datetime.now(timezone.utc).timestamp()
        request=c.sessions[v]
        end=now+request.remaining_kwh/11*3600-1
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end,timezone.utc).isoformat()})
        await tick(c,3)
        owner=(request.generation,request.started_at,request.origin_meter)
        hass.states.async_set('sensor.iq_energy',request.origin_meter+request.remaining_kwh-1,{'unit_of_measurement':'kWh'})
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end+180,timezone.utc).isoformat()})
        await tick(c,4)
        assert calls[-2:]==[('switch','off'),('mode','Green')]
        assert (request.generation,request.started_at,request.origin_meter)==owner
        assert request.remaining_kwh==pytest.approx(1) and not request.terminal
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_fallback_publishes_unknown_window_cost_in_real_sensor_and_diagnostics(tmp_path):
    from custom_components.adaptive_charge_preview.diagnostics import async_get_config_entry_diagnostics
    from homeassistant.helpers import entity_registry as er
    from .test_lifecycle import make_hass
    from .test_ha import entry
    hass=await make_hass(tmp_path)
    e=entry()
    try:
        end=datetime.now(timezone.utc).timestamp()+10800
        for name,value,attrs in [
            ('sensor.tesla_soc',50,{'unit_of_measurement':'%'}),('sensor.tesla_energy',35,{'unit_of_measurement':'kWh'}),
            ('sensor.tesla_range',200,{'unit_of_measurement':'km'}),('number.tesla_limit',80,{'unit_of_measurement':'%'}),
            ('sensor.car_capture',datetime.now(timezone.utc).isoformat(),{}),('binary_sensor.tesla_cable','on',{}),
            ('binary_sensor.evse_plug','on',{}),('sensor.connection','connection-a',{}),('input_number.live_goal',250,{}),
            ('input_datetime.live_departure',datetime.fromtimestamp(end,timezone.utc).isoformat(),{}),
            ('sensor.iq_energy',100,{'unit_of_measurement':'kWh'}),('sensor.hw_power',0,{'unit_of_measurement':'W'})]:
            hass.states.async_set(name,value,attrs)
        with patch.object(type(hass.services),'async_call',side_effect=AssertionError('Read-only setup actuated')):
            await hass.config_entries.async_add(e)
            await hass.async_block_till_done()
        v=next(iter(e.subentries))
        with patch.object(type(hass.services),'async_call',side_effect=AssertionError('Disabled control actuated')):
            await e.runtime_data.async_preview_action('start',v,{'target_range_km':250,'departure':datetime.fromtimestamp(end,timezone.utc).isoformat()})
            await e.runtime_data.async_refresh()
            await hass.async_block_till_done()
        registered=er.async_entries_for_config_entry(er.async_get(hass),e.entry_id)
        sensor=next(item for item in registered if item.domain=='sensor' and item.config_subentry_id==v and item.unique_id.endswith('_preview'))
        published=hass.states.get(sensor.entity_id)
        assert published is not None and published.attributes['plan']['windows'][0]['cost'] is None
        diagnostics=await async_get_config_entry_diagnostics(hass,e)
        assert diagnostics['preview']['vehicles'][v]['plan']['windows'][0]['cost'] is None
        planning=next(item for item in registered if item.unique_id.endswith('_planning'))
        published_planning=hass.states.get(planning.entity_id)
        assert float(published_planning.state)>0 and published_planning.attributes['quarters']
        assert sum(q['minutes'] for q in published_planning.attributes['quarters'])==pytest.approx(float(published_planning.state))
        original=e.runtime_data.data
        e.runtime_data.async_set_updated_data({**original,'updated_at':'invalid'})
        await hass.async_block_till_done()
        assert hass.states.get(planning.entity_id).state=='unknown'
        assert hass.states.get(planning.entity_id).attributes['reason']=='planning_clock_unknown'
        e.runtime_data.async_set_updated_data({**original,'vehicles':{**original['vehicles'],v:{**original['vehicles'][v],'plan':None}}})
        await hass.async_block_till_done()
        assert hass.states.get(planning.entity_id).state=='unknown'
        await e.runtime_data.async_control_stop(v)
        await e.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get(planning.entity_id).state)==0
        assert hass.states.get(planning.entity_id).attributes['quarters']==[]
        assert await hass.config_entries.async_unload(e.entry_id)
    finally:
        await hass.async_stop()


@pytest.mark.asyncio
async def test_fallback_restore_validates_hint_and_preserves_active_budget(tmp_path):
    from unittest.mock import AsyncMock
    from custom_components.adaptive_charge_preview.coordinator import PreviewCoordinator
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    restored=None
    try:
        end=datetime.now(timezone.utc).timestamp()+600
        await c.async_preview_action('set_target',v,{'departure':datetime.fromtimestamp(end,timezone.utc).isoformat()})
        await tick(c,3)
        state=c._store_data()
        assert state['control']['grant']['unpriced_departure']==pytest.approx(end)
        await c.async_shutdown()
        restored=PreviewCoordinator(hass,c.entry)
        restored.store.async_load=AsyncMock(return_value=state)
        restored.store.async_save=AsyncMock()
        restored.store.async_delay_save=Mock()
        restored.async_request_refresh=AsyncMock()
        await restored.async_initialize()
        await tick(restored,3)
        assert restored.control.unpriced_started(restored.sessions[v])
        assert restored.sessions[v].remaining_kwh==pytest.approx(state['sessions'][v]['remaining_kwh'])
        assert restored.control.fault is None and calls.count(('mode','Manual'))==2
        bad=deepcopy(state['control'])
        bad['grant']['unpriced_departure']={'bad':'hint'}
        restored.control.restore(bad)
        assert 'unpriced_departure' not in restored.control.grant and restored.control.fault is None
        text=deepcopy(state['control'])
        text['grant']['unpriced_departure']=datetime.fromtimestamp(end,timezone.utc).isoformat()
        restored.control.restore(text)
        assert isinstance(restored.control.grant['unpriced_departure'],float)
        assert restored.control.unpriced_started(restored.sessions[v])
        text['grant']['unpriced_departure']=str(end)
        restored.control.restore(text)
        assert isinstance(restored.control.grant['unpriced_departure'],float)
        assert restored.control.unpriced_started(restored.sessions[v])
        restored.control.grant['unpriced_departure']={'bad':'hint'}
        assert not restored.control.unpriced_started(restored.sessions[v])
        restored.control.restore(text)
        await tick(restored,2)
        assert restored.control.fault is None
    finally:
        if restored:
            await restored.async_shutdown()
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize('blocker',['energy_cap','time_cap','foreign_master','invalid_power','terminal','invalid_remaining'])
async def test_fallback_keeps_existing_caps_and_authority_guards(tmp_path,blocker):
    from custom_components.adaptive_charge_preview.control import MAX_MANUAL_SECONDS
    hass,c,v,calls=await controlled(tmp_path,require_prices=True)
    try:
        await tick(c,3)
        before=list(calls)
        if blocker=='energy_cap':
            c.control.grant['max_kwh']=.1
            hass.states.async_set('sensor.iq_energy',c.sessions[v].origin_meter+.2,{'unit_of_measurement':'kWh'})
        elif blocker=='time_cap':
            c.control.grant['manual_seconds']=MAX_MANUAL_SECONDS+1
        elif blocker=='foreign_master':
            hass.states.async_set('input_boolean.legacy_master','on')
        elif blocker=='invalid_power':
            object.__setattr__(c.entry,'data',MappingProxyType({**c.entry.data,'power_kw':float('nan')}))
            assert not c.control._unpriced_dispatch_due(datetime.now(timezone.utc).timestamp())
        elif blocker=='terminal':
            await c.async_control_stop(v)
            assert not c.control._unpriced_dispatch_due(datetime.now(timezone.utc).timestamp())
        else:
            c.sessions[v].remaining_kwh=float('nan')
            assert not c.control._unpriced_dispatch_due(datetime.now(timezone.utc).timestamp())
            c.sessions[v].remaining_kwh=1  # Do not persist an intentionally corrupted test value.
        await tick(c,4)
        assert not any(call in [('mode','Manual'),('switch','on')] for call in calls[len(before):])
        if blocker in {'energy_cap','time_cap'}:
            assert c.control.fault=='command_energy_or_manual_time_cap'
    finally:
        await close(hass,c)
