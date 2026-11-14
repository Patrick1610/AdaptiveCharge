"""Quarter projections conserve planned duration/energy and never imply a Start."""

from copy import deepcopy
from datetime import datetime
import json

import pytest

from custom_components.adaptive_charge_preview.core.schedule import planning_attributes
from .test_core import NOW


def row():
    return {'vehicle_id':'car','session':{'terminal':False},'target_km':250,'departure':NOW+3600,
            'planning_power_kw':5.5,'phase':'scheduled','control_enabled':True,
            'plan':{'mode':'Green','reason':'green_between_boost_windows','windows':[
                {'start':NOW,'end':NOW+900,'kwh':1.375},
                {'start':NOW+1800,'end':NOW+2700,'kwh':1.375}]}}


def test_quarters_include_zeroes_and_clip_elapsed_charging():
    result=planning_attributes(row(),NOW+450)
    assert result['remaining_minutes']==22.5
    assert result['scheduled_energy_kwh']==pytest.approx(2.0625)
    assert [q['minutes'] for q in result['quarters']]==[7.5,0,15,0]
    assert [q['charging'] for q in result['quarters']]==[True,False,True,False]
    assert sum(q['energy_kwh'] for q in result['quarters'])==pytest.approx(result['scheduled_energy_kwh'])
    assert sum(q['minutes'] for q in result['quarters'])==result['remaining_minutes']
    assert all(datetime.fromisoformat(q['start']).tzinfo is not None for q in result['quarters'])
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('session',[None,{}, {'terminal':True}])
def test_no_active_request_has_no_scheduled_quarters_even_with_advice(session):
    source=row(); source['session']=session
    result=planning_attributes(source,NOW)
    assert result['remaining_minutes']==0 and result['quarters']==[] and not result['active_request']


def test_direct_request_has_an_energy_bounded_duration():
    source=row(); source['plan']={'mode':'Manual','needed_kwh':2.75,'windows':[]}
    source['departure']=None
    result=planning_attributes(source,NOW)
    assert result['remaining_minutes']==30 and len(result['quarters'])==2
    assert result['scheduled_energy_kwh']==2.75
    assert source['plan']['windows']==[]  # Projection does not change the executor plan.


@pytest.mark.parametrize('windows',[None,[],[{}],[None],[{'start':NOW,'end':NOW,'kwh':1}],
                                    [{'start':NOW,'end':NOW+900,'kwh':float('nan')}],
                                    [{'start':NOW,'end':NOW+900,'kwh':1},{'start':NOW+300,'end':NOW+1000,'kwh':1}]])
def test_waiting_and_malformed_plans_do_not_crash_sensor(windows):
    source=row()
    if windows is None:
        source['plan']=None
    else:
        source['plan']['windows']=windows
    result=planning_attributes(source,NOW)
    assert result['remaining_minutes'] is None or result['remaining_minutes']==0
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('horizon',[3600,48*3600,72*3600])
def test_quarter_payload_is_bounded_to_supported_horizon(monkeypatch,horizon):
    import custom_components.adaptive_charge_preview.core.schedule as schedule
    monkeypatch.setattr(schedule,'MAX_HORIZON_SECONDS',horizon)
    source=row(); source['departure']=NOW+100*86400
    source['plan']['windows']=[{'start':NOW,'end':NOW+100*86400,'kwh':100}]
    result=planning_attributes(source,NOW+1)
    assert result['remaining_minutes']<=horizon/60
    assert sum(q['minutes'] for q in result['quarters'])==pytest.approx(result['remaining_minutes'])
    assert sum(q['energy_kwh'] for q in result['quarters'])==pytest.approx(result['scheduled_energy_kwh'])


def test_projection_is_read_only_and_ignores_unpriced_window_cost():
    source=row(); source['plan']['windows'][0]['cost']=None
    original=deepcopy(source)
    result=planning_attributes(source,NOW)
    assert source==original and result['energy_scope']=='scheduled_manual_wall_energy'
    assert all('cost' not in q for q in result['quarters'])


def test_wall_energy_basis_and_warning_copy_are_explicit():
    source=row(); source['warnings']=['example']
    source['plan']['windows'][0]['additional_kwh']=.5
    result=planning_attributes(source,NOW)
    assert result['quarters'][0]['energy_kwh']==1.375
    result['warnings'].append('changed')
    assert source['warnings']==['example']


@pytest.mark.parametrize('power',[None,0])
def test_manual_prediction_with_unknown_power_reports_unknown(power):
    source=row(); source['planning_power_kw']=power
    source['plan']={'mode':'Manual','needed_kwh':3,'windows':[]}
    result=planning_attributes(source,NOW)
    assert result['remaining_minutes'] is None and not result['quarters']


def test_unknown_projection_clock_does_not_report_zero():
    result=planning_attributes(row(),None)
    assert result['remaining_minutes'] is None and result['reason']=='planning_clock_unknown'


def test_user_visible_translation_values_have_normal_names():
    from pathlib import Path
    import re
    root=Path(__file__).resolve().parents[2]/'custom_components'/'adaptive_charge_preview'
    def strings(value):
        if isinstance(value,dict):
            return [s for child in value.values() for s in strings(child)]
        if isinstance(value,list):
            return [s for child in value for s in strings(child)]
        return [value] if isinstance(value,str) else []
    for path in [root/'strings.json',*(root/'translations').glob('*.json')]:
        data=json.loads(path.read_text())
        assert not any(re.search(r'\bpreview\b',s,re.I) for s in strings(data))
        assert data['entity']['sensor']['planning']['name']
