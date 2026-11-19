"""Static tripwires complement the real HA runtime no-write tests."""

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryError

from custom_components.adaptive_charge_preview import async_setup_entry
from custom_components.adaptive_charge_preview.const import CONTROL_ENABLED
from .test_ha import setup, entry

PACKAGE=Path(__file__).resolve().parents[2]/"custom_components/adaptive_charge_preview"


def test_actuators_confined_to_executor_no_network_or_subprocesses():
    assert CONTROL_ENABLED is False
    forbidden={"async_call","call_service","async_set_value","urlopen","request","run","Popen","system","exec","eval","set_state"}
    # DateTimeEntity.async_set_value is an incoming entity method, not a call.
    for path in PACKAGE.rglob("*.py"):
        tree=ast.parse(path.read_text())
        parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        dispatchers=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ""
                if name == "async_call" and path.name == "control.py":
                    owner=node
                    while not isinstance(owner,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        owner=parents[owner]
                    dispatchers.add(owner.name)
                    assert ast.unparse(node.func)=="self.hass.services.async_call"
                    assert owner.name in {"_call","_notify_fault","_notify_limit"}
                    if owner.name!="_call":
                        assert isinstance(node.args[0],ast.Constant) and node.args[0].value=="persistent_notification"
                        assert isinstance(node.args[1],ast.Constant) and node.args[1].value=="create"
                    continue
                assert name not in forbidden, f"Forbidden call {name} in {path}"
        if path.name=="control.py":
            assert dispatchers=={"_call","_notify_fault","_notify_limit"}
    manifest=json.loads((PACKAGE/"manifest.json").read_text())
    assert manifest["domain"]=="adaptive_charge_preview" and not manifest["requirements"]
    assert (PACKAGE/"switch.py").exists()


@pytest.mark.asyncio
async def test_control_cannot_be_enabled_by_entry_data(tmp_path):
    from homeassistant.core import HomeAssistant
    hass=HomeAssistant(str(tmp_path))
    e=entry(False)
    # Reconstruct immutable HA entry data through its supported local helper.
    from types import MappingProxyType
    object.__setattr__(e,"data",MappingProxyType({**e.data,"control_enabled":True}))
    with pytest.raises(ConfigEntryError,match="using the master"):
        await async_setup_entry(hass,e)
    await hass.async_stop()


@pytest.mark.asyncio
async def test_repeated_start_preserves_delivered_energy(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        before=c.sessions[vehicle]
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await c._async_update_data()
        balance=before.remaining_kwh
        c.async_request_refresh=AsyncMock()
        await c.async_preview_action("start",vehicle,{})
        await c._async_update_data()
        assert c.sessions[vehicle] is before and before.remaining_kwh==pytest.approx(balance)
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_refused_start_does_not_apply_partial_overrides(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        hass.states.async_set("binary_sensor.tesla_cable","off")
        with pytest.raises(ValueError,match="connection"):
            await c.async_preview_action("start",vehicle,{"target_range_km":400})
        assert c.values.get(vehicle,{})=={}
    finally:
        await c.async_shutdown()
        await hass.async_stop()


@pytest.mark.asyncio
async def test_repeated_start_with_temporarily_unknown_id_keeps_original_allowance(tmp_path):
    hass,c,vehicle=await setup(tmp_path)
    try:
        await c._async_update_data()
        original=c.sessions[vehicle]
        hass.states.async_set("sensor.iq_energy",102,{"unit_of_measurement":"kWh"})
        await c._async_update_data()
        balance=original.remaining_kwh
        hass.states.async_set("sensor.connection","unavailable")
        c.async_request_refresh=AsyncMock()
        await c.async_preview_action("start",vehicle,{})
        assert c.sessions[vehicle] is original and original.connection_id=="connection-a"
        assert original.remaining_kwh==pytest.approx(balance) and original.anchor_meter==100
    finally:
        await c.async_shutdown()
        await hass.async_stop()
