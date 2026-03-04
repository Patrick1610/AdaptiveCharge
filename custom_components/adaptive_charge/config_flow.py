"""Config flow for AdaptiveCharge."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_SENSOR,
    CONF_CABLE_SENSOR,
    CONF_CHARGE_LIMIT_NUMBER,
    CONF_CHARGE_LIMIT_SENSOR,
    CONF_CHARGE_BUFFER,
    CONF_CHARGE_CURRENT_NUMBER,
    CONF_CHARGE_SWITCH,
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENT_RANGE_SENSOR,
    CONF_DEFAULT_CHARGE_LIMIT,
    CONF_DESIRED_RANGE,
    CONF_ENABLE_UTILITY_METERS,
    CONF_EV_POWER_SENSOR,
    CONF_FORECAST_SENSORS,
    CONF_FORECAST_TOTAL_SENSORS,
    CONF_IMPORT_GUARD_DURATION,
    CONF_IMPORT_GUARD_THRESHOLD,
    CONF_INVERT_NET_POWER,
    CONF_LOW_POWER_FORECAST_THRESHOLD_KWH,
    CONF_LOW_POWER_THRESHOLD,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MAX_CURRENT_LIMIT,
    CONF_MIN_CURRENT_LIMIT,
    CONF_MODULATE_MIN_INTERVAL,
    CONF_NET_POWER_MODE,
    CONF_NET_POWER_SENSOR,
    CONF_NIGHT_OFF_HOUR,
    CONF_NIGHT_OFF_MINUTE,
    CONF_PRESENCE_ENTITY,
    CONF_PRODUCTION_SENSOR,
    CONF_RANGE_HYSTERESIS_PCT,
    CONF_SAMPLE_INTERVAL,
    CONF_SMOOTHING_WINDOW,
    CONF_SOLAR_DONE_DURATION,
    CONF_SOLAR_DONE_THRESHOLD,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_SENSORS,
    CONF_SHOW_FORECAST_TOTAL,
    CONF_SPLIT_MISSED_SOLAR,
    CONF_START_DELAY,
    CONF_STOP_DELAY,
    CONF_SURPLUS_START_THRESHOLD_A,
    CONF_SURPLUS_STOP_THRESHOLD_A,
    CONF_TONIGHT_START_HOUR,
    CONF_TONIGHT_START_MINUTE,
    CONF_UTILITY_DAILY,
    CONF_UTILITY_MONTHLY,
    CONF_UTILITY_YEARLY,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_CHARGE_BUFFER,
    DEFAULT_CHARGE_LIMIT,
    DEFAULT_DESIRED_RANGE,
    DEFAULT_IMPORT_GUARD_DURATION_S,
    DEFAULT_IMPORT_GUARD_THRESHOLD_W,
    DEFAULT_LOW_POWER_FORECAST_THRESHOLD_KWH,
    DEFAULT_LOW_POWER_THRESHOLD,
    DEFAULT_MAX_CURRENT_LIMIT,
    DEFAULT_MIN_CURRENT_LIMIT,
    DEFAULT_MODULATE_MIN_INTERVAL,
    DEFAULT_NIGHT_OFF_HOUR,
    DEFAULT_NIGHT_OFF_MINUTE,
    DEFAULT_RANGE_HYSTERESIS_PCT,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_SMOOTHING_WINDOW,
    DEFAULT_SOLAR_DONE_DURATION,
    DEFAULT_SOLAR_DONE_THRESHOLD,
    DEFAULT_START_DELAY,
    DEFAULT_STOP_DELAY,
    DEFAULT_SURPLUS_START_THRESHOLD_A,
    DEFAULT_SURPLUS_STOP_THRESHOLD_A,
    DEFAULT_TONIGHT_START_HOUR,
    DEFAULT_TONIGHT_START_MINUTE,
    DOMAIN,
    MODE_CONSUMPTION_PRODUCTION,
    MODE_NET_ONLY,
)

_LOGGER = logging.getLogger(__name__)


def _parse_time_string(time_str: str, default_hour: int, default_minute: int) -> tuple[int, int]:
    """Parse an HH:MM time string into (hour, minute) with safe defaults."""
    try:
        parts = str(time_str).split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, TypeError, IndexError):
        pass
    return default_hour, default_minute


class AdaptiveChargeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AdaptiveCharge."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise config flow with empty data."""
        super().__init__()
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: name and net power mode."""
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip() or "AdaptiveCharge"
            await self.async_set_unique_id(name)
            self._abort_if_unique_id_configured()
            self._data = {**self._data, **user_input, CONF_NAME: name}
            if user_input[CONF_NET_POWER_MODE] == MODE_NET_ONLY:
                return await self.async_step_net_power()
            return await self.async_step_consumption_production()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="AdaptiveCharge"): selector.selector(
                    {"text": {}}
                ),
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
                ),
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
                ),
                vol.Optional(CONF_INVERT_NET_POWER, default=False): selector.selector(
                    {"boolean": {}}
                ),
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
            self._data = {**self._data, **user_input}
            return await self.async_step_vehicle()

        schema = vol.Schema(
            {
                vol.Required(CONF_EV_POWER_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_VOLTAGE_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
            }
        )
        return self.async_show_form(step_id="charger", data_schema=schema)

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: vehicle entities."""
        if user_input is not None:
            self._data = {**self._data, **{k: v for k, v in user_input.items() if v is not None and v != ""}}
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
                vol.Optional(CONF_BATTERY_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
            }
        )
        return self.async_show_form(step_id="vehicle", data_schema=schema)

    async def async_step_range(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5: charge buffer and range hysteresis."""
        errors: dict[str, str] = {}
        if user_input is not None:
            buffer_val = float(user_input.get(CONF_CHARGE_BUFFER, DEFAULT_CHARGE_BUFFER))
            hyst_val = float(user_input.get(CONF_RANGE_HYSTERESIS_PCT, DEFAULT_RANGE_HYSTERESIS_PCT))
            if hyst_val > buffer_val:
                errors[CONF_RANGE_HYSTERESIS_PCT] = "hysteresis_exceeds_buffer"
            else:
                self._data = {**self._data, **user_input}
                return await self.async_step_surplus_settings()

        schema = vol.Schema(
            {
                vol.Required(CONF_CHARGE_BUFFER, default=DEFAULT_CHARGE_BUFFER): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 25,
                            "step": 1,
                            "unit_of_measurement": "%",
                            "mode": "box",
                        }
                    }
                ),
                vol.Required(CONF_RANGE_HYSTERESIS_PCT, default=DEFAULT_RANGE_HYSTERESIS_PCT): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 25,
                            "step": 0.5,
                            "unit_of_measurement": "%",
                            "mode": "box",
                        }
                    }
                ),
                vol.Optional(CONF_DEFAULT_CHARGE_LIMIT, default=DEFAULT_CHARGE_LIMIT): selector.selector(
                    {
                        "number": {
                            "min": 50,
                            "max": 100,
                            "step": 5,
                            "unit_of_measurement": "%",
                            "mode": "box",
                        }
                    }
                ),
                vol.Optional(
                    CONF_LOW_POWER_THRESHOLD, default=DEFAULT_LOW_POWER_THRESHOLD
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 100,
                            "step": 5,
                            "unit_of_measurement": "%",
                            "mode": "box",
                        }
                    }
                ),
                vol.Optional(
                    CONF_LOW_POWER_FORECAST_THRESHOLD_KWH,
                    default=DEFAULT_LOW_POWER_FORECAST_THRESHOLD_KWH,
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 50,
                            "step": 0.5,
                            "unit_of_measurement": "kWh",
                            "mode": "box",
                        }
                    }
                ),
                vol.Optional(
                    CONF_BATTERY_CAPACITY_KWH, default=DEFAULT_BATTERY_CAPACITY_KWH
                ): selector.selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 200,
                            "step": 0.5,
                            "unit_of_measurement": "kWh",
                            "mode": "box",
                        }
                    }
                ),
            }
        )
        return self.async_show_form(step_id="range", data_schema=schema, errors=errors)

    async def async_step_surplus_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 6: surplus charging thresholds and current limits."""
        errors: dict[str, str] = {}
        if user_input is not None:
            start_val = float(user_input.get(CONF_SURPLUS_START_THRESHOLD_A, DEFAULT_SURPLUS_START_THRESHOLD_A))
            stop_val = float(user_input.get(CONF_SURPLUS_STOP_THRESHOLD_A, DEFAULT_SURPLUS_STOP_THRESHOLD_A))
            min_val = float(user_input.get(CONF_MIN_CURRENT_LIMIT, DEFAULT_MIN_CURRENT_LIMIT))
            max_val = float(user_input.get(CONF_MAX_CURRENT_LIMIT, DEFAULT_MAX_CURRENT_LIMIT))
            if stop_val > start_val:
                errors[CONF_SURPLUS_STOP_THRESHOLD_A] = "stop_exceeds_start"
            elif min_val > max_val:
                errors[CONF_MIN_CURRENT_LIMIT] = "min_exceeds_max"
            else:
                self._data = {**self._data, **user_input}
                return await self.async_step_charge_window()

        schema = vol.Schema(
            {
                vol.Required(CONF_SURPLUS_START_THRESHOLD_A, default=DEFAULT_SURPLUS_START_THRESHOLD_A): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(CONF_SURPLUS_STOP_THRESHOLD_A, default=DEFAULT_SURPLUS_STOP_THRESHOLD_A): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(CONF_MIN_CURRENT_LIMIT, default=DEFAULT_MIN_CURRENT_LIMIT): selector.selector(
                    {"number": {"min": 0, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(CONF_MAX_CURRENT_LIMIT, default=DEFAULT_MAX_CURRENT_LIMIT): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
            }
        )
        return self.async_show_form(step_id="surplus_settings", data_schema=schema, errors=errors)

    async def async_step_charge_window(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: night charging time window."""
        if user_input is not None:
            start_h, start_m = _parse_time_string(
                user_input.get("night_charging_start", "22:00"),
                DEFAULT_TONIGHT_START_HOUR, DEFAULT_TONIGHT_START_MINUTE,
            )
            end_h, end_m = _parse_time_string(
                user_input.get("night_charging_end", "05:00"),
                DEFAULT_NIGHT_OFF_HOUR, DEFAULT_NIGHT_OFF_MINUTE,
            )
            self._data = {**self._data, **{
                CONF_TONIGHT_START_HOUR: start_h,
                CONF_TONIGHT_START_MINUTE: start_m,
                CONF_NIGHT_OFF_HOUR: end_h,
                CONF_NIGHT_OFF_MINUTE: end_m,
            }}
            return await self.async_step_solar_optional()

        schema = vol.Schema(
            {
                vol.Required(
                    "night_charging_start",
                    default=f"{DEFAULT_TONIGHT_START_HOUR:02d}:{DEFAULT_TONIGHT_START_MINUTE:02d}",
                ): selector.selector({"time": {}}),
                vol.Required(
                    "night_charging_end",
                    default=f"{DEFAULT_NIGHT_OFF_HOUR:02d}:{DEFAULT_NIGHT_OFF_MINUTE:02d}",
                ): selector.selector({"time": {}}),
            }
        )
        return self.async_show_form(step_id="charge_window", data_schema=schema)

    async def async_step_solar_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 8: optional solar sensor(s) and forecast sensors."""
        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v}
            # Remove the toggle itself from persisted data
            cleaned.pop(CONF_SHOW_FORECAST_TOTAL, None)
            self._data = {**self._data, **cleaned}
            return await self.async_step_actuators_optional()

        schema = vol.Schema(
            {
                vol.Optional(CONF_SOLAR_SENSORS): selector.selector(
                    {"entity": {"domain": "sensor", "multiple": True}}
                ),
                vol.Optional(CONF_FORECAST_SENSORS): selector.selector(
                    {"entity": {"domain": "sensor", "multiple": True}}
                ),
                vol.Optional(
                    CONF_SHOW_FORECAST_TOTAL, default=False
                ): selector.selector({"boolean": {}}),
                vol.Optional(CONF_FORECAST_TOTAL_SENSORS): selector.selector(
                    {"entity": {"domain": "sensor", "multiple": True}}
                ),
            }
        )
        return self.async_show_form(step_id="solar_optional", data_schema=schema)

    async def async_step_actuators_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: optional charge switch, current number, and charge limit number."""
        if user_input is not None:
            self._data = {**self._data, **{k: v for k, v in user_input.items() if v is not None and v != ""}}
            return await self.async_step_advanced()

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHARGE_SWITCH): selector.selector(
                    {"entity": {"domain": "switch"}}
                ),
                vol.Optional(CONF_CHARGE_CURRENT_NUMBER): selector.selector(
                    {"entity": {"domain": "number"}}
                ),
                vol.Optional(CONF_CHARGE_LIMIT_NUMBER): selector.selector(
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
            if user_input.get(CONF_ENABLE_UTILITY_METERS, False):
                return await self.async_step_utility_meters()
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, "AdaptiveCharge"),
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
                vol.Required(
                    CONF_IMPORT_GUARD_THRESHOLD, default=int(DEFAULT_IMPORT_GUARD_THRESHOLD_W)
                ): selector.selector(
                    {"number": {"min": 0, "max": 1000, "step": 10, "unit_of_measurement": "W", "mode": "box"}}
                ),
                vol.Required(
                    CONF_IMPORT_GUARD_DURATION, default=int(DEFAULT_IMPORT_GUARD_DURATION_S)
                ): selector.selector(
                    {"number": {"min": 5, "max": 120, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_ENABLE_UTILITY_METERS, default=False
                ): selector.selector({"boolean": {}}),
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)

    async def async_step_utility_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 9: utility meter period selection."""
        if user_input is not None:
            self._data = {**self._data, **user_input}
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, "AdaptiveCharge"),
                data=self._data,
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_UTILITY_DAILY, default=True): selector.selector({"boolean": {}}),
                vol.Optional(CONF_UTILITY_MONTHLY, default=True): selector.selector({"boolean": {}}),
                vol.Optional(CONF_UTILITY_YEARLY, default=True): selector.selector({"boolean": {}}),
                vol.Optional(CONF_SPLIT_MISSED_SOLAR, default=False): selector.selector({"boolean": {}}),
            }
        )
        return self.async_show_form(step_id="utility_meters", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return options flow handler."""
        return AdaptiveChargeOptionsFlow(config_entry)


class AdaptiveChargeOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for reconfiguring all settings (multi-step)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry
        self._data: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        """Return merged data + options + collected data."""
        return {**self._config_entry.data, **self._config_entry.options, **self._data}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: select net power mode."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_NET_POWER_MODE] == MODE_NET_ONLY:
                return await self.async_step_net_power()
            return await self.async_step_consumption_production()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NET_POWER_MODE,
                    default=current.get(CONF_NET_POWER_MODE, MODE_NET_ONLY),
                ): selector.selector(
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
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_net_power(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2a: net power sensor."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_charger()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NET_POWER_SENSOR,
                    description={"suggested_value": current.get(CONF_NET_POWER_SENSOR, "")},
                ): selector.selector({"entity": {"domain": "sensor"}}),
                vol.Optional(
                    CONF_INVERT_NET_POWER,
                    default=bool(current.get(CONF_INVERT_NET_POWER, False)),
                ): selector.selector({"boolean": {}}),
            }
        )
        return self.async_show_form(step_id="net_power", data_schema=schema)

    async def async_step_consumption_production(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2b: consumption and production sensors."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_charger()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONSUMPTION_SENSOR,
                    description={"suggested_value": current.get(CONF_CONSUMPTION_SENSOR, "")},
                ): selector.selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_PRODUCTION_SENSOR,
                    description={"suggested_value": current.get(CONF_PRODUCTION_SENSOR, "")},
                ): selector.selector({"entity": {"domain": "sensor"}}),
            }
        )
        return self.async_show_form(step_id="consumption_production", data_schema=schema)

    async def async_step_charger(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: charger sensors."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_vehicle()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EV_POWER_SENSOR,
                    default=current.get(CONF_EV_POWER_SENSOR),
                ): selector.selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_VOLTAGE_SENSOR,
                    default=current.get(CONF_VOLTAGE_SENSOR),
                ): selector.selector({"entity": {"domain": "sensor"}}),
            }
        )
        return self.async_show_form(step_id="charger", data_schema=schema)

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: vehicle entities."""
        current = self._current()
        if user_input is not None:
            self._data.update({k: v for k, v in user_input.items() if v is not None and v != ""})
            return await self.async_step_range()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PRESENCE_ENTITY,
                    default=current.get(CONF_PRESENCE_ENTITY),
                ): selector.selector({"entity": {"domain": "device_tracker"}}),
                vol.Required(
                    CONF_CABLE_SENSOR,
                    default=current.get(CONF_CABLE_SENSOR),
                ): selector.selector({"entity": {"domain": "binary_sensor"}}),
                vol.Required(
                    CONF_CURRENT_RANGE_SENSOR,
                    default=current.get(CONF_CURRENT_RANGE_SENSOR),
                ): selector.selector({"entity": {"domain": "sensor"}}),
                vol.Optional(
                    CONF_BATTERY_SENSOR,
                    description={"suggested_value": current.get(CONF_BATTERY_SENSOR, "")},
                ): selector.selector({"entity": {"domain": "sensor"}}),
            }
        )
        return self.async_show_form(step_id="vehicle", data_schema=schema)

    async def async_step_range(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5: charge buffer and range hysteresis."""
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            buffer_val = float(user_input.get(CONF_CHARGE_BUFFER, DEFAULT_CHARGE_BUFFER))
            hyst_val = float(user_input.get(CONF_RANGE_HYSTERESIS_PCT, DEFAULT_RANGE_HYSTERESIS_PCT))
            if hyst_val > buffer_val:
                errors[CONF_RANGE_HYSTERESIS_PCT] = "hysteresis_exceeds_buffer"
            else:
                self._data.update(user_input)
                return await self.async_step_surplus_settings()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CHARGE_BUFFER,
                    default=float(current.get(CONF_CHARGE_BUFFER, DEFAULT_CHARGE_BUFFER)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 25, "step": 1, "unit_of_measurement": "%", "mode": "box"}}
                ),
                vol.Required(
                    CONF_RANGE_HYSTERESIS_PCT,
                    default=float(current.get(CONF_RANGE_HYSTERESIS_PCT, DEFAULT_RANGE_HYSTERESIS_PCT)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 25, "step": 0.5, "unit_of_measurement": "%", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_DEFAULT_CHARGE_LIMIT,
                    default=int(current.get(CONF_DEFAULT_CHARGE_LIMIT, DEFAULT_CHARGE_LIMIT)),
                ): selector.selector(
                    {"number": {"min": 50, "max": 100, "step": 5, "unit_of_measurement": "%", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_LOW_POWER_THRESHOLD,
                    default=float(current.get(CONF_LOW_POWER_THRESHOLD, DEFAULT_LOW_POWER_THRESHOLD)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 100, "step": 5, "unit_of_measurement": "%", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_LOW_POWER_FORECAST_THRESHOLD_KWH,
                    default=float(current.get(
                        CONF_LOW_POWER_FORECAST_THRESHOLD_KWH,
                        DEFAULT_LOW_POWER_FORECAST_THRESHOLD_KWH,
                    )),
                ): selector.selector(
                    {"number": {"min": 0, "max": 50, "step": 0.5, "unit_of_measurement": "kWh", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_BATTERY_CAPACITY_KWH,
                    default=float(current.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 200, "step": 0.5, "unit_of_measurement": "kWh", "mode": "box"}}
                ),
            }
        )
        return self.async_show_form(step_id="range", data_schema=schema, errors=errors)

    async def async_step_surplus_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 6: surplus charging thresholds and current limits."""
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            start_val = float(user_input.get(CONF_SURPLUS_START_THRESHOLD_A, DEFAULT_SURPLUS_START_THRESHOLD_A))
            stop_val = float(user_input.get(CONF_SURPLUS_STOP_THRESHOLD_A, DEFAULT_SURPLUS_STOP_THRESHOLD_A))
            min_val = float(user_input.get(CONF_MIN_CURRENT_LIMIT, DEFAULT_MIN_CURRENT_LIMIT))
            max_val = float(user_input.get(CONF_MAX_CURRENT_LIMIT, DEFAULT_MAX_CURRENT_LIMIT))
            if stop_val > start_val:
                errors[CONF_SURPLUS_STOP_THRESHOLD_A] = "stop_exceeds_start"
            elif min_val > max_val:
                errors[CONF_MIN_CURRENT_LIMIT] = "min_exceeds_max"
            else:
                self._data.update(user_input)
                return await self.async_step_charge_window()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SURPLUS_START_THRESHOLD_A,
                    default=int(current.get(CONF_SURPLUS_START_THRESHOLD_A, DEFAULT_SURPLUS_START_THRESHOLD_A)),
                ): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SURPLUS_STOP_THRESHOLD_A,
                    default=int(current.get(CONF_SURPLUS_STOP_THRESHOLD_A, DEFAULT_SURPLUS_STOP_THRESHOLD_A)),
                ): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(
                    CONF_MIN_CURRENT_LIMIT,
                    default=int(current.get(CONF_MIN_CURRENT_LIMIT, DEFAULT_MIN_CURRENT_LIMIT)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
                vol.Required(
                    CONF_MAX_CURRENT_LIMIT,
                    default=int(current.get(CONF_MAX_CURRENT_LIMIT, DEFAULT_MAX_CURRENT_LIMIT)),
                ): selector.selector(
                    {"number": {"min": 1, "max": 16, "step": 1, "unit_of_measurement": "A", "mode": "box"}}
                ),
            }
        )
        return self.async_show_form(step_id="surplus_settings", data_schema=schema, errors=errors)

    async def async_step_charge_window(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: night charging time window."""
        current = self._current()
        if user_input is not None:
            start_h, start_m = _parse_time_string(
                user_input.get("night_charging_start", "22:00"),
                DEFAULT_TONIGHT_START_HOUR, DEFAULT_TONIGHT_START_MINUTE,
            )
            end_h, end_m = _parse_time_string(
                user_input.get("night_charging_end", "05:00"),
                DEFAULT_NIGHT_OFF_HOUR, DEFAULT_NIGHT_OFF_MINUTE,
            )
            self._data.update({
                CONF_TONIGHT_START_HOUR: start_h,
                CONF_TONIGHT_START_MINUTE: start_m,
                CONF_NIGHT_OFF_HOUR: end_h,
                CONF_NIGHT_OFF_MINUTE: end_m,
            })
            return await self.async_step_solar_optional()

        start_h = int(current.get(CONF_TONIGHT_START_HOUR, DEFAULT_TONIGHT_START_HOUR))
        start_m = int(current.get(CONF_TONIGHT_START_MINUTE, DEFAULT_TONIGHT_START_MINUTE))
        end_h = int(current.get(CONF_NIGHT_OFF_HOUR, DEFAULT_NIGHT_OFF_HOUR))
        end_m = int(current.get(CONF_NIGHT_OFF_MINUTE, DEFAULT_NIGHT_OFF_MINUTE))

        schema = vol.Schema(
            {
                vol.Required(
                    "night_charging_start",
                    default=f"{start_h:02d}:{start_m:02d}",
                ): selector.selector({"time": {}}),
                vol.Required(
                    "night_charging_end",
                    default=f"{end_h:02d}:{end_m:02d}",
                ): selector.selector({"time": {}}),
            }
        )
        return self.async_show_form(step_id="charge_window", data_schema=schema)

    async def async_step_solar_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 6: optional solar sensor(s) and forecast sensors."""
        current = self._current()
        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v}
            # Remove the toggle itself from persisted data
            cleaned.pop(CONF_SHOW_FORECAST_TOTAL, None)
            self._data.update(cleaned)
            return await self.async_step_actuators_optional()

        # Backward compat: migrate old single solar_sensor to list
        old_solar = current.get(CONF_SOLAR_SENSOR, "")
        solar_default = current.get(CONF_SOLAR_SENSORS, [])
        if not solar_default and old_solar:
            solar_default = [old_solar]

        has_total = bool(current.get(CONF_FORECAST_TOTAL_SENSORS, []))

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SOLAR_SENSORS,
                    description={"suggested_value": solar_default},
                ): selector.selector({"entity": {"domain": "sensor", "multiple": True}}),
                vol.Optional(
                    CONF_FORECAST_SENSORS,
                    description={"suggested_value": current.get(CONF_FORECAST_SENSORS, [])},
                ): selector.selector({"entity": {"domain": "sensor", "multiple": True}}),
                vol.Optional(
                    CONF_SHOW_FORECAST_TOTAL, default=has_total,
                ): selector.selector({"boolean": {}}),
                vol.Optional(
                    CONF_FORECAST_TOTAL_SENSORS,
                    description={"suggested_value": current.get(CONF_FORECAST_TOTAL_SENSORS, [])},
                ): selector.selector({"entity": {"domain": "sensor", "multiple": True}}),
            }
        )
        return self.async_show_form(step_id="solar_optional", data_schema=schema)

    async def async_step_actuators_optional(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: optional charge switch, current number, and charge limit number."""
        current = self._current()
        if user_input is not None:
            self._data.update({k: v for k, v in user_input.items() if v is not None and v != ""})
            return await self.async_step_advanced()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CHARGE_SWITCH,
                    description={"suggested_value": current.get(CONF_CHARGE_SWITCH, "")},
                ): selector.selector({"entity": {"domain": "switch"}}),
                vol.Optional(
                    CONF_CHARGE_CURRENT_NUMBER,
                    description={"suggested_value": current.get(CONF_CHARGE_CURRENT_NUMBER, "")},
                ): selector.selector({"entity": {"domain": "number"}}),
                vol.Optional(
                    CONF_CHARGE_LIMIT_NUMBER,
                    description={"suggested_value": current.get(CONF_CHARGE_LIMIT_NUMBER, "")},
                ): selector.selector({"entity": {"domain": "number"}}),
            }
        )
        return self.async_show_form(step_id="actuators_optional", data_schema=schema)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 8: advanced timing settings."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            if user_input.get(CONF_ENABLE_UTILITY_METERS, False):
                return await self.async_step_utility_meters()
            return self.async_create_entry(title="", data=self._data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SMOOTHING_WINDOW,
                    default=int(current.get(CONF_SMOOTHING_WINDOW, DEFAULT_SMOOTHING_WINDOW)),
                ): selector.selector(
                    {"number": {"min": 10, "max": 600, "step": 10, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SAMPLE_INTERVAL,
                    default=int(current.get(CONF_SAMPLE_INTERVAL, DEFAULT_SAMPLE_INTERVAL)),
                ): selector.selector(
                    {"number": {"min": 5, "max": 60, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_THRESHOLD,
                    default=int(current.get(CONF_SOLAR_DONE_THRESHOLD, DEFAULT_SOLAR_DONE_THRESHOLD)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 500, "step": 10, "unit_of_measurement": "W", "mode": "box"}}
                ),
                vol.Required(
                    CONF_SOLAR_DONE_DURATION,
                    default=int(current.get(CONF_SOLAR_DONE_DURATION, DEFAULT_SOLAR_DONE_DURATION)),
                ): selector.selector(
                    {"number": {"min": 60, "max": 3600, "step": 60, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_START_DELAY,
                    default=int(current.get(CONF_START_DELAY, DEFAULT_START_DELAY)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_STOP_DELAY,
                    default=int(current.get(CONF_STOP_DELAY, DEFAULT_STOP_DELAY)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_MODULATE_MIN_INTERVAL,
                    default=int(current.get(CONF_MODULATE_MIN_INTERVAL, DEFAULT_MODULATE_MIN_INTERVAL)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 300, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Required(
                    CONF_IMPORT_GUARD_THRESHOLD,
                    default=int(current.get(CONF_IMPORT_GUARD_THRESHOLD, DEFAULT_IMPORT_GUARD_THRESHOLD_W)),
                ): selector.selector(
                    {"number": {"min": 0, "max": 1000, "step": 10, "unit_of_measurement": "W", "mode": "box"}}
                ),
                vol.Required(
                    CONF_IMPORT_GUARD_DURATION,
                    default=int(current.get(CONF_IMPORT_GUARD_DURATION, DEFAULT_IMPORT_GUARD_DURATION_S)),
                ): selector.selector(
                    {"number": {"min": 5, "max": 120, "step": 5, "unit_of_measurement": "s", "mode": "box"}}
                ),
                vol.Optional(
                    CONF_ENABLE_UTILITY_METERS,
                    default=bool(current.get(CONF_ENABLE_UTILITY_METERS, False)),
                ): selector.selector({"boolean": {}}),
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)

    async def async_step_utility_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 9: utility meter period selection."""
        current = self._current()
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UTILITY_DAILY,
                    default=bool(current.get(CONF_UTILITY_DAILY, True)),
                ): selector.selector({"boolean": {}}),
                vol.Optional(
                    CONF_UTILITY_MONTHLY,
                    default=bool(current.get(CONF_UTILITY_MONTHLY, True)),
                ): selector.selector({"boolean": {}}),
                vol.Optional(
                    CONF_UTILITY_YEARLY,
                    default=bool(current.get(CONF_UTILITY_YEARLY, True)),
                ): selector.selector({"boolean": {}}),
                vol.Optional(
                    CONF_SPLIT_MISSED_SOLAR,
                    default=bool(current.get(CONF_SPLIT_MISSED_SOLAR, True)),
                ): selector.selector({"boolean": {}}),
            }
        )
        return self.async_show_form(step_id="utility_meters", data_schema=schema)

