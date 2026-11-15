"""Regressions for the physical 5 A / 16 A Green-to-Manual feedback loop."""

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from unittest.mock import patch

import pytest

from custom_components.adaptive_charge_preview.sources import planning_capacity
from .test_control import close, controlled, tick, fast_feedback, live_prices


@pytest.mark.asyncio
@pytest.mark.parametrize("amps_sequence",[(5,16,5,16,5),("unavailable",16,"unavailable",5)])
async def test_green_current_changes_do_not_create_urgency_or_switch_the_charger(tmp_path,amps_sequence):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        now=datetime.now(timezone.utc)
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,
            "economic_enabled":True,"power_kw":11,"safe_power_kw":5.5,"price_entity":"sensor.price"}))
        profile=c.entry.subentries[v]
        object.__setattr__(profile,"data",MappingProxyType({**profile.data,"current_limit_entity":"number.car_amps"}))
        # Use the real Enphase public state vocabulary, not just the test device's label.
        hass.states.async_set("sensor.ev_feedback","GREEN_CHARGING",{
            "requested_mode":None,"preferred_mode":"GREEN_CHARGING","effective_mode":"GREEN_CHARGING"})
        assert c.entry.data["control_mode_entity"]=="select.ev_mode"
        assert c.entry.data["control_feedback_entity"]=="sensor.ev_feedback"
        midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
        hass.states.async_set("sensor.price",.4,{"data":[{
            "time":midnight+timedelta(minutes=15*i),
            "price":.4 if midnight+timedelta(minutes=15*i)<now+timedelta(hours=2) else .2}
            for i in range(192)]})
        await c.async_preview_action("set_target",v,{"target_range_km":300,"departure":(now+timedelta(hours=5)).isoformat()})
        await c._observe()  # Initialize the original meter allocation, without dispatch.
        binding=(c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)
        for amps in amps_sequence:
            hass.states.async_set("number.car_amps",amps,{"unit_of_measurement":"A"})
            row=(await tick(c))["vehicles"][v]
            assert row["planning_power_kw"]==row["planning_capacity"]["power_kw"]==11
            assert row["planning_capacity"]["capacity_source"]=="enphase_normal"
            assert row["planning_capacity"]["vehicle_current_used"] is False
            assert row["plan"]["mode"]=="Green"
            assert row["plan"]["reason"]!="departure_urgency"
            assert row["plan"]["windows"][0]["start"]>=now.timestamp()+90*60
            assert (c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)==binding
        assert calls==[]
    finally:
        await close(hass,c)


@pytest.mark.asyncio
@pytest.mark.parametrize("current",[5,8,16,"unknown","unavailable",None])
@pytest.mark.parametrize("gateway,expected",[("on",11),("off",5.5),("unavailable",5.5)])
async def test_only_enphase_capacity_is_used_even_with_an_old_vehicle_current_binding(tmp_path,current,gateway,expected):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        cfg={**c.entry.data,"power_kw":11,"safe_power_kw":5.5,
             "gateway_connectivity_entity":"binary_sensor.gateway"}
        hass.states.async_set("binary_sensor.gateway",gateway)
        if current is not None:
            hass.states.async_set("number.car_amps",current,{"unit_of_measurement":"A"})
        original=hass.states.get
        def read(entity):
            assert entity!="number.car_amps", "Vehicle pilot must not be read as future Manual capacity"
            return original(entity)
        with patch.object(type(hass.states),"get",side_effect=read):
            result=planning_capacity(hass,cfg,{"current_limit_entity":"number.car_amps"})
        assert result["power_kw"]==expected
        assert result["capacity_source"]==("enphase_normal" if gateway=="on" else "enphase_safe")
        assert result["vehicle_current_used"] is False
        assert not calls
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_safe_mode_and_explicit_profile_power_remain_independent_of_green_power(tmp_path):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        cfg={**c.entry.data,"power_kw":11,"safe_power_kw":5.5,"capacity_entity":"sensor.capacity"}
        hass.states.async_set("sensor.capacity","Normal")
        hass.states.async_set("sensor.hw_power",300,{"unit_of_measurement":"W"})
        assert planning_capacity(hass,cfg,{})["power_kw"]==11
        assert planning_capacity(hass,cfg,{"planning_power_kw":3.7})["power_kw"]==3.7
        hass.states.async_set("sensor.capacity","Safe mode")
        assert planning_capacity(hass,cfg,{})["power_kw"]==5.5
        hass.states.async_set("sensor.capacity","unavailable")
        assert planning_capacity(hass,cfg,{})["power_kw"]==5.5
        assert not calls
    finally:
        await close(hass,c)


def test_removed_vehicle_pilot_settings_are_not_offered_in_configuration():
    from custom_components.adaptive_charge_preview.config_flow import charger_schema,vehicle_schema
    assert "current_limit_entity" not in {str(k) for k in vehicle_schema().schema}
    assert "nominal_current_a" not in {str(k) for k in charger_schema().schema}


@pytest.mark.asyncio
async def test_real_executor_returns_to_green_once_and_does_not_reenter_from_pilot_changes(tmp_path,fast_feedback):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        now=datetime.now(timezone.utc)
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,
            "economic_enabled":True,"power_kw":11,"safe_power_kw":5.5,"price_entity":"sensor.price"}))
        profile=c.entry.subentries[v]
        object.__setattr__(profile,"data",MappingProxyType({**profile.data,"current_limit_entity":"number.car_amps"}))
        base=int(now.timestamp()//900)*900
        rows=[{"time":base+i*900,"price":.2} for i in range(96)]
        hass.states.async_set("sensor.price",.2,{"data":rows})
        hass.states.async_set("number.car_amps",5,{"unit_of_measurement":"A"})
        await c.async_preview_action("set_target",v,{"target_range_km":300,"departure":(now+timedelta(hours=5)).isoformat()})
        await tick(c,3)
        assert calls==[("mode","Manual"),("switch","on")]
        owner=(c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)
        # A normal price publication legitimately moves the requested energy later.
        rows=[{**r,"price":.4 if r["time"]<now.timestamp()+7200 else .2} for r in rows]
        hass.states.async_set("sensor.price",.4,{"data":rows})
        await tick(c,4)
        expected=[("mode","Manual"),("switch","on"),("switch","off"),("mode","Green")]
        assert calls==expected
        for amps in (5,16,5,"unavailable",16):
            hass.states.async_set("number.car_amps",amps,{"unit_of_measurement":"A"})
            row=(await tick(c))["vehicles"][v]
            assert row["plan"]["mode"]=="Green"
            assert not c.sessions[v].terminal
            assert owner==(c.sessions[v].generation,c.sessions[v].started_at,c.sessions[v].origin_meter)
        assert calls==expected
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_sustained_low_manual_power_warns_without_changing_capacity_plan_or_actuators(tmp_path,fast_feedback):
    hass,c,v,calls=await controlled(tmp_path)
    try:
        now=datetime.now(timezone.utc).timestamp()
        await c.async_preview_action("set_target",v,{"departure":datetime.fromtimestamp(now+7200,timezone.utc).isoformat()})
        attrs=live_prices(hass,c);attrs["data"][0]["end"]=now+10800
        hass.states.async_set("sensor.prices",.25,attrs)
        await tick(c,3)
        before=list(calls)
        owner=(c.sessions[v].generation,c.sessions[v].origin_meter)
        for elapsed in (0,60,121):
            stamp=now+elapsed
            hass.states.async_set("sensor.hw_power",3400,{"unit_of_measurement":"W"},timestamp=stamp)
            with patch("custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow",return_value=datetime.fromtimestamp(stamp,timezone.utc)):
                result=await tick(c)
            row=result["vehicles"][v]
            assert row["planning_power_kw"]==11
            assert row["plan"]["mode"]=="Manual"
            assert (c.sessions[v].generation,c.sessions[v].origin_meter)==owner
            assert calls==before
            assert ("delivered_power_below_planning_capacity" in row["warnings"])==(elapsed==121)
        assert row["delivery"]["state"]=="below_capacity"
        assert row["delivery"]["distinct_samples"]==3
        assert row["delivery"]["departure_lateness_seconds"]>0
        assert datetime.fromisoformat(row["delivery"]["continuous_finish_at_observed_rate"]).timestamp()>now+7200
        c.async_set_updated_data(result)
        from custom_components.adaptive_charge_preview.sensor import PreviewSensor
        assert PreviewSensor(c,v).extra_state_attributes["delivery"]==row["delivery"]
        hass.states.async_set("sensor.hw_power",11000,{"unit_of_measurement":"W"},timestamp=now+130)
        with patch("custom_components.adaptive_charge_preview.coordinator.dt_util.utcnow",return_value=datetime.fromtimestamp(now+130,timezone.utc)):
            row=(await tick(c))["vehicles"][v]
        assert row["delivery"]["state"]=="on_rate"
        assert "delivered_power_below_planning_capacity" not in row["warnings"]
        assert calls==before
    finally:
        await close(hass,c)


@pytest.mark.asyncio
async def test_old_pilot_binding_is_not_subscribed_after_loading_existing_configuration(tmp_path):
    from .test_ha import setup
    from unittest.mock import Mock
    hass,c,v=await setup(tmp_path)
    try:
        await c.async_shutdown()
        profile=c.entry.subentries[v]
        object.__setattr__(profile,"data",MappingProxyType({**profile.data,"current_limit_entity":"number.car_amps"}))
        object.__setattr__(c.entry,"data",MappingProxyType({**c.entry.data,"nominal_current_a":16}))
        with patch("custom_components.adaptive_charge_preview.coordinator.async_track_state_change_event",return_value=Mock()) as subscribe:
            await c.async_initialize()
        assert "number.car_amps" not in subscribe.call_args.args[1]
        assert "sensor.iq_energy" in subscribe.call_args.args[1]
        row=(await c._observe())["vehicles"][v]
        assert row["planning_capacity"]["capacity_source"]=="enphase_normal"
    finally:
        await close(hass,c)
