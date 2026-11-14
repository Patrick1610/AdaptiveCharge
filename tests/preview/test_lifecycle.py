"""Load the shipped custom integration in an isolated real Home Assistant."""

from pathlib import Path
from unittest.mock import patch

import pytest

from homeassistant import loader
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
async def test_real_config_and_vehicle_subentry_flows(tmp_path):
    hass=await make_hass(tmp_path)
    try:
        form=await hass.config_entries.flow.async_init(DOMAIN,context={"source":"user"})
        assert form["type"]=="form" and form["step_id"]=="user"
        data={"name":"Enphase preview","meter_entity":"sensor.energy","power_entity":"sensor.power","plug_entity":"binary_sensor.plug","power_kw":11,"reserve_min":15,"minimum_min":3,"house_kw":0.5,"price_interval_min":15,"price_attribute":"data","price_time_key":"time","import_price_key":"price","export_price_key":""}
        result=await hass.config_entries.flow.async_configure(form["flow_id"],data)
        await hass.async_block_till_done()
        assert result["type"]=="create_entry"
        configured=result["result"]
        assert configured.data["control_enabled"] is False
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
        assert meter.entity_id==data["meter_entity"]
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
