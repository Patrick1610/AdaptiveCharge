"""Config flow for AdaptiveCharge."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import (
    CONF_CABLE_SENSOR,
    CONF_CHARGE_CURRENT_NUMBER,
    CONF_CHARGE_SWITCH,
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENT_RANGE_SENSOR,
    CONF_DESIRED_RANGE,
    CONF_EV_POWER_SENSOR,
    CONF_MODULATE_MIN_INTERVAL,
    CONF_NET_POWER_MODE,
    CONF_NET_POWER_SENSOR,
    CONF_PRESENCE_ENTITY,
    CONF_PRODUCTION_SENSOR,
    CONF_SAMPLE_INTERVAL,
    CONF_SMOOTHING_WINDOW,
    CONF_SOLAR_DONE_DURATION,
    CONF_SOLAR_DONE_THRESHOLD,
    CONF_SOLAR_SENSOR,
    CONF_START_DELAY,
    CONF_STOP_DELAY,
    CONF_VOLTAGE_FALLBACK,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_DESIRED_RANGE,
    DEFAULT_MODULATE_MIN_INTERVAL,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_SMOOTHING_WINDOW,
    DEFAULT_SOLAR_DONE_DURATION,
    DEFAULT_SOLAR_DONE_THRESHOLD,
    DEFAULT_START_DELAY,
    DEFAULT_STOP_DELAY,
    DEFAULT_VOLTAGE_FALLBACK,
    DOMAIN,
    MODE_CONSUMPTION_PRODUCTION,
    MODE_NET_ONLY,
)

_LOGGER = logging.getLogger(__name__)


class AdaptiveChargeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AdaptiveCharge."""

    VERSION = 1
    _data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: select net power mode."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            if user_input[CONF_NET_POWER_MODE] == MODE_NET_ONLY:
                return await self.async_step_net_power()
            return await self.async_step_consumption_production()

        schema = vol.Schema(
            {
                vol.Required(CONF_NET_POWER_MODE, default=MODE_NET_ONLY): selector.selector(
                    {
                        "select": {
                            "options": [
                                {"value": MODE_NET_ONLY, "label": "Single net power sensor"},
                                {
                                    "value": MODE_CONSUMPTION_PRODUCTION,
                                    "label": "Separate consumption & production sensors",
                                },
                            ]
                        }
                    }
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_net_power(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2a: net power sensor."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            return await self.async_step_charger()

        schema = vol.Schema(
            {
                vol.Required(CONF_NET_POWER_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                )
            }
        )
        return self.async_show_form(step_id="net_power", data_schema=schema)

    async def async_step_consumption_production(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2b: consumption and production sensors."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            return await self.async_step_charger()

        schema = vol.Schema(
            {
                vol.Required(CONF_CONSUMPTION_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_PRODUCTION_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
            }
        )
        return self.async_show_form(step_id="consumption_production", data_schema=schema)

    async def async_step_charger(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: charger sensors."""
        if user_input is not None:
            self._data = {**self._data, **{k: v for k, v in user_input.items() if v != "" and v is not None}}
            # Always store voltage_fallback (it may be the only voltage source)
            if CONF_VOLTAGE_FALLBACK in user_input:
                self._data[CONF_VOLTAGE_FALLBACK] = user_input[CONF_VOLTAGE_FALLBACK]
            return await self.async_step_vehicle()

        schema = vol.Schema(
            {
                vol.Required(CONF_EV_POWER_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Optional(CONF_VOLTAGE_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_VOLTAGE_FALLBACK, default=DEFAULT_VOLTAGE_FALLBACK): selector.selector(
                    {
                        "number": {
                            "min": 100,
                            "max": 260,
                            "step": 1,
                            "unit_of_measurement": "V",
                            "mode": "box",
                        }
                    }
                ),
            }
        )
        return self.async_show_form(step_id="charger", data_schema=schema)

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: vehicle entities."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            return await self.async_step_range()

        schema = vol.Schema(
            {
                vol.Required(CONF_PRESENCE_ENTITY): selector.selector(
                    {"entity": {"domain": "device_tracker"}}
                ),
                vol.Required(CONF_CABLE_SENSOR): selector.selector(
                    {"entity": {"domain": "binary_sensor"}}
                ),
                vol.Required(CONF_CURRENT_RANGE_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
            }
        )
        return self.async_show_form(step_id="vehicle", data_schema=schema)

    async def async_step_range(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5: desired range."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            return await self.async_step_solar_optional()

        schema = vol.Schema(
            {
                vol.Required(CONF_DESIRED_RANGE, default=DEFAULT_DESIRED_RANGE): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 1000,
                            "step": 1,
                            "unit_of_measurement": "km",
                            "mode": "box",
                        }
                    }
                )
            }
        )
        return self.async_show_form(step_id="range", data_schema=schema)

    async def async_step_solar_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 6: optional solar sensor."""
        if user_input is not None:
            self._data = {**self._data, **{k: v for k, v in user_input.items() if v}}
            return await self.async_step_actuators_optional()

        schema = vol.Schema(
            {
                vol.Optional(CONF_SOLAR_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
            }
        )
        return self.async_show_form(step_id="solar_optional", data_schema=schema)

    async def async_step_actuators_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: optional charge switch and current number."""
        if user_input is not None:
            self._data = {**self._data, **{k: v for k, v in user_input.items() if v}}
            return await self.async_step_advanced()

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHARGE_SWITCH): selector.selector(
                    {"entity": {"domain": "switch"}}
                ),
                vol.Optional(CONF_CHARGE_CURRENT_NUMBER): selector.selector(
                    {"entity": {"domain": "number"}}
                ),
            }
        )
        return self.async_show_form(step_id="actuators_optional", data_schema=schema)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 8: advanced timing settings."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="AdaptiveCharge",
                data=self._data,
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SMOOTHING_WINDOW, default=DEFAULT_SMOOTHING_WINDOW
                ): selector.selector(
                    {"number": {"min": 10, "max": 600, "step": 10, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SAMPLE_INTERVAL, default=DEFAULT_SAMPLE_INTERVAL
                ): selector.selector(
                    {"number": {"min": 5, "max": 60, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_THRESHOLD, default=DEFAULT_SOLAR_DONE_THRESHOLD
                ): selector.selector(
                    {"number": {"min": 0, "max": 500, "step": 10, "unit_of_measurement": "W", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_DURATION, default=DEFAULT_SOLAR_DONE_DURATION
                ): selector.selector(
                    {"number": {"min": 60, "max": 3600, "step": 60, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_START_DELAY, default=DEFAULT_START_DELAY
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_STOP_DELAY, default=DEFAULT_STOP_DELAY
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_MODULATE_MIN_INTERVAL, default=DEFAULT_MODULATE_MIN_INTERVAL
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return options flow handler."""
        return AdaptiveChargeOptionsFlow(config_entry)


class AdaptiveChargeOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for reconfiguring advanced settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage options."""
        options = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SMOOTHING_WINDOW,
                    default=int(options.get(CONF_SMOOTHING_WINDOW, DEFAULT_SMOOTHING_WINDOW)),
                ): selector.selector(
                    {"number": {"min": 10, "max": 600, "step": 10, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SAMPLE_INTERVAL,
                    default=int(options.get(CONF_SAMPLE_INTERVAL, DEFAULT_SAMPLE_INTERVAL)),
                ): selector.selector(
                    {"number": {"min": 5, "max": 60, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_THRESHOLD,
                    default=int(options.get(CONF_SOLAR_DONE_THRESHOLD, DEFAULT_SOLAR_DONE_THRESHOLD)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 500, "step": 10, "unit_of_measurement": "W", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_DURATION,
                    default=int(options.get(CONF_SOLAR_DONE_DURATION, DEFAULT_SOLAR_DONE_DURATION)),
                ): selector.selector(
                    {"number": {"min": 60, "max": 3600, "step": 60, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_START_DELAY,
                    default=int(options.get(CONF_START_DELAY, DEFAULT_START_DELAY)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_STOP_DELAY,
                    default=int(options.get(CONF_STOP_DELAY, DEFAULT_STOP_DELAY)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_MODULATE_MIN_INTERVAL,
                    default=int(options.get(CONF_MODULATE_MIN_INTERVAL, DEFAULT_MODULATE_MIN_INTERVAL)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
