"""Load the shipped custom integration in an isolated real Home Assistant."""

from pathlib import Path
import json
from types import MappingProxyType
from unittest.mock import patch

import pytest

from homeassistant import loader
from homeassistant.components.config.config_entries import ConfigManagerFlowIndexView
from homeassistant.config_entries import ConfigEntries, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame, entity_registry, device_registry, area_registry

from custom_components.adaptive_charge_preview.const import DOMAIN
from .test_ha import entry


async def make_hass(tmp_path):
    custom=tmp_path/"custom_components"
    custom.mkdir()
    (custom/DOMAIN).symlink_to(Path(__file__).resolve().parents[2]/"custom_components"/DOMAIN,target_is_directory=True)
    hass=HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    frame.async_setup(hass)
    device_registry.async_setup(hass)
    hass.config_entries=ConfigEntries(hass,{})
    await hass.config_entries.async_initialize()
    await area_registry.async_load(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    return hass


def form_values(fields):
    """Collect what the frontend initially submits for a prefilled form."""
    values = {}
    for field in fields:
        name = field["name"]
        if "suggested_value" in field.get("description", {}):
            values[name] = field["description"]["suggested_value"]
        elif "default" in field:
            values[name] = field["default"]
        elif field.get("type") == "expandable":
            children = form_values(field["schema"])
            if children or field.get("required"):
                values[name] = children
    return values


@pytest.mark.asyncio
async def test_full_setup_platforms_and_unload_are_read_only(tmp_path):
    hass=await make_hass(tmp_path)
    e=entry()
    try:
        with patch.object(type(hass.services),"async_call",side_effect=AssertionError("Preview sent a service")):
            await hass.config_entries.async_add(e)
            await hass.async_block_till_done()
        assert e.state is ConfigEntryState.LOADED
        assert e.runtime_data.data["control_enabled"] is False
        registered=entity_registry.async_entries_for_config_entry(entity_registry.async_get(hass),e.entry_id)
        assert len(registered)==10
        assert sum(item.config_subentry_id is not None for item in registered)==7
        devices=device_registry.async_entries_for_config_entry(device_registry.async_get(hass),e.entry_id)
        manifest=json.loads((Path(__file__).resolve().parents[2]/"custom_components"/DOMAIN/"manifest.json").read_text())
        assert devices and all(device.sw_version==manifest["version"] for device in devices)
        own_states=[s for s in hass.states.async_all() if "preview" in s.entity_id]
        assert own_states
        assert hass.services.has_service(DOMAIN,"start")
        with patch.object(type(hass.services),"async_call",side_effect=AssertionError("Unload sent a service")):
            assert await hass.config_entries.async_unload(e.entry_id)
        assert e.state is ConfigEntryState.NOT_LOADED
        assert not hass.services.has_service(DOMAIN,"start")
    finally:
        await hass.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["original_attribute", "original_state", "renamed_history", "live_source"])
async def test_upgrade_removes_only_the_historical_charger_bindings(tmp_path, source_type):
    hass = await make_hass(tmp_path)
    configured = entry()
    subentries = dict(configured.subentries)
    source = "sensor.iq_ev_charger_last_session"
    attribute = "plugged_in_at" if source_type == "original_attribute" else ""
    if source_type in {"renamed_history", "live_source"}:
        history = source_type == "renamed_history"
        registered = entity_registry.async_get(hass).async_get_or_create(
            "sensor", "enphase_ev", "renamed_last_session" if history else "live_attachment",
            translation_key="last_session" if history else "attachment",
        )
        source = registered.entity_id
    data = {**configured.data, "connection_entity": source,
            "connection_attribute": attribute, "charging_entity": "binary_sensor.charge",
            "mode_entity": "sensor.old_mode", "price_entity": "sensor.price", "power_kw": 7}
    object.__setattr__(configured, "data", MappingProxyType(data))
    try:
        await hass.config_entries.async_add(configured)
        await hass.async_block_till_done()
        assert configured.state is ConfigEntryState.LOADED and configured.minor_version == 2
        assert dict(configured.subentries) == subentries
        removed = {"connection_attribute", "mode_entity", "charging_entity"}
        if source_type != "live_source":
            removed.add("connection_entity")
        assert dict(configured.data) == {key: value for key, value in data.items() if key not in removed}
    finally:
        await hass.config_entries.async_unload(configured.entry_id)
        await hass.async_stop()


@pytest.mark.asyncio
async def test_real_config_and_vehicle_subentry_flows(tmp_path):
    hass=await make_hass(tmp_path)
    try:
        form=await hass.config_entries.flow.async_init(DOMAIN,context={"source":"user"})
        assert form["type"]=="form" and form["step_id"]=="user"
        data = {
            "name": "Enphase preview",
            "power_entity": "sensor.power",
            "charger_connections": {"meter_entity": "sensor.energy", "plug_entity": "binary_sensor.plug"},
        }
        result=await hass.config_entries.flow.async_configure(form["flow_id"],data)
        await hass.async_block_till_done()
        assert result["type"]=="create_entry"
        configured=result["result"]
        assert configured.data["control_enabled"] is False
        assert configured.data["export_price_key"] == ""
        assert "connection_attribute" not in configured.data
        assert configured.data["power_kw"] == 11
        assert configured.data["minimum_min"] == 3
        assert "charger_connections" not in configured.data
        vehicle_form=await hass.config_entries.subentries.async_init((configured.entry_id,"vehicle"),context={"source":"user"})
        assert vehicle_form["type"]=="form"
        vehicle={"name":"Tesla","kind":"tesla","soc_entity":"sensor.soc","battery_entity":"sensor.energy_car","range_entity":"sensor.range","limit_entity":"number.limit","cable_entity":"binary_sensor.cable","droop_pp":1,"margin":0.15,"limit_writer_entity":"number.limit"}
        child=await hass.config_entries.subentries.async_configure(vehicle_form["flow_id"],vehicle)
        await hass.async_block_till_done()
        assert child["type"]=="create_entry"
        assert len(configured.subentries)==1
        assert configured.state is ConfigEntryState.LOADED
        registered=entity_registry.async_entries_for_config_entry(entity_registry.async_get(hass),configured.entry_id)
        assert len(registered)==10 and sum(e.config_subentry_id is not None for e in registered)==7
        subentry_id=next(iter(configured.subentries))
        own=next(e.entity_id for e in registered if e.domain=="sensor")
        rejected=await hass.config_entries.subentries.async_init((configured.entry_id,"vehicle"),context={"source":"user"},data={**vehicle,"soc_entity":own})
        assert rejected["type"]=="form" and rejected["errors"]=={"soc_entity":"preview_source"}
        assert len(configured.subentries)==1 and configured.state is ConfigEntryState.LOADED
        reconfigure=await hass.config_entries.subentries.async_init((configured.entry_id,"vehicle"),context={"source":"reconfigure","subentry_id":subentry_id})
        without_writer={k:v for k,v in vehicle.items() if k!="limit_writer_entity"}
        updated=await hass.config_entries.subentries.async_configure(reconfigure["flow_id"],without_writer)
        await hass.async_block_till_done()
        assert updated["type"]=="abort"
        assert updated["translation_domain"]=="homeassistant"
        assert configured.subentries[subentry_id].data.get("limit_writer_entity") is None
        duplicate=await hass.config_entries.flow.async_init(DOMAIN,context={"source":"user"},data=data)
        assert duplicate["type"]=="abort" and duplicate["reason"]=="already_configured"
        # A previously unregistered source can gain a registry entry; keep
        # the charger entry, subentry identities and persisted bookkeeping.
        meter=entity_registry.async_get(hass).async_get_or_create("sensor","enphase","meter",suggested_object_id="energy")
        assert meter.entity_id==configured.data["meter_entity"]
        parent_form=await hass.config_entries.flow.async_init(DOMAIN,context={"source":"reconfigure","entry_id":configured.entry_id})
        migrated=await hass.config_entries.flow.async_configure(parent_form["flow_id"],data)
        await hass.async_block_till_done()
        assert migrated["type"]=="abort" and configured.state is ConfigEntryState.LOADED
        assert migrated["translation_domain"]=="homeassistant"
        assert configured.unique_id==f"entity:{meter.id}" and subentry_id in configured.subentries
        hass.config_entries.async_remove_subentry(configured,subentry_id)
        await hass.async_block_till_done()
        assert configured.state is ConfigEntryState.LOADED and not configured.subentries
        assert len(entity_registry.async_entries_for_config_entry(entity_registry.async_get(hass),configured.entry_id))==3
    finally:
        await hass.async_block_till_done()
        for configured in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_unload(configured.entry_id)
        await hass.async_stop()


@pytest.mark.asyncio
async def test_charger_form_round_trip_preserves_flat_settings_and_folds_sections(tmp_path):
    hass = await make_hass(tmp_path)
    try:
        flow = hass.config_entries.flow
        view = ConfigManagerFlowIndexView(flow)
        form = await flow.async_init(DOMAIN, context={"source": "user"})
        layout = {field["name"]: field for field in view._prepare_result_json(form)["data_schema"]}
        assert set(layout) == {
            "name", "enphase_profile", "power_entity", "price_source", "charger_connections",
            "planning_settings", "tariff_settings", "legacy_controller",
        }
        assert layout["charger_connections"]["expanded"] is True
        assert layout["price_source"]["expanded"] is True
        assert layout["tariff_settings"]["expanded"] is False
        initial_values = form_values(layout.values())
        assert initial_values["planning_settings"]["power_kw"] == 11
        assert initial_values["tariff_settings"]["price_attribute"] == "data"
        tariff_fields = {field["name"]: field for field in layout["tariff_settings"]["schema"]}
        assert tariff_fields["zonneplan_tax_eur_kwh"]["visible"]["value"] == "zonneplan"
        assert tariff_fields["bonus_export_ytd_entity"]["visible"]["field"] == "zonneplan_bonus_eligible"
        assert not tariff_fields["export_price_key"].get("required")
        basic = {
            "name": "Enphase",
            "enphase_profile": "self_consumption",
            "power_entity": "sensor.power",
        }
        tariff = {
            "export_contract": "zonneplan", "export_price_key": "",
            "zonneplan_tax_eur_kwh": .11, "zonneplan_tax_valid_until": "2026-12-31",
            "zonneplan_bonus_eligible": True, "bonus_export_ytd_entity": "sensor.ytd",
        }
        conn = {"meter_entity": "sensor.energy", "plug_entity": "binary_sensor.plug",
                "control_feedback_entity": "sensor.charge_mode"}
        legacy = {"legacy_master_entity": "input_boolean.legacy", "generation_entity": "counter.legacy"}
        initial = await flow.async_configure(form["flow_id"], {
            **basic, "charger_connections": conn, "tariff_settings": tariff,
            "legacy_controller": legacy, "planning_settings": {"maximum_command_kwh": 83},
            "price_source": {"price_entity": "sensor.enever"},
        })
        configured = initial["result"]
        await hass.async_block_till_done()
        old = dict(configured.data)
        old["installation_note"] = "Retain settings from other versions"
        hass.config_entries.async_update_entry(configured, data=old)
        await hass.async_block_till_done()
        context = {"source": "reconfigure", "entry_id": configured.entry_id}
        form = await flow.async_init(DOMAIN, context=context)
        layout = {field["name"]: field for field in view._prepare_result_json(form)["data_schema"]}
        assert layout["charger_connections"]["expanded"] is False
        fields = {field["name"]: field for field in layout["charger_connections"]["schema"]}
        assert fields["control_feedback_entity"]["description"]["suggested_value"] == conn["control_feedback_entity"]
        assert "mode_entity" not in fields and "charging_entity" not in fields
        assert "connection_entity" not in fields and "live_connection_identity" not in layout
        before = configured.runtime_data
        result = await flow.async_configure(form["flow_id"], basic)
        await hass.async_block_till_done()
        assert result["reason"] == "reconfigure_successful"
        assert dict(configured.data) == old
        assert configured.runtime_data is before
        form = await flow.async_init(DOMAIN, context=context)
        submitted = form_values(view._prepare_result_json(form)["data_schema"])
        result = await flow.async_configure(form["flow_id"], submitted)
        await hass.async_block_till_done()
        assert result["reason"] == "reconfigure_successful"
        assert dict(configured.data) == old
        assert configured.runtime_data is before
        form = await flow.async_init(DOMAIN, context=context)
        with patch.object(configured.runtime_data.control, "binding_change_blocked", return_value=True) as guard:
            rejected = await flow.async_configure(form["flow_id"], {**basic, "enphase_profile": "ai_optimisation"})
            assert rejected["reason"] == "finish_control_cleanup_first"
            assert guard.call_args.args[0]["control_feedback_entity"] == conn["control_feedback_entity"]
        assert dict(configured.data) == old
        form = await flow.async_init(DOMAIN, context=context)
        await flow.async_configure(form["flow_id"], {**basic, "enphase_profile": "ai_optimisation",
                                                     "tariff_settings": {"export_contract": "unknown"}})
        await hass.async_block_till_done()
        assert dict(configured.data) == {**old, "enphase_profile": "ai_optimisation", "export_contract": "unknown"}
        form = await flow.async_init(DOMAIN, context=context)
        await flow.async_configure(form["flow_id"], {**basic, "enphase_profile": "ai_optimisation",
                                                     "tariff_settings": {"export_contract": "unknown"}})
        await hass.async_block_till_done()
        assert configured.data["zonneplan_tax_valid_until"] == tariff["zonneplan_tax_valid_until"]
        form = await flow.async_init(DOMAIN, context=context)
        await flow.async_configure(form["flow_id"], {**basic, "enphase_profile": "ai_optimisation",
                                                     "legacy_controller": {"legacy_master_entity": legacy["legacy_master_entity"]}})
        await hass.async_block_till_done()
        assert "generation_entity" not in configured.data
        assert configured.data["legacy_master_entity"] == legacy["legacy_master_entity"]
        assert configured.data["price_entity"] == "sensor.enever"
        form = await flow.async_init(DOMAIN, context=context)
        await flow.async_configure(form["flow_id"], {**basic, "enphase_profile": "ai_optimisation", "price_source": {}})
        await hass.async_block_till_done()
        assert "price_entity" not in configured.data
    finally:
        await hass.async_block_till_done()
        for configured in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_unload(configured.entry_id)
        await hass.async_stop()


@pytest.mark.asyncio
async def test_charger_identity_survives_entity_rename_and_rejects_feedback(tmp_path):
    from custom_components.adaptive_charge_preview.config_flow import identity
    from custom_components.adaptive_charge_preview.sources import validate_source_bindings
    hass=await make_hass(tmp_path)
    try:
        registry=entity_registry.async_get(hass)
        source=registry.async_get_or_create("sensor","enphase","energy-meter")
        original=identity(hass,source.entity_id)
        renamed=registry.async_update_entity(source.entity_id,new_entity_id="sensor.renamed_iq_energy")
        assert identity(hass,renamed.entity_id)==original
        own=registry.async_get_or_create("sensor",DOMAIN,"own-preview")
        with pytest.raises(ValueError,match="cannot be input"):
            validate_source_bindings(hass,[{"meter_entity":own.entity_id}])
        validate_source_bindings(hass,[{"meter_entity":renamed.entity_id}])
    finally:
        await hass.async_stop()
