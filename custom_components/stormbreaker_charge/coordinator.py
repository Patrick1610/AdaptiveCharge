"""Coordinator for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

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
    CONF_VOLTAGE_SENSOR,
    DEFAULT_COOLDOWN_PERIOD,
    DEFAULT_DESIRED_RANGE,
    DEFAULT_HYSTERESIS_BAND,
    DEFAULT_MAX_CURRENT_LIMIT,
    DEFAULT_MAX_STEP_SIZE,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MIN_START_CURRENT,
    DEFAULT_MODULATE_MIN_INTERVAL,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_SMOOTHING_WINDOW,
    DEFAULT_SOLAR_DONE_DURATION,
    DEFAULT_SOLAR_DONE_THRESHOLD,
    DEFAULT_STALE_THRESHOLD,
    DEFAULT_START_DELAY,
    DEFAULT_STOP_DELAY,
    DOMAIN,
    MODE_CONSUMPTION_PRODUCTION,
    MODE_FORCE,
    MODE_NET_ONLY,
    MODE_STOPPED,
    MODE_SURPLUS,
    SURPLUS_CLAMP_W,
)

_LOGGER = logging.getLogger(__name__)

MAX_CURRENT_ABS = 16


def _get_float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Return the float value of an entity state, or None."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown", ""):
        return None
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


def _get_bool_state(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    """Return the boolean value of an entity state, or None."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown", ""):
        return None
    return state.state in ("on", "home", "true", "1")


def _to_watts(value: float, entity_id: str | None, hass: HomeAssistant) -> float:
    """Convert kW to W if necessary based on unit_of_measurement attribute."""
    if entity_id is None:
        return value
    state = hass.states.get(entity_id)
    if state is None:
        return value
    uom = state.attributes.get("unit_of_measurement", "")
    if "kW" in uom:
        return value * 1000.0
    # Heuristic: if no unit but value looks like kW (very small), multiply
    if not uom and abs(value) < 20:
        return value * 1000.0
    return value


class StormbreakerCoordinator(DataUpdateCoordinator):
    """Coordinator managing EV charge control logic."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Initialise the coordinator."""
        self._entry = entry
        self._entry_id = entry.entry_id
        options = {**entry.data, **entry.options}

        self._net_power_mode: str = options.get(CONF_NET_POWER_MODE, MODE_NET_ONLY)
        self._net_power_sensor: str | None = options.get(CONF_NET_POWER_SENSOR)
        self._consumption_sensor: str | None = options.get(CONF_CONSUMPTION_SENSOR)
        self._production_sensor: str | None = options.get(CONF_PRODUCTION_SENSOR)
        self._ev_power_sensor: str | None = options.get(CONF_EV_POWER_SENSOR)
        self._voltage_sensor: str | None = options.get(CONF_VOLTAGE_SENSOR)
        self._presence_entity: str | None = options.get(CONF_PRESENCE_ENTITY)
        self._cable_sensor: str | None = options.get(CONF_CABLE_SENSOR)
        self._current_range_sensor: str | None = options.get(CONF_CURRENT_RANGE_SENSOR)
        self._solar_sensor: str | None = options.get(CONF_SOLAR_SENSOR)
        self._charge_switch: str | None = options.get(CONF_CHARGE_SWITCH)
        self._charge_current_number: str | None = options.get(CONF_CHARGE_CURRENT_NUMBER)

        self._smoothing_window: int = int(options.get(CONF_SMOOTHING_WINDOW, DEFAULT_SMOOTHING_WINDOW))
        self._sample_interval: int = int(options.get(CONF_SAMPLE_INTERVAL, DEFAULT_SAMPLE_INTERVAL))
        self._solar_done_threshold_w: float = float(options.get(CONF_SOLAR_DONE_THRESHOLD, DEFAULT_SOLAR_DONE_THRESHOLD))
        self._solar_done_duration: int = int(options.get(CONF_SOLAR_DONE_DURATION, DEFAULT_SOLAR_DONE_DURATION))
        self._start_delay: int = int(options.get(CONF_START_DELAY, DEFAULT_START_DELAY))
        self._stop_delay: int = int(options.get(CONF_STOP_DELAY, DEFAULT_STOP_DELAY))
        self._modulate_min_interval: int = int(options.get(CONF_MODULATE_MIN_INTERVAL, DEFAULT_MODULATE_MIN_INTERVAL))

        # Anti-flap / hysteresis
        self._hysteresis_band: float = DEFAULT_HYSTERESIS_BAND
        self._min_start_current: int = DEFAULT_MIN_START_CURRENT

        # Rate limiting
        self._cooldown_period: int = DEFAULT_COOLDOWN_PERIOD
        self._max_step_size: int = DEFAULT_MAX_STEP_SIZE

        # Timestamp coherency
        self._stale_threshold: int = DEFAULT_STALE_THRESHOLD

        # Minimum on/off times
        self._min_on_time: int = DEFAULT_MIN_ON_TIME
        self._min_off_time: int = DEFAULT_MIN_OFF_TIME

        # Mutable runtime state
        self._desired_range: float = float(options.get(CONF_DESIRED_RANGE, DEFAULT_DESIRED_RANGE))
        self._max_current_limit: float = DEFAULT_MAX_CURRENT_LIMIT

        self._charge_now: bool = False
        self._charge_tonight: bool = False
        self._charging_enabled: bool = False

        # Control state
        self._charging_on: bool = False
        self._current_mode: str = MODE_STOPPED
        self._last_action: str = ""
        self._last_raw_floored: int = 0
        self._pending_task: asyncio.Task | None = None
        self._pending_modulate_task: asyncio.Task | None = None
        self._pending_plugin_task: asyncio.Task | None = None
        self._force_charge_prev: bool = False
        self._cable_prev: bool | None = None
        self._pending_action: str | None = None  # "start", "stop", or None

        # Hysteresis / rate-limiting state
        self._applied_current: int = 0
        self._last_change_time: datetime | None = None
        self._last_start_time: datetime | None = None
        self._last_stop_time: datetime | None = None
        self._decision_reason: str = ""
        self._samples_stale: bool = False

        # Solar done tracking
        self._solar_below_threshold_since: datetime | None = None
        self._solar_done: bool = False

        # Smoothing deque: list of (timestamp, current_a)
        self._samples: deque[tuple[datetime, float]] = deque()

        # Unsub for interval tracker and night-off timer
        self._unsub_interval = None
        self._unsub_night_off = None
        self._last_night_off_date: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # We drive updates ourselves via async_track_time_interval
            update_interval=None,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_config_entry_first_refresh(self) -> None:
        """Set up interval and do first refresh."""
        await self._async_tick(None)
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_tick,
            timedelta(seconds=self._sample_interval),
        )
        self._schedule_night_off()

    def _schedule_night_off(self) -> None:
        """Schedule the nightly 05:00 charge_tonight reset."""
        if self._unsub_night_off:
            self._unsub_night_off()
        self._unsub_night_off = async_track_time_change(
            self.hass,
            self._async_night_off,
            hour=5,
            minute=0,
            second=0,
        )

    @callback
    def _async_night_off(self, _now) -> None:
        """Turn off charge_tonight at 05:00."""
        _LOGGER.info("Stormbreaker: Night-Off — disabling charge_tonight at 05:00")
        self._charge_tonight = False
        self._schedule_night_off()

    async def async_shutdown(self) -> None:
        """Cancel subscriptions."""
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self._unsub_night_off:
            self._unsub_night_off()
            self._unsub_night_off = None
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        if self._pending_modulate_task and not self._pending_modulate_task.done():
            self._pending_modulate_task.cancel()

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    @callback
    async def _async_tick(self, _now) -> None:
        """Periodic update tick."""
        try:
            await self._async_update_data_internal()
        except HomeAssistantError as exc:
            _LOGGER.warning("Coordinator tick HA error: %s", exc)
        except (ValueError, TypeError, AttributeError) as exc:
            _LOGGER.warning("Coordinator tick data error: %s", exc)

    # ------------------------------------------------------------------
    # Data update
    # ------------------------------------------------------------------

    async def _async_update(self) -> dict[str, Any]:
        """Called by DataUpdateCoordinator.async_refresh (not primary path)."""
        return await self._async_update_data_internal()

    async def _async_update_data(self) -> dict[str, Any]:
        """Called by DataUpdateCoordinator base class."""
        return await self._async_update_data_internal()

    async def _async_update_data_internal(self) -> dict[str, Any]:
        """Compute all values and run control logic."""
        now = datetime.now()

        # --- Read raw sensor values ---
        net_raw = _get_float_state(self.hass, self._net_power_sensor)
        consumption_raw = _get_float_state(self.hass, self._consumption_sensor)
        production_raw = _get_float_state(self.hass, self._production_sensor)
        ev_raw = _get_float_state(self.hass, self._ev_power_sensor)
        voltage_raw = _get_float_state(self.hass, self._voltage_sensor)
        solar_raw = _get_float_state(self.hass, self._solar_sensor)
        presence = _get_bool_state(self.hass, self._presence_entity)
        cable_connected = _get_bool_state(self.hass, self._cable_sensor)
        current_range = _get_float_state(self.hass, self._current_range_sensor)

        # --- Convert units ---
        if net_raw is not None:
            net_w = _to_watts(net_raw, self._net_power_sensor, self.hass)
        else:
            net_w = None

        if consumption_raw is not None:
            consumption_w = _to_watts(consumption_raw, self._consumption_sensor, self.hass)
        else:
            consumption_w = None

        if production_raw is not None:
            production_w = _to_watts(production_raw, self._production_sensor, self.hass)
        else:
            production_w = None

        if ev_raw is not None:
            ev_w = _to_watts(ev_raw, self._ev_power_sensor, self.hass)
        else:
            ev_w = 0.0

        voltage = voltage_raw if voltage_raw is not None else 230.0

        if solar_raw is not None:
            solar_w = _to_watts(solar_raw, self._solar_sensor, self.hass)
        else:
            solar_w = None

        # --- Compute net_w ---
        if self._net_power_mode == MODE_NET_ONLY:
            computed_net_w = net_w if net_w is not None else 0.0
        else:
            if consumption_w is not None and production_w is not None:
                computed_net_w = consumption_w - production_w
            elif consumption_w is not None:
                computed_net_w = consumption_w
            else:
                computed_net_w = 0.0

        # --- Compute surplus and current ---
        # surplus_w = available surplus for EV = -(net_w) + ev_w
        # net_w positive = importing from grid → negative surplus
        # net_w negative = exporting to grid → positive surplus
        surplus_w = (0.0 - computed_net_w) + ev_w

        # Safety rail: clamp surplus to ±SURPLUS_CLAMP_W
        surplus_w = max(-SURPLUS_CLAMP_W, min(SURPLUS_CLAMP_W, surplus_w))

        # Safety rail: if voltage is 0/unknown, hold safe
        if voltage <= 0:
            voltage = 230.0

        raw_current_a = surplus_w / (voltage * 3.0)
        capped = min(self._max_current_limit, MAX_CURRENT_ABS)
        raw_floored = min(max(int(raw_current_a), 0), int(capped))

        # --- Smoothing ---
        self._samples.append((now, raw_current_a))
        cutoff = now - timedelta(seconds=self._smoothing_window)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        valid_samples = [v for _, v in self._samples]
        smoothed_a = mean(valid_samples) if valid_samples else 0.0
        smoothed_floored = min(max(int(smoothed_a), 0), int(capped))

        # --- Solar done ---
        if solar_w is not None:
            if solar_w < self._solar_done_threshold_w:
                if self._solar_below_threshold_since is None:
                    self._solar_below_threshold_since = now
                elapsed = (now - self._solar_below_threshold_since).total_seconds()
                if elapsed >= self._solar_done_duration:
                    self._solar_done = True
            else:
                self._solar_below_threshold_since = None
                self._solar_done = False
        else:
            self._solar_done = False

        # --- Force charge ---
        # charge_now OR (tonight AND home AND cable AND need AND solar_done)
        need = (
            current_range is not None
            and current_range < self._desired_range
        )
        tonight_condition = (
            self._charge_tonight
            and bool(presence)
            and bool(cable_connected)
            and need
            and self._solar_done
        )
        force_charge = self._charge_now or tonight_condition

        # --- Cable plug-in detection ---
        if cable_connected is not None and cable_connected != self._cable_prev:
            if cable_connected and not self._cable_prev:
                # Cable just connected — cancel any previous plugin task and track the new one
                if self._pending_plugin_task and not self._pending_plugin_task.done():
                    self._pending_plugin_task.cancel()
                self._pending_plugin_task = self.hass.async_create_task(
                    self._action_plug_in_delayed(force_charge, smoothed_floored),
                    eager_start=False,
                )
            self._cable_prev = cable_connected

        # --- Timestamp coherency ---
        self._samples_stale = not self._check_sample_coherency()

        # --- Control logic ---
        if self._samples_stale:
            self._decision_reason = "blocked:stale_samples"
        else:
            await self._run_control_logic(
                force_charge, smoothed_floored, raw_floored, smoothed_a, now
            )

        data: dict[str, Any] = {
            "net_w": computed_net_w,
            "ev_w": ev_w,
            "voltage": voltage,
            "surplus_w": surplus_w,
            "raw_current_a": raw_current_a,
            "raw_floored": raw_floored,
            "smoothed_a": smoothed_a,
            "smoothed_floored": smoothed_floored,
            "solar_w": solar_w,
            "solar_done": self._solar_done,
            "force_charge": force_charge,
            "presence": presence,
            "cable_connected": cable_connected,
            "current_range": current_range,
            "desired_range": self._desired_range,
            "max_current_limit": self._max_current_limit,
            "charge_now": self._charge_now,
            "charge_tonight": self._charge_tonight,
            "tonight_condition": tonight_condition,
            "need": need,
            "charging_enabled": self._charging_enabled,
            "current_mode": self._current_mode,
            "last_action": self._last_action,
            "charging_on": self._charging_on,
            "sample_count": len(self._samples),
            "last_updated": now.isoformat(),
            "decision_reason": self._decision_reason,
            "applied_current": self._applied_current,
            "samples_stale": self._samples_stale,
        }
        # Push to coordinator listeners
        self.async_set_updated_data(data)
        return data

    # ------------------------------------------------------------------
    # Control logic
    # ------------------------------------------------------------------

    async def _run_control_logic(
        self, force_charge: bool, smoothed_floored: int, raw_floored: int,
        smoothed_a: float, now: datetime,
    ) -> None:
        """Evaluate and schedule control actions with hysteresis and rate limiting."""
        force_changed = force_charge != self._force_charge_prev

        if force_changed:
            self._force_charge_prev = force_charge
            if force_charge:
                self._cancel_pending()
                self._decision_reason = "start_force"
                self._pending_task = self.hass.async_create_task(
                    self._debounced(5, self._action_start_force),
                    eager_start=False,
                )
                return
            else:
                self._cancel_pending()
                self._decision_reason = "stop_force"
                self._pending_task = self.hass.async_create_task(
                    self._debounced(3, self._action_stop_force),
                    eager_start=False,
                )
                return

        if force_charge:
            # Already handling in force mode
            return

        capped = int(min(self._max_current_limit, MAX_CURRENT_ABS))

        if not self._charging_on:
            # --- Not charging: evaluate start condition ---
            # Enforce minimum off-time
            if self._last_stop_time:
                off_elapsed = (now - self._last_stop_time).total_seconds()
                if off_elapsed < self._min_off_time:
                    self._decision_reason = "hold:min_off_time"
                    # Cancel any pending start while in min_off_time
                    if self._pending_action == "start":
                        self._cancel_pending()
                    return

            if smoothed_floored >= self._min_start_current:
                # Only schedule start if not already pending
                if self._pending_action != "start":
                    self._cancel_pending()
                    self._pending_action = "start"
                    self._decision_reason = "pending:start"
                    self._pending_task = self.hass.async_create_task(
                        self._debounced(
                            self._start_delay,
                            self._action_start_surplus,
                            smoothed_floored,
                        ),
                        eager_start=False,
                    )
            else:
                # Below start threshold, cancel any pending start
                if self._pending_action == "start":
                    self._cancel_pending()
                    self._decision_reason = "cancelled:below_start_threshold"
        else:
            # --- Currently charging in surplus mode ---
            if self._current_mode != MODE_SURPLUS:
                return

            if smoothed_a < 1.0:
                # --- Stop condition ---
                # Enforce minimum on-time (stop still allowed if forced)
                if self._last_start_time:
                    on_elapsed = (now - self._last_start_time).total_seconds()
                    if on_elapsed < self._min_on_time:
                        self._decision_reason = "hold:min_on_time"
                        return

                # Only schedule stop if not already pending
                if self._pending_action != "stop":
                    self._cancel_pending()
                    self._cancel_pending_modulate()
                    self._pending_action = "stop"
                    self._decision_reason = "pending:stop"
                    self._pending_task = self.hass.async_create_task(
                        self._debounced(self._stop_delay, self._action_stop_surplus),
                        eager_start=False,
                    )
            else:
                # Cancel any pending stop since conditions improved
                if self._pending_action == "stop":
                    self._cancel_pending()
                    self._decision_reason = "cancelled:surplus_recovered"

                # --- Modulation with hysteresis + cooldown + max step ---
                diff = smoothed_a - self._applied_current
                if abs(diff) < self._hysteresis_band:
                    # Within hysteresis band — no change needed
                    return

                # Check cooldown
                if self._last_change_time:
                    cooldown_elapsed = (now - self._last_change_time).total_seconds()
                    if cooldown_elapsed < self._cooldown_period:
                        self._decision_reason = "hold:cooldown"
                        return

                # Calculate target with max step size
                if diff > 0:
                    target = min(
                        self._applied_current + self._max_step_size,
                        smoothed_floored,
                    )
                else:
                    target = max(
                        self._applied_current - self._max_step_size,
                        smoothed_floored,
                    )

                target = min(max(target, 1), capped)

                if target != self._applied_current:
                    if self._pending_modulate_task is None or self._pending_modulate_task.done():
                        direction = "upscale" if target > self._applied_current else "downscale"
                        self._decision_reason = f"pending:{direction}"
                        self._pending_modulate_task = self.hass.async_create_task(
                            self._debounced(
                                self._modulate_min_interval,
                                self._action_modulate,
                                target,
                            ),
                            eager_start=False,
                        )

        self._last_raw_floored = raw_floored

    def _check_sample_coherency(self) -> bool:
        """Check if net-power and EV-power samples are coherent (not stale)."""
        if self._net_power_mode == MODE_CONSUMPTION_PRODUCTION:
            power_entities = [
                e for e in [self._consumption_sensor, self._production_sensor] if e
            ]
        else:
            power_entities = [self._net_power_sensor] if self._net_power_sensor else []

        if self._ev_power_sensor:
            power_entities.append(self._ev_power_sensor)

        if len(power_entities) < 2:
            return True  # Only one source, no cross-check possible

        timestamps = []
        for eid in power_entities:
            state = self.hass.states.get(eid)
            if state is None or state.state in ("unavailable", "unknown", ""):
                return False
            timestamps.append(state.last_updated)

        gap = (max(timestamps) - min(timestamps)).total_seconds()
        return gap <= self._stale_threshold

    def _cancel_pending(self) -> None:
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        self._pending_task = None
        self._pending_action = None

    def _cancel_pending_modulate(self) -> None:
        if self._pending_modulate_task and not self._pending_modulate_task.done():
            self._pending_modulate_task.cancel()
        self._pending_modulate_task = None

    async def _debounced(self, delay: int, action, *args) -> None:
        """Wait delay seconds then execute action.

        If this task is cancelled during the sleep the action will not run —
        this is intentional: callers cancel pending tasks before scheduling
        a replacement, so cancellation during delay means the action is no
        longer relevant.
        """
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await action(*args)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _action_start_force(self) -> None:
        _LOGGER.info("Stormbreaker: start_force")
        await self._set_charge_current(MAX_CURRENT_ABS)
        await asyncio.sleep(5)
        await self._enable_charging()
        self._charging_on = True
        self._current_mode = MODE_FORCE
        self._applied_current = MAX_CURRENT_ABS
        self._last_start_time = datetime.now()
        self._last_change_time = self._last_start_time
        self._last_action = "start_force"
        self._decision_reason = "start_force"

    async def _action_stop_force(self) -> None:
        _LOGGER.info("Stormbreaker: stop_force")
        await self._disable_charging()
        self._charging_on = False
        self._current_mode = MODE_STOPPED
        self._applied_current = 0
        self._last_stop_time = datetime.now()
        self._last_action = "stop_force"
        self._decision_reason = "stop_force"
        await asyncio.sleep(10)
        await self._set_charge_current(MAX_CURRENT_ABS)

    async def _action_start_surplus(self, current_a: int) -> None:
        _LOGGER.info("Stormbreaker: start_surplus at %dA", current_a)
        await self._set_charge_current(current_a)
        await asyncio.sleep(5)
        await self._enable_charging()
        self._charging_on = True
        self._current_mode = MODE_SURPLUS
        self._applied_current = current_a
        self._last_start_time = datetime.now()
        self._last_change_time = self._last_start_time
        self._last_action = f"start_surplus_{current_a}A"
        self._decision_reason = f"start_surplus_{current_a}A"
        self._pending_action = None

    async def _action_stop_surplus(self) -> None:
        _LOGGER.info("Stormbreaker: stop_surplus")
        await self._disable_charging()
        self._charging_on = False
        self._current_mode = MODE_STOPPED
        self._applied_current = 0
        self._last_stop_time = datetime.now()
        self._last_action = "stop_surplus"
        self._decision_reason = "stop_surplus"
        self._pending_action = None
        await asyncio.sleep(10)
        await self._set_charge_current(MAX_CURRENT_ABS)

    async def _action_modulate(self, target_a: int) -> None:
        if target_a > 0:
            _LOGGER.debug("Stormbreaker: modulate to %dA", target_a)
            await self._set_charge_current(target_a)
            self._applied_current = target_a
            self._last_change_time = datetime.now()
            self._last_action = f"modulate_{target_a}A"
            self._decision_reason = f"modulate_{target_a}A"

    async def _action_plug_in_delayed(self, force_charge: bool, smoothed_floored: int) -> None:
        await asyncio.sleep(2)
        if force_charge:
            await self._action_start_force()
        elif smoothed_floored >= self._min_start_current:
            await self._action_start_surplus(smoothed_floored)
        else:
            await self._action_stop_surplus()

    # ------------------------------------------------------------------
    # Actuator helpers
    # ------------------------------------------------------------------

    async def _set_charge_current(self, current_a: int) -> None:
        """Set the charge current on the configured number entity."""
        if self._charge_current_number:
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": self._charge_current_number, "value": current_a},
                    blocking=True,
                )
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to set charge current: %s", exc)

    async def _enable_charging(self) -> None:
        """Enable charging via configured switch or virtual state."""
        self._charging_enabled = True
        if self._charge_switch:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": self._charge_switch},
                    blocking=True,
                )
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to enable charging: %s", exc)

    async def _disable_charging(self) -> None:
        """Disable charging via configured switch or virtual state."""
        self._charging_enabled = False
        if self._charge_switch:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": self._charge_switch},
                    blocking=True,
                )
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to disable charging: %s", exc)

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------

    async def async_service_force_start(self) -> None:
        """Service: force start charging."""
        self._charge_now = True
        self._cancel_pending()
        self._pending_task = self.hass.async_create_task(
            self._action_start_force(), eager_start=False
        )

    async def async_service_force_stop(self) -> None:
        """Service: force stop charging."""
        self._charge_now = False
        self._cancel_pending()
        self._pending_task = self.hass.async_create_task(
            self._action_stop_force(), eager_start=False
        )

    async def async_service_set_desired_range(self, range_km: float) -> None:
        """Service: set desired range."""
        self._desired_range = range_km

    async def async_service_enable_tonight(self) -> None:
        """Service: enable charge tonight."""
        self._charge_tonight = True

    async def async_service_disable_tonight(self) -> None:
        """Service: disable charge tonight."""
        self._charge_tonight = False

    # ------------------------------------------------------------------
    # Public setters (used by switch/number entities)
    # ------------------------------------------------------------------

    def set_charge_now(self, value: bool) -> None:
        """Set the charge_now flag."""
        self._charge_now = value

    def set_charge_tonight(self, value: bool) -> None:
        """Set the charge_tonight flag."""
        self._charge_tonight = value

    def set_charging_enabled(self, value: bool) -> None:
        """Set the charging_enabled virtual state."""
        self._charging_enabled = value

    def set_desired_range(self, value: float) -> None:
        """Set the desired range in km."""
        self._desired_range = value

    def set_max_current_limit(self, value: float) -> None:
        """Set the max current limit in A."""
        self._max_current_limit = value
