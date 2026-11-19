"""Bind a published Tessie value to its upstream charge-state timestamp."""

from types import SimpleNamespace

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES

from custom_components.adaptive_charge_preview.sources import tessie_capture


class TessieCoordinator:
    __module__ = "homeassistant.components.tessie.coordinator"

    def __init__(self, data):
        self.data = data
        self.last_update_success = True


def fixture(key, raw, published, unit):
    source=State("sensor.selected",str(published),{"unit_of_measurement":unit})
    coordinator=TessieCoordinator({key:raw,"charge_state_timestamp":1_794_556_000_000})
    entity=SimpleNamespace(coordinator=coordinator,entity_description=SimpleNamespace(data_key=key,key=key))
    hass=SimpleNamespace(data={er.DATA_REGISTRY:SimpleNamespace(entities={},async_get=lambda e:SimpleNamespace(platform="tessie")),DATA_INSTANCES:{"sensor":SimpleNamespace(get_entity=lambda e:entity)}})
    return hass,source,entity


@pytest.mark.parametrize("key,raw,published,unit",[
    ("charge_state_usable_battery_level",42,42,"%"),
    ("charge_state_energy_remaining",33.62,33.62,"kWh"),
    ("charge_state_est_battery_range",120,193.12128,"km"),
])
def test_actual_selected_value_matches_documented_charge_timestamp(key,raw,published,unit):
    hass,source,_=fixture(key,raw,published,unit)
    assert tessie_capture(hass,source)==1_794_556_000


@pytest.mark.parametrize("change",["wrong_value","failed_coordinator","wrong_platform","unsupported_key","missing_timestamp","wrong_coordinator"])
def test_inconsistent_or_unrelated_data_never_gets_fabricated_capture(change):
    hass,source,entity=fixture("charge_state_energy_remaining",33.62,33.62,"kWh")
    if change=="wrong_value":
        entity.coordinator.data["charge_state_energy_remaining"]=30
    elif change=="failed_coordinator":
        entity.coordinator.last_update_success=False
    elif change=="wrong_platform":
        hass.data[er.DATA_REGISTRY]=SimpleNamespace(entities={},async_get=lambda e:SimpleNamespace(platform="unrelated"))
    elif change=="unsupported_key":
        entity.entity_description.data_key="unrelated"
    elif change=="missing_timestamp":
        entity.coordinator.data.pop("charge_state_timestamp")
    else:
        entity.coordinator=SimpleNamespace(data=entity.coordinator.data,last_update_success=True)
    assert tessie_capture(hass,source) is None
