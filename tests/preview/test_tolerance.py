"""A tolerated initial remainder must still allow later real SOC droop refill."""

import pytest

from custom_components.adaptive_charge_preview.core.models import VehicleConfig, demand
from custom_components.adaptive_charge_preview.core.session import Session
from .test_core import NOW,snapshot


def test_initial_tolerance_rearms_after_real_droop_and_dwell():
    c=VehicleConfig("t","Tesla","tesla")
    s=snapshot(soc=74.8,energy=52.36,range_km=299.2)
    request=Session("t",1,"connection-a",NOW,300,None)
    d=demand(c,s,300,NOW)
    request.update(NOW,s,d,100,1,meter_id="meter")
    request.accept_tolerance(s.soc.value)
    assert request.phase=="within_tolerance" and request.confirmed_soc is None
    s=snapshot(soc=73.8,energy=51.66,range_km=295.2,now=NOW+60)
    assert request.update(NOW+60,s,demand(c,s,300,NOW+60),100,1,meter_id="meter")==0
    s=snapshot(soc=73.8,energy=51.66,range_km=295.2,now=NOW+360)
    assert request.update(NOW+360,s,demand(c,s,300,NOW+360),100,1,meter_id="meter")>0.5
