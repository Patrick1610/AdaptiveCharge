"""Native HA templates: remaining km and a restored SOH reference, no services."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from homeassistant.components.sensor import SensorExtraStoredData
from homeassistant.components.template.sensor import SENSOR_YAML_SCHEMA, StateSensorEntity
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry
from homeassistant.helpers.template import Template


HELPERS = yaml.safe_load((Path(__file__).parents[2] / "examples/id3-remaining-range.yaml").read_text())
RANGE = "sensor.volkswagen_id_3_pro_performance_electric_range"
SOC = "sensor.volkswagen_id_3_pro_performance_battery_level"
SOH = "sensor.volkswagen_id_3_pro_performance_battery_health_state_of_health"


@pytest.mark.asyncio
@pytest.mark.parametrize("reported,soc,live,saved,expected", [
    (223, 73, 89, 89, 223),
    (184, 62, "unknown", "unknown", 184),
    (0, 0, "unknown", "unknown", 0),
    (312, 73, 89, 90, 202.7),
    ("312.0", 62, 89, 90, 172.2),
    ("unknown", 73, 89, 90, 202.7),
    ("unavailable", 73, "unknown", 89, 202.7),
    (312, 73, "unknown", "unknown", 205.0),
    (312, 73, "unavailable", 0, 205.0),
    (312, 73, 101, 89, 202.7),
    ("nan", 73, 89, 90, 202.7),
    ("inf", 73, 89, 90, 202.7),
    (-1, 73, 89, 90, 202.7),
    (312, 0, 89, 90, 0),
    (223, "unknown", "unknown", "unknown", 223),
    (312, "unknown", 89, 90, None),
    (312, 101, 89, 90, None),
])
async def test_remaining_range_sources_and_boundaries(tmp_path, reported, soc, live, saved, expected):
    hass = HomeAssistant(str(tmp_path))
    try:
        for entity_id, state in ((RANGE, reported), (SOC, soc), (SOH, live), ("sensor.id3_soh_reference", saved)):
            hass.states.async_set(entity_id, state)
        available = Template(HELPERS["remaining_range"]["additional_options"]["availability"], hass).async_render()
        assert available is (expected is not None)
        actual = Template(HELPERS["remaining_range"]["state"], hass).async_render()
        if expected is None:
            assert actual is None
        else:
            assert float(actual) == pytest.approx(expected)
        assert not hass.services.async_services()
    finally:
        await hass.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("live,restored,expected", [
    (89, 90, 89), ("unknown", 89, 89), ("unavailable", 87, 87),
    (100, 89, 100), (0, 89, 89), ("unknown", 100, 100),
    ("unknown", "unknown", 90), (0, 89, 89), (101, 89, 89),
    ("nan", "unknown", 90), ("inf", "unknown", 90),
])
async def test_soh_retains_valid_value_or_uses_ninety(tmp_path, live, restored, expected):
    hass = HomeAssistant(str(tmp_path))
    try:
        hass.states.async_set(SOH, live)
        actual = Template(HELPERS["soh_reference"]["state"], hass).async_render({"this": SimpleNamespace(state=restored)})
        assert float(actual) == expected
    finally:
        await hass.async_stop()


@pytest.mark.asyncio
async def test_native_sensor_restore_supplies_last_soh_after_restart(tmp_path):
    """Exercise HA's restore implementation before rendering an absent VW source."""
    hass = HomeAssistant(str(tmp_path))
    try:
        hass.data[device_registry.DATA_REGISTRY] = device_registry.DeviceRegistry(hass)
        await device_registry.async_load(hass, load_empty=True)
        options = {k: v for k, v in HELPERS["soh_reference"].items() if k != "entity_id"}
        entity = StateSensorEntity(hass, SENSOR_YAML_SCHEMA(options), "range-helper-test")
        entity.entity_id = "sensor.id3_soh_reference"
        entity.async_get_last_state = AsyncMock(return_value=State(entity.entity_id, "87"))
        entity._async_get_last_template_data = AsyncMock(return_value=SensorExtraStoredData(87, "%"))
        await entity.async_restore_last_state()
        assert entity.native_value == 87
        hass.states.async_set(SOH, "unavailable")
        actual = Template(HELPERS["soh_reference"]["state"], hass).async_render({"this": SimpleNamespace(state=str(entity.native_value))})
        assert float(actual) == 87
    finally:
        await hass.async_stop()
