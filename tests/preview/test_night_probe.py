"""Finite commissioning uses ordinary integration inputs and yields to users."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from tools.ha_night_probe import own, run


class Clock:
    def __init__(self):
        self.now = 1800000000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HA:
    def __init__(self, clock, *, powered=True, foreign=False, uncertain=False):
        self.clock, self.powered, self.foreign, self.uncertain = clock, powered, foreign, uncertain
        self.actions, self.reads = [], 0
        self.root = {"integration_version":"5.2.0b1", "control_enabled":True,
                     "command_generation":4, "active_vehicle":None, "physical":{},"control_pending":None,
                     "owned_manual":False,"limit_cleanup_pending":False,"economic_pause":None,"control_status":"green_confirmed"}
        self.row = {"vehicle_id":"T", "observations":{"connected":True,"soc":{"value":49}},
                    "demand":{"full_range_km":450}, "maximum_soc":90,"session":{"terminal":True}, "phase":"idle"}
        self.changed = datetime.fromtimestamp(clock.time()-100,timezone.utc).isoformat()
        self.absent_until = 0
        self.restart_setup_seconds = 0

    def request(self, path, data=None, method=None):
        if path == "states":
            self.reads += 1
            if self.clock.time()<self.absent_until:
                return []
            if self.foreign and self.reads == 7:
                self.row["session"]["target_km"] = 999
            self.root["physical"] = {"fresh":True,"kw":11 if self.powered and self.root["active_vehicle"] else 0,"independent":False}
            self.root["meter_kwh"] = 100+(self.clock.time()-1800000000)/3600*11
            return deepcopy([{"entity_id":"sensor.root","attributes":self.root,"last_changed":self.changed},
                             {"entity_id":"sensor.vehicle","attributes":self.row},
                             {"entity_id":"sensor.plan","attributes":{}}])
        if path=="services":
            return [{"domain":"adaptive_charge_preview","services":{a:{"fields":{k:{} for k in
                ({"vehicle_id","entry_id"}|({"target_range_km","departure"} if a!="stop" else set()))}}
                for a in ("start","set_target","stop")}}]
        self.actions.append((path,deepcopy(data)))
        action = path.rsplit("/",1)[-1]
        if action == "restart":
            self.changed = datetime.fromtimestamp(self.clock.time(),timezone.utc).isoformat()
            self.absent_until=self.clock.time()+self.restart_setup_seconds
        elif action == "start":
            if self.uncertain:
                raise TimeoutError("Uncertain service return")
            self.row["session"] = {"vehicle_id":"T","generation":5,"started_at":self.clock.time(),"origin_meter":100,"terminal":False}
            self.root.update(active_vehicle="T",command_generation=5)
            self.row["phase"] = "charging"
        elif action == "stop":
            self.row["session"]["terminal"] = True
            self.root["active_vehicle"] = None
        if action in {"start","set_target"}:
            self.row["session"].update(target_km=data["target_range_km"],departure=datetime.fromisoformat(data["departure"]).timestamp())
        return []


def settings(clock):
    return {"end_utc":clock.time()+3600,"version":"5.2.0b1","final_soc":85,"authorised_maximum_soc":90,"post_stop_seconds":600,
            "entry_id":"E","vehicle_id":"T","root_entity":"sensor.root",
            "vehicle_entity":"sensor.vehicle","planning_entity":"sensor.plan","evidence_entities":[]}


def test_ownership_requires_all_request_keys_and_nonterminal():
    binding={"vehicle_id":"T","generation":5,"started_at":10,"origin_meter":100}
    assert own(binding,binding)
    for key in binding:
        assert not own({**binding,key:None},binding)
    assert not own({**binding,"terminal":True},binding)


def test_probe_only_uses_public_inputs_once_and_preserves_request(tmp_path):
    clock=Clock()
    h=HA(clock)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="finished_requires_history_assessment"
    assert [p.rsplit("/",1)[-1] for p,_ in h.actions]==["start","restart","set_target","set_target","set_target","stop"]
    assert all(p.startswith("services/adaptive_charge_preview/") or p=="services/homeassistant/restart" for p,_ in h.actions)
    power=next(o for o in result["observations"] if o["kind"]=="same_request_after_restart_with_power_readback")
    assert power["power_provenance"]["independent"] is False
    assert power["independent_physical_confirmation"] is False
    assert result["binding"]["generation"]==5
    for line in (tmp_path/"samples.jsonl").read_text().splitlines():
        assert isinstance(json.loads(line)["states"],dict)
    with pytest.raises(FileExistsError):
        run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)


def test_uncertain_start_is_never_retried_and_no_blind_stop(tmp_path):
    clock=Clock()
    h=HA(clock,uncertain=True)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="blocked_no_retry"
    assert len(h.actions)==1 and result["actions"][0]["phase"]=="intent"


def test_changed_user_target_relinquishes_without_stop(tmp_path):
    clock=Clock()
    h=HA(clock,foreign=True)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="relinquished_changed_target_or_departure"
    assert len(h.actions)==1


def test_no_power_does_not_force_charger_or_restart(tmp_path):
    clock=Clock()
    h=HA(clock,powered=False)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert [p.rsplit("/",1)[-1] for p,_ in h.actions]==["start","set_target","stop"]
    assert any(o["kind"]=="short_sequence_not_physically_observed" for o in result["observations"])


def test_existing_request_blocks_commissioning_without_actions(tmp_path):
    clock=Clock()
    h=HA(clock)
    h.root["active_vehicle"]="T"
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="blocked_no_retry" and not h.actions


def test_missing_entities_during_own_restart_waits_for_setup_without_a_second_start(tmp_path):
    clock=Clock()
    h=HA(clock)
    h.restart_setup_seconds=180
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="finished_requires_history_assessment"
    assert sum(p.endswith('/start') for p,_ in h.actions)==1
    assert any(o["kind"]=="same_request_after_restart_with_power_readback" for o in result["observations"])


def test_float_km_rounding_never_pushes_90_target_over_90(tmp_path):
    from math import ceil
    clock=Clock()
    h=HA(clock,powered=False)
    h.row["demand"]["full_range_km"]=333.33
    cfg={**settings(clock),"final_soc":90}
    result=run(h,cfg,tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="finished_requires_history_assessment"
    data=next(d for p,d in h.actions if p.endswith('/set_target'))
    assert ceil(100*data['target_range_km']/333.33)<=90


def test_configured_over_limit_blocks_before_start(tmp_path):
    clock=Clock()
    h=HA(clock)
    h.row["maximum_soc"]=100
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result["phase"]=="blocked_no_retry" and not h.actions


def test_cleared_departure_is_a_user_change_not_a_math_error():
    from tools.ha_night_probe import same_target
    assert not same_target({"target_km":200,"departure":None},{"target_km":200,"departure":10})


@pytest.mark.asyncio
async def test_probe_contract_matches_real_ha_registration_and_real_entities(tmp_path):
    from types import MappingProxyType
    from custom_components.adaptive_charge_preview import _register_actions
    from custom_components.adaptive_charge_preview.sensor import PreviewSensor
    from tools.ha_night_probe import contract
    from .test_control import controlled,tick,close
    hass,c,v,calls=await controlled(tmp_path)
    try:
        sub=c.entry.subentries[v]
        object.__setattr__(sub,'data',MappingProxyType({**sub.data,'maximum_soc':90}))
        result=await tick(c,3)
        c.async_set_updated_data(result)
        _register_actions(hass)
        services=hass.services.async_services()['adaptive_charge_preview']
        descriptions=[{'domain':'adaptive_charge_preview','services':{name:{'fields':{str(k):{} for k in s.schema.schema}} for name,s in services.items()}}]
        record={'root':PreviewSensor(c).extra_state_attributes,'row':PreviewSensor(c,v).extra_state_attributes}
        contract(record,descriptions,settings(Clock()))
        assert record['root']['integration_version']=='5.2.0b1'
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_real_api_service_descriptions_include_all_probe_fields(tmp_path):
    from homeassistant.helpers.service import async_get_all_descriptions
    from custom_components.adaptive_charge_preview.const import DOMAIN
    from .test_lifecycle import make_hass
    from .test_ha import entry
    hass=await make_hass(tmp_path)
    e=entry()
    try:
        await hass.config_entries.async_add(e)
        await hass.async_block_till_done()
        descriptions=await async_get_all_descriptions(hass)
        # This is the services.yaml-backed provider used by /api/services.
        for action in ('start','set_target','stop'):
            expected={'vehicle_id','entry_id'}|({'target_range_km','departure'} if action!='stop' else set())
            assert expected<=descriptions[DOMAIN][action]['fields'].keys()
    finally:
        await hass.config_entries.async_unload(e.entry_id)
        await hass.async_stop()


def test_zero_post_stop_allowance_is_rejected_before_any_action(tmp_path):
    clock=Clock()
    h=HA(clock)
    with pytest.raises(ValueError):
        run(h,{**settings(clock),'post_stop_seconds':0},tmp_path,clock=clock.time,sleep=clock.sleep)
    assert not h.actions


def test_transient_entity_absence_after_stop_preserves_the_one_stop_intent(tmp_path):
    clock=Clock()
    class PostStopHA(HA):
        def request(self,path,data=None,method=None):
            result=super().request(path,data,method)
            if path.endswith('/stop'):
                self.absent_until=self.clock.time()+90
            return result
    h=PostStopHA(clock,powered=False)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result['phase']=='finished_requires_history_assessment'
    assert sum(p.endswith('/stop') for p,_ in h.actions)==1
    assert result['last_read_error']=='LookupError'


def test_restart_grace_expiry_never_sends_a_second_start(tmp_path):
    clock=Clock()
    h=HA(clock)
    h.restart_setup_seconds=900
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result['phase']=='blocked_no_retry'
    assert [p.rsplit('/',1)[-1] for p,_ in h.actions]==['start','restart']


def test_user_raised_out_of_band_target_relinquishes(tmp_path):
    clock=Clock()
    class RaisedHA(HA):
        def request(self,path,data=None,method=None):
            if path=='states' and self.reads==6:
                self.row['session']['target_km']=999
                self.row['demand']['requested_soc']=99
            return super().request(path,data,method)
    h=RaisedHA(clock)
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result['phase']=='relinquished_changed_target_or_departure'
    assert len(h.actions)==1


def test_fractional_start_soc_has_integral_bounded_intermediate_targets(tmp_path):
    from math import ceil
    clock=Clock()
    h=HA(clock)
    h.row['observations']['soc']['value']=49.5
    result=run(h,settings(clock),tmp_path,clock=clock.time,sleep=clock.sleep)
    assert result['phase']=='finished_requires_history_assessment'
    targets=[ceil(100*d['target_range_km']/450) for p,d in h.actions if p.endswith(('/start','/set_target'))]
    assert targets==[53,55,52,85]
