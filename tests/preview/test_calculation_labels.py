"""Vehicle calculation choices remain editable and preserve stored profiles."""

import json
from pathlib import Path
import re

import probatio
import pytest
from homeassistant.helpers import config_validation as cv

from custom_components.adaptive_charge_preview.config_flow import VehicleSubentryFlow, vehicle_schema


@pytest.mark.parametrize("stored", ["tesla", "id3"])
def test_existing_method_survives_serialized_ha_form(stored):
    data = {
        "name": "My vehicle", "kind": stored, "soc_entity": "sensor.soc",
        "battery_entity": "sensor.stored_energy", "range_entity": "sensor.range",
        "limit_entity": "number.limit", "cable_entity": "binary_sensor.cable",
    }
    schema = VehicleSubentryFlow().add_suggested_values_to_schema(vehicle_schema(data), data)
    fields = probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
    choice = next(field for field in fields if field["name"] == "kind")
    assert choice["selector"]["select"]["options"] == ["tesla", "id3"]
    assert choice["selector"]["select"]["translation_key"] == "range_calculation"
    assert choice["description"]["suggested_value"] == stored
    assert vehicle_schema(data)(data)["kind"] == stored


@pytest.mark.parametrize("relative", ["strings.json", "translations/en.json", "translations/nl.json"])
def test_new_and_edit_forms_explain_brand_independent_methods(relative):
    root = Path(__file__).parents[2] / "custom_components/adaptive_charge_preview"
    content = json.loads((root / relative).read_text())
    methods = content["selector"]["range_calculation"]["options"]
    assert set(methods) == {"tesla", "id3"}
    vehicle = content["config_subentries"]["vehicle"]["step"]
    assert vehicle["user"]["data"] == vehicle["reconfigure"]["data"]
    assert vehicle["user"]["data_description"] == vehicle["reconfigure"]["data_description"]
    assert "range_entity" in vehicle["user"]["data_description"]
    visible = [*methods.values(), *(json.dumps(step, ensure_ascii=False) for step in vehicle.values())]
    assert not any(re.search(r"\b(Tesla|Volkswagen|VW|ID\.?3)\b", text, re.I) for text in visible)
