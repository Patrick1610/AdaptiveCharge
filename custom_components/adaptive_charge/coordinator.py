"""Coordinator for AdaptiveCharge."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .alignment import (
    AlignmentEngine,
    EMAFilter,
    MeasurementTracker,
    compute_adaptive_skew_threshold,
    compute_coherence,
    compute_confidence,
    compute_skew,
)
from .helpers import format_duration
from .storage import AdaptiveChargeStore
from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SENSOR,
    CONF_CABLE_SENSOR,
    CONF_CHARGING_PRIORITY,
    CONF_EV_BATTERY_ENERGY_SENSOR,
    CONF_EV_ENERGY_ADDED_SENSOR,
    CONF_CHARGE_LIMIT_NUMBER,
    CONF_CHARGE_LIMIT_SENSOR,
    CONF_DEFAULT_CHARGE_LIMIT,
    CONF_FORECAST_SENSORS,
    CONF_CHARGE_BUFFER,
    CONF_CHARGE_CURRENT_NUMBER,
    CONF_CHARGE_SWITCH,
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENT_RANGE_SENSOR,
    CONF_DESIRED_RANGE,
    CONF_EV_POWER_SENSOR,
    CONF_INVERT_NET_POWER,
    CONF_LOW_POWER_FORECAST_THRESHOLD_KWH,
    CONF_LOW_POWER_THRESHOLD,
    CONF_MAX_CURRENT_LIMIT,
    CONF_MIN_CURRENT_LIMIT,
    CONF_HYSTERESIS_DOWN,
    CONF_HYSTERESIS_UP,
    CONF_IMPORT_GUARD_CLEAR_DURATION_S,
    CONF_IMPORT_GUARD_DURATION,
    CONF_IMPORT_GUARD_HYSTERESIS_W,
    CONF_IMPORT_GUARD_SETTLE_S,
    CONF_IMPORT_GUARD_THRESHOLD,
    CONF_MAX_STEP_A,
    CONF_MODULATE_MIN_INTERVAL,
    CONF_NET_POWER_MODE,
    CONF_NET_POWER_SENSOR,
    CONF_NIGHT_OFF_HOUR,
    CONF_NIGHT_OFF_MINUTE,
    CONF_PRESENCE_ENTITY,
    CONF_PRODUCTION_SENSOR,
    CONF_RANGE_HYSTERESIS_PCT,
    CONF_SAMPLE_INTERVAL,
    CONF_SETTLING_DURATION_S,
    CONF_SMOOTHING_WINDOW,
    CONF_SOLAR_DONE_DURATION,
    CONF_SOLAR_DONE_THRESHOLD,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_SENSORS,
    CONF_START_DELAY,
    CONF_STOP_DELAY,
    CONF_SURPLUS_START_THRESHOLD_A,
    CONF_SURPLUS_STOP_THRESHOLD_A,
    CONF_TONIGHT_START_HOUR,
    CONF_TONIGHT_START_MINUTE,
    CONF_VOLTAGE_SENSOR,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DEFAULT_ALIGNMENT_TIMEOUT_MAX,
    DEFAULT_ALIGNMENT_TIMEOUT_MIN,
    DEFAULT_CHARGE_BUFFER,
    DEFAULT_CHARGE_LIMIT,
    DEFAULT_DESIRED_RANGE,
    DEFAULT_EMA_SPAN_S,
    DEFAULT_EV_STEP_THRESHOLD_W,
    DEFAULT_HYSTERESIS_DOWN,
    DEFAULT_HYSTERESIS_UP,
    DEFAULT_IMPORT_GUARD_CLEAR_DURATION_S,
    DEFAULT_IMPORT_GUARD_DURATION_S,
    DEFAULT_IMPORT_GUARD_HYSTERESIS_W,
    DEFAULT_IMPORT_GUARD_SETTLE_S,
    DEFAULT_IMPORT_GUARD_THRESHOLD_W,
    DEFAULT_IMPORT_GUARD_ZERO_HOLD_S,
    DEFAULT_IMPORT_SAFETY_DURATION_S,
    DEFAULT_IMPORT_SAFETY_THRESHOLD_W,
    DEFAULT_MAX_CURRENT_LIMIT,
    DEFAULT_MAX_STEP_A,
    DEFAULT_MIN_CURRENT_LIMIT,
    DEFAULT_RANGE_HYSTERESIS_PCT,
    DEFAULT_SURPLUS_START_THRESHOLD_A,
    DEFAULT_SURPLUS_STOP_THRESHOLD_A,
    DEFAULT_TONIGHT_REENTRY_CURRENT_A,
    DEFAULT_TONIGHT_START_HOUR,
    DEFAULT_TONIGHT_START_MINUTE,
    DEFAULT_NIGHT_OFF_HOUR,
    DEFAULT_NIGHT_OFF_MINUTE,
    DEFAULT_LOW_POWER_FORECAST_THRESHOLD_KWH,
    DEFAULT_LOW_POWER_THRESHOLD,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_MIN_OFF_TIME_S,
    DEFAULT_MIN_ON_TIME_S,
    DEFAULT_MIN_SWITCH_TOGGLE_INTERVAL_S,
    DEFAULT_MODULATE_MIN_INTERVAL,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_SETTLING_DURATION_S,
    DEFAULT_SMOOTHING_WINDOW,
    DEFAULT_SOLAR_DONE_DURATION,
    DEFAULT_SOLAR_DONE_THRESHOLD,
    DEFAULT_START_DELAY,
    DEFAULT_STOP_DELAY,
    DOMAIN,
    IMPORT_GUARD_OK,
    IMPORT_GUARD_REDUCING,
    IMPORT_GUARD_STOPPED,
    MODE_CONSUMPTION_PRODUCTION,
    MODE_FORCE,
    MODE_NET_ONLY,
    MODE_STOPPED,
    MODE_SURPLUS,
    CONF_PRIORITY_BIAS_W,
    DEFAULT_PRIORITY_BIAS_W,
    PRIORITY_BALANCE,
    PRIORITY_EXPORT,
    PRIORITY_IMPORT,
    PRIORITY_ZERO_PREFER_EXPORT,
    PRIORITY_ZERO_PREFER_IMPORT,
)

_LOGGER = logging.getLogger(__name__)

MAX_CURRENT_ABS = 16

# Minimum tracker samples before lag can fall back to 0.0 (warmup threshold)
_LAG_WARMUP_SAMPLES = 5

# Maximum time delta (hours) for energy accumulation; skip if exceeded (missed ticks)
_MAX_ENERGY_DT_HOURS = 0.1  # 6 minutes

# Ignore tiny solar noise when computing solar-production-based ratios.
_SOLAR_RATIO_MIN_POWER_W = 50.0



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


class AdaptiveChargeCoordinator(DataUpdateCoordinator):
    """Coordinator managing EV charge control logic."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Initialise the coordinator."""
        self._entry = entry
        self._entry_id = entry.entry_id
        options = {**entry.data, **entry.options}

        self._net_power_mode: str = options.get(CONF_NET_POWER_MODE, MODE_NET_ONLY)
        self._net_power_sensor: str | None = options.get(CONF_NET_POWER_SENSOR)
        self._invert_net_power: bool = bool(options.get(CONF_INVERT_NET_POWER, False))
        self._consumption_sensor: str | None = options.get(CONF_CONSUMPTION_SENSOR)
        self._production_sensor: str | None = options.get(CONF_PRODUCTION_SENSOR)
        self._ev_power_sensor: str | None = options.get(CONF_EV_POWER_SENSOR)
        self._voltage_sensor: str | None = options.get(CONF_VOLTAGE_SENSOR)
        self._presence_entity: str | None = options.get(CONF_PRESENCE_ENTITY)
        self._cable_sensor: str | None = options.get(CONF_CABLE_SENSOR)
        self._current_range_sensor: str | None = options.get(CONF_CURRENT_RANGE_SENSOR)
        self._battery_sensor: str | None = options.get(CONF_BATTERY_SENSOR)
        self._ev_battery_energy_sensor: str | None = options.get(CONF_EV_BATTERY_ENERGY_SENSOR)
        self._ev_energy_added_sensor: str | None = options.get(CONF_EV_ENERGY_ADDED_SENSOR)
        self._charge_limit_sensor: str | None = options.get(CONF_CHARGE_LIMIT_SENSOR)
        self._charge_limit_number: str | None = options.get(CONF_CHARGE_LIMIT_NUMBER)
        self._default_charge_limit: int = int(options.get(CONF_DEFAULT_CHARGE_LIMIT, DEFAULT_CHARGE_LIMIT))
        self._forecast_sensors: list[str] = options.get(CONF_FORECAST_SENSORS, []) or []
        # Multi-select solar sensors with backward compat for old single-sensor config
        solar_sensors = options.get(CONF_SOLAR_SENSORS, []) or []
        old_solar = options.get(CONF_SOLAR_SENSOR)
        if not solar_sensors and old_solar:
            solar_sensors = [old_solar]
        self._solar_sensors: list[str] = solar_sensors
        self._charge_switch: str | None = options.get(CONF_CHARGE_SWITCH)
        self._charge_current_number: str | None = options.get(CONF_CHARGE_CURRENT_NUMBER)

        self._smoothing_window: int = int(options.get(CONF_SMOOTHING_WINDOW, DEFAULT_SMOOTHING_WINDOW))
        self._sample_interval: int = int(options.get(CONF_SAMPLE_INTERVAL, DEFAULT_SAMPLE_INTERVAL))
        self._solar_done_threshold_w: float = float(options.get(CONF_SOLAR_DONE_THRESHOLD, DEFAULT_SOLAR_DONE_THRESHOLD))
        self._solar_done_duration: int = int(options.get(CONF_SOLAR_DONE_DURATION, DEFAULT_SOLAR_DONE_DURATION))
        self._start_delay: int = int(options.get(CONF_START_DELAY, DEFAULT_START_DELAY))
        self._stop_delay: int = int(options.get(CONF_STOP_DELAY, DEFAULT_STOP_DELAY))
        self._modulate_min_interval: int = int(options.get(CONF_MODULATE_MIN_INTERVAL, DEFAULT_MODULATE_MIN_INTERVAL))
        self._import_guard_threshold: float = float(
            options.get(CONF_IMPORT_GUARD_THRESHOLD, DEFAULT_IMPORT_GUARD_THRESHOLD_W)
        )
        self._import_guard_duration: float = float(
            options.get(CONF_IMPORT_GUARD_DURATION, DEFAULT_IMPORT_GUARD_DURATION_S)
        )
        self._import_guard_hysteresis: float = float(
            options.get(CONF_IMPORT_GUARD_HYSTERESIS_W, DEFAULT_IMPORT_GUARD_HYSTERESIS_W)
        )
        self._import_guard_clear_duration: float = float(
            options.get(CONF_IMPORT_GUARD_CLEAR_DURATION_S, DEFAULT_IMPORT_GUARD_CLEAR_DURATION_S)
        )
        self._import_guard_settle: float = float(
            options.get(CONF_IMPORT_GUARD_SETTLE_S, DEFAULT_IMPORT_GUARD_SETTLE_S)
        )

        # Expert mode: controller tuning (fall back to defaults if not configured)
        self._max_step_a: float = float(options.get(CONF_MAX_STEP_A, DEFAULT_MAX_STEP_A))
        self._hysteresis_up: float = float(options.get(CONF_HYSTERESIS_UP, DEFAULT_HYSTERESIS_UP))
        self._hysteresis_down: float = float(options.get(CONF_HYSTERESIS_DOWN, DEFAULT_HYSTERESIS_DOWN))
        self._settling_duration_s: float = float(options.get(CONF_SETTLING_DURATION_S, DEFAULT_SETTLING_DURATION_S))
        self._priority_bias_w: float = float(options.get(CONF_PRIORITY_BIAS_W, DEFAULT_PRIORITY_BIAS_W))

        # Mutable runtime state
        self._desired_range: float = float(options.get(CONF_DESIRED_RANGE, DEFAULT_DESIRED_RANGE))
        self._max_current_limit: float = float(options.get(CONF_MAX_CURRENT_LIMIT, DEFAULT_MAX_CURRENT_LIMIT))
        self._min_current_limit: float = float(options.get(CONF_MIN_CURRENT_LIMIT, DEFAULT_MIN_CURRENT_LIMIT))
        self._surplus_start_threshold_a: float = float(
            options.get(CONF_SURPLUS_START_THRESHOLD_A, DEFAULT_SURPLUS_START_THRESHOLD_A)
        )
        self._surplus_stop_threshold_a: float = float(
            options.get(CONF_SURPLUS_STOP_THRESHOLD_A, DEFAULT_SURPLUS_STOP_THRESHOLD_A)
        )
        self._charge_buffer: float = float(options.get(CONF_CHARGE_BUFFER, DEFAULT_CHARGE_BUFFER))
        self._range_hysteresis_pct: float = float(options.get(CONF_RANGE_HYSTERESIS_PCT, DEFAULT_RANGE_HYSTERESIS_PCT))
        self._tonight_start_hour: int = int(options.get(CONF_TONIGHT_START_HOUR, DEFAULT_TONIGHT_START_HOUR))
        self._tonight_start_minute: int = int(options.get(CONF_TONIGHT_START_MINUTE, DEFAULT_TONIGHT_START_MINUTE))
        self._night_off_hour: int = int(options.get(CONF_NIGHT_OFF_HOUR, DEFAULT_NIGHT_OFF_HOUR))
        self._night_off_minute: int = int(options.get(CONF_NIGHT_OFF_MINUTE, DEFAULT_NIGHT_OFF_MINUTE))
        self._low_power_threshold: float = float(
            options.get(CONF_LOW_POWER_THRESHOLD, DEFAULT_LOW_POWER_THRESHOLD)
        )
        self._low_power_forecast_threshold_kwh: float = float(
            options.get(CONF_LOW_POWER_FORECAST_THRESHOLD_KWH, DEFAULT_LOW_POWER_FORECAST_THRESHOLD_KWH)
        )
        self._battery_capacity_kwh: float = float(
            options.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        )

        self._charge_now: bool = False
        self._charge_tonight: bool = False
        self._charging_enabled: bool = False

        # Master controller switch — default OFF for safe first install
        self._controller_enabled: bool = False

        # Charging priority mode — controls current bias and import guard behaviour
        self._charging_priority: str = PRIORITY_BALANCE

        # Control state
        self._charging_on: bool = False
        self._current_mode: str = MODE_STOPPED
        self._last_action: str = ""
        self._last_reason: str = ""
        self._target_current: float = 0.0
        self._last_raw_floored: int = 0
        self._pending_task: asyncio.Task | None = None
        self._pending_modulate_task: asyncio.Task | None = None
        self._pending_plugin_task: asyncio.Task | None = None
        self._force_charge_prev: bool = False
        self._force_source: str = ""
        self._need_active: bool = False
        self._tonight_reason: str = ""
        self._tonight_reentry: bool = False
        self._cable_prev: bool | None = None

        # Timestamps for diagnostics
        self._last_action_ts: float | None = None
        self._last_current_set_ts: float | None = None
        self._last_switch_toggle_ts: float | None = None

        # Solar done tracking
        self._solar_below_threshold_since: datetime | None = None
        self._solar_done: bool = False

        # --- Alignment engine ---
        self._net_tracker = MeasurementTracker("net_power")
        self._ev_tracker = MeasurementTracker("ev_power")
        self._voltage_tracker = MeasurementTracker("voltage")
        self._alignment = AlignmentEngine(
            ev_step_threshold_w=DEFAULT_EV_STEP_THRESHOLD_W,
            timeout_min_s=DEFAULT_ALIGNMENT_TIMEOUT_MIN,
            timeout_max_s=DEFAULT_ALIGNMENT_TIMEOUT_MAX,
        )
        self._ema_filter = EMAFilter(span_s=DEFAULT_EMA_SPAN_S)

        # --- Controller stabilization state ---
        self._committed_current: float | None = None
        self._last_committed_int: int | None = None
        self._last_up_time: float | None = None
        self._last_down_time: float | None = None
        self._last_on_time: float | None = None
        self._last_off_time: float | None = None
        self._confidence: str = CONFIDENCE_LOW
        self._last_commit_reason: str = ""

        # Import guard state (enhanced with hysteresis + escalation)
        self._import_exceed_since: float | None = None
        self._import_guard_active: bool = False
        self._import_guard_state: str = IMPORT_GUARD_OK
        self._import_guard_reason: str = ""
        self._import_guard_state_since: float | None = None
        self._import_below_since: float | None = None
        self._import_guard_last_reduce_time: float | None = None
        self._import_guard_zero_since: float | None = None  # when we first held at 0A
        self._ev_zero_since: float | None = None  # when EV power first read 0 while _charging_on

        # Previous values for step detection
        self._prev_ev_w: float | None = None
        self._prev_net_w: float | None = None
        self._prev_solar_done: bool = False

        # Mode tracking (reason / source / timestamps)
        self._mode_reason: str = "initializing"
        self._mode_source: str = "startup"
        self._mode_since: str = datetime.now().isoformat()
        self._last_transition: str = ""

        # --- Energy accumulation ---
        self._energy_total_wh: float = 0.0
        self._energy_solar_wh: float = 0.0
        self._energy_import_wh: float = 0.0
        self._energy_session_wh: float = 0.0
        self._energy_session_solar_wh: float = 0.0
        self._energy_session_import_wh: float = 0.0
        self._solar_production_wh: float = 0.0
        self._last_energy_mono: float | None = None

        # --- Solar capture factor (operational control) ---
        # Rolling EMA of (session_solar_wh / session_solar_production_wh) per
        # completed charging session.  Used by low-power forecast logic instead
        # of the lifetime solar-to-EV ratio KPI, so short-term capture
        # efficiency (clouds, shading, panel degradation) is reflected faster.
        self._solar_capture_factor: float = 0.0
        # Per-session solar production accumulation for capture factor.
        self._session_solar_production_wh: float = 0.0

        # --- EV battery-side energy tracking ---
        # Snapshot of EV battery energy remaining at session start (kWh).
        self._session_start_battery_kwh: float | None = None
        # Last battery sensor value seen — used to detect when it changes so we
        # can snapshot the wall energy at that exact moment (see below).
        self._prev_battery_energy_kwh: float | None = None
        # Wall energy (session Wh) captured the last time the battery sensor
        # reported a new value.  _compute_overhead_pct uses this snapshot rather
        # than the live _energy_session_wh to avoid a sawtooth:
        #   • Wall energy accumulates every 10 s (HA integration tick)
        #   • Battery sensor polls the car API every ~3 min
        # Without the snapshot, overhead creeps up ~0.1 pp/10 s between battery
        # updates then snaps down 2–3 pp when the battery finally reports a new
        # reading — a perfectly regular sawtooth visible in the HA history graph.
        # Using the wall value at the moment the battery changed gives a matched
        # pair, so the displayed overhead is stable between car API polls.
        self._session_battery_wall_snapshot_wh: float = 0.0
        # Rolling overhead tracking (wall energy vs battery-received).
        self._overhead_total_wall_wh: float = 0.0
        self._overhead_total_battery_wh: float = 0.0

        # --- Battery capacity auto-detection ---
        # SoC recorded when cable is plugged in, used to compute session estimate.
        self._session_start_soc: float | None = None
        # EMA estimate derived from past sessions (kWh as seen at the EV power
        # sensor, so it already includes AC→battery charging losses).
        # 0.0 means no estimate available yet.
        self._estimated_battery_capacity_kwh: float = 0.0

        # Persistent counter store
        self._store = AdaptiveChargeStore(hass, entry.entry_id)

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

    @property
    def store(self) -> AdaptiveChargeStore:
        """Expose persistent counter store for sensor access."""
        return self._store

    async def async_config_entry_first_refresh(self) -> None:
        """Set up interval and do first refresh."""
        # Load persistent counters from .storage
        await self._store.async_load()
        # Restore battery capacity estimate from the store
        stored_estimate = self._store.get("battery_capacity_estimate_kwh")
        if stored_estimate > 0:
            self._estimated_battery_capacity_kwh = stored_estimate
        # Restore overhead totals from the store
        self._overhead_total_wall_wh = self._store.get("overhead_wall_wh")
        self._overhead_total_battery_wh = self._store.get("overhead_battery_wh")
        # Restore energy counters from the store so that sensors report their
        # correct value in the very first tick rather than briefly showing 0.
        # Without this, TOTAL_INCREASING sensors (e.g. Energy Charged) would
        # momentarily drop to 0 on reload, causing HA utility meters to
        # incorrectly count the recovery as new energy.
        stored_energy_total = self._store.get("energy_total_wh")
        if stored_energy_total > 0:
            self._energy_total_wh = stored_energy_total
            self._energy_solar_wh = self._store.get("energy_solar_wh")
            self._energy_import_wh = self._store.get("energy_import_wh")
        # Restore solar production total so the solar_production_kwh attribute
        # is also correct from the first tick (used by SolarToEvRatioSensor).
        self._solar_production_wh = self._store.get("solar_production_total_wh")
        # Restore rolling solar capture factor for low-power forecast logic.
        stored_capture = self._store.get("solar_capture_factor")
        if stored_capture > 0:
            self._solar_capture_factor = stored_capture
        await self._async_tick(None)
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_tick,
            timedelta(seconds=self._sample_interval),
        )
        self._schedule_night_off()

    def _schedule_night_off(self) -> None:
        """Schedule the nightly charge_tonight reset at configured time."""
        if self._unsub_night_off:
            self._unsub_night_off()
        self._unsub_night_off = async_track_time_change(
            self.hass,
            self._async_night_off,
            hour=self._night_off_hour,
            minute=self._night_off_minute,
            second=0,
        )

    @callback
    def _async_night_off(self, _now) -> None:
        """Turn off charge_tonight at configured night-off time."""
        _LOGGER.info(
            "AdaptiveCharge: Night-Off — disabling charge_tonight at %02d:%02d",
            self._night_off_hour, self._night_off_minute,
        )
        self._tonight_reason = f"auto_off: {self._night_off_hour:02d}:{self._night_off_minute:02d} reset"
        self._charge_tonight = False
        self._schedule_night_off()
        self.hass.async_create_task(
            self.async_request_refresh(), eager_start=False
        )

    async def async_shutdown(self) -> None:
        """Cancel subscriptions and flush persistent store."""
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
        # If charging was active on reload/restart, disable the charger so it
        # does not remain enabled after the integration restarts.  Without this
        # the physical charger switch stays ON while _charging_on is reset to
        # False, causing the first "not charging" status after restart to be
        # silently ignored and leaving the EV in an unmanaged charging state.
        if self._charging_on:
            _LOGGER.info(
                "AdaptiveCharge: shutdown — disabling charger (was charging_on=True)"
            )
            await self._disable_charging()
            self._charging_on = False
            self._last_committed_int = None
        # Best-effort session finalization on shutdown so capacity/overhead/
        # capture factor are persisted even if the cable is never unplugged.
        self._finalize_session_if_needed("shutdown")
        # Flush persistent counters to disk on shutdown
        await self._store.async_save()

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

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
        mono_now = time.monotonic()

        # --- Phase 1: Read & convert sensors ---
        sensor_data = self._read_sensors()

        # --- Phase 2: Alignment, EMA, confidence ---
        analysis = self._analyze_measurements(sensor_data, mono_now)

        # --- Phase 3: Solar done ---
        self._evaluate_solar_done(sensor_data["solar_w"], now)

        # --- Phase 4: Charge tonight auto-off ---
        self._evaluate_tonight_auto_off(sensor_data["cable_connected"])

        # --- Phase 5: Force charge / need / tonight condition ---
        # Compute solar-to-EV ratio from persistent store totals
        store_solar_production_wh = self._store.get("solar_production_total_wh")
        store_energy_solar_wh = self._store.get("energy_solar_wh")
        solar_to_ev_ratio: float | None = None
        if store_solar_production_wh > 0:
            solar_to_ev_ratio = min(store_energy_solar_wh / store_solar_production_wh, 1.0)

        force_data = self._evaluate_force_charge(
            sensor_data["current_range"],
            sensor_data["presence"],
            sensor_data["cable_connected"],
            sensor_data.get("battery_pct"),
            sensor_data.get("forecast_kwh"),
            solar_to_ev_ratio,
        )

        # --- Phase 6: Cable plug-in detection ---
        self._detect_cable_plugin(
            sensor_data["cable_connected"],
            force_data["force_charge"],
            analysis["ema_current_a"],
        )

        # --- Phase 7: Import guard ---
        import_guard_triggered = self._check_import_guard(
            sensor_data["computed_net_w"], mono_now
        )
        # When explicitly targeting import (PRIORITY_IMPORT), the user accepts
        # grid draw — disable import guard so it does not reduce current.
        if self._charging_priority == PRIORITY_IMPORT:
            import_guard_triggered = False

        # --- Phase 7b: Energy accumulation ---
        self._accumulate_energy(sensor_data, mono_now)

        # --- Phase 7c: Stale charge detection ---
        self._detect_stale_charge(sensor_data["ev_w"], mono_now)

        # --- Phase 8: Control logic ---
        if self._controller_enabled:
            # Apply priority current bias for zero_prefer_import mode.
            # The bias is added ONLY to the current used by the control logic —
            # surplus_w and the displayed ema_current_a are NOT affected, so
            # the real power scenario remains visible in monitoring.
            control_ema = analysis["ema_current_a"]
            bias_a = self._get_priority_current_bias_a()
            if bias_a != 0.0:
                capped_limit = min(self._max_current_limit, MAX_CURRENT_ABS)
                control_ema = min(max(control_ema + bias_a, 0.0), capped_limit)
            await self._run_control_logic(
                force_charge=force_data["force_charge"],
                raw_floored=analysis["raw_floored"],
                ema_current=control_ema,
                mono_now=mono_now,
                import_safety=import_guard_triggered,
                coherence=analysis["coherence"],
                control_reason=analysis["control_reason"],
                cable_connected=sensor_data["cable_connected"],
                net_power_valid=sensor_data["net_power_valid"],
            )

        self._last_raw_floored = analysis["raw_floored"]
        capped_limit = min(self._max_current_limit, MAX_CURRENT_ABS)
        self._target_current = min(max(analysis["ema_current_a"], 0.0), capped_limit)

        # During force charge, charger power is NOT available surplus current
        if force_data["force_charge"]:
            display_ema = 0.0
            display_available = 0.0
        else:
            display_ema = round(analysis["ema_current_a"], 2)
            display_available = round(analysis["ema_current_a"], 2)

        # --- Phase 9: Build data dict ---
        data = self._build_data_dict(
            sensor_data, analysis, force_data, display_ema, display_available, now,
            solar_to_ev_ratio,
        )

        self.async_set_updated_data(data)
        return data

    # ------------------------------------------------------------------
    # Phase 1: Read & convert sensors
    # ------------------------------------------------------------------

    def _read_sensors(self) -> dict[str, Any]:
        """Read raw sensor values and convert units."""
        net_raw = _get_float_state(self.hass, self._net_power_sensor)
        consumption_raw = _get_float_state(self.hass, self._consumption_sensor)
        production_raw = _get_float_state(self.hass, self._production_sensor)
        ev_raw = _get_float_state(self.hass, self._ev_power_sensor)
        voltage_raw = _get_float_state(self.hass, self._voltage_sensor)
        # Sum multiple solar sensors (backward compat: list may contain single entry)
        solar_raw: float | None = None
        for sid in self._solar_sensors:
            val = _get_float_state(self.hass, sid)
            if val is not None:
                converted = _to_watts(val, sid, self.hass)
                solar_raw = (solar_raw or 0.0) + converted
        presence = _get_bool_state(self.hass, self._presence_entity)
        cable_connected = _get_bool_state(self.hass, self._cable_sensor)
        current_range = _get_float_state(self.hass, self._current_range_sensor)
        battery_pct = _get_float_state(self.hass, self._battery_sensor)
        charge_limit_pct = _get_float_state(self.hass, self._charge_limit_sensor)
        ev_battery_energy_kwh = _get_float_state(self.hass, self._ev_battery_energy_sensor)
        ev_energy_added_kwh = _get_float_state(self.hass, self._ev_energy_added_sensor)

        # Sum all remaining-forecast sensor values (kWh remaining today)
        forecast_kwh: float | None = None
        for fid in self._forecast_sensors:
            val = _get_float_state(self.hass, fid)
            if val is not None:
                forecast_kwh = (forecast_kwh or 0.0) + val

        net_w = _to_watts(net_raw, self._net_power_sensor, self.hass) if net_raw is not None else None
        consumption_w = _to_watts(consumption_raw, self._consumption_sensor, self.hass) if consumption_raw is not None else None
        production_w = _to_watts(production_raw, self._production_sensor, self.hass) if production_raw is not None else None
        ev_w = _to_watts(ev_raw, self._ev_power_sensor, self.hass) if ev_raw is not None else 0.0
        voltage = voltage_raw if voltage_raw is not None else 230.0
        # solar_w already summed and converted above
        solar_w = solar_raw

        # Determine net power validity: True when the underlying sensor(s)
        # provided a real numeric value, False when the value is unknown/
        # unavailable and we are falling back to 0 W.
        net_power_valid: bool
        if self._net_power_mode == MODE_NET_ONLY:
            net_power_valid = net_w is not None
            computed_net_w = net_w if net_w is not None else 0.0
            # Apply invert if configured (flip sign for sensors with reversed convention)
            if self._invert_net_power:
                computed_net_w = -computed_net_w
        else:
            if consumption_w is not None and production_w is not None:
                computed_net_w = consumption_w - production_w
                net_power_valid = True
            elif consumption_w is not None:
                computed_net_w = consumption_w
                net_power_valid = True
            else:
                computed_net_w = 0.0
                net_power_valid = False

        return {
            "computed_net_w": computed_net_w,
            "net_power_valid": net_power_valid,
            "ev_w": ev_w,
            "voltage": voltage,
            "solar_w": solar_w,
            "presence": presence,
            "cable_connected": cable_connected,
            "current_range": current_range,
            "battery_pct": battery_pct,
            "charge_limit_pct": charge_limit_pct,
            "forecast_kwh": forecast_kwh,
            "ev_battery_energy_kwh": ev_battery_energy_kwh,
            "ev_energy_added_kwh": ev_energy_added_kwh,
        }

    # ------------------------------------------------------------------
    # Phase 2: Alignment, EMA, confidence
    # ------------------------------------------------------------------

    def _get_priority_current_bias_a(self) -> float:
        """Return current bias (A) to add to EMA current for control logic only.

        Applied AFTER the surplus/EMA calculation so surplus_w and the displayed
        ema_current_a remain clean for monitoring purposes.

          balance             → 0 A (default — aim for exactly 0 W net, pure surplus)
          zero_prefer_import  → +bias A: controller starts/modulates even when the
                                actual surplus is slightly below zero, targeting
                                ~priority_bias_w of deliberate grid import.
          zero_prefer_export  → −bias A: controller requires an actual surplus above
                                the bias before starting/modulating, targeting
                                ~priority_bias_w of grid export above the start point.
          export_priority     → 0 A (surplus charging blocked in control logic)
          import_priority     → 0 A (handled via force charge path, not bias)
        """
        bias_a = self._priority_bias_w / (230.0 * 3.0)
        if self._charging_priority == PRIORITY_ZERO_PREFER_IMPORT:
            return bias_a
        if self._charging_priority == PRIORITY_ZERO_PREFER_EXPORT:
            return -bias_a
        return 0.0

    def _analyze_measurements(
        self, sensor_data: dict[str, Any], mono_now: float
    ) -> dict[str, Any]:
        """Update trackers, compute EMA, confidence, and control reason."""
        computed_net_w = sensor_data["computed_net_w"]
        ev_w = sensor_data["ev_w"]
        voltage = sensor_data["voltage"]

        self._net_tracker.update(computed_net_w, mono_now)
        self._ev_tracker.update(ev_w, mono_now)
        self._voltage_tracker.update(voltage, mono_now)

        self._alignment.on_ev_power_change(self._prev_ev_w, ev_w, mono_now)
        net_delta = computed_net_w - self._prev_net_w if self._prev_net_w is not None else 0.0
        self._alignment.on_net_power_update(mono_now, net_delta)
        self._alignment.check_timeout(mono_now)
        self._alignment.check_settling(mono_now)

        skew = compute_skew(self._net_tracker, self._ev_tracker)
        coherence = compute_coherence(self._net_tracker, self._ev_tracker)

        self._prev_ev_w = ev_w
        self._prev_net_w = computed_net_w

        surplus_w = (0.0 - computed_net_w) + ev_w
        raw_current_a = (surplus_w / (voltage * 3.0)) if voltage > 0 else 0.0
        capped = min(self._max_current_limit, MAX_CURRENT_ABS)

        ema_current_a = self._ema_filter.update(raw_current_a, mono_now)
        ema_current_a = min(max(ema_current_a, 0.0), capped)

        raw_floored = min(max(int(raw_current_a), 0), int(capped))

        # Debug: log surplus vs. committed current to diagnose ramp-up lag
        # (morning "unused power" is caused by modulation rate limits, not
        # start threshold — the charger is already ON but not stepping up
        # fast enough due to cooldown / hysteresis / settling / alignment).
        if self._charging_on and self._committed_current is not None:
            gap = ema_current_a - self._committed_current
            if gap >= self._hysteresis_up:
                _LOGGER.debug(
                    "AdaptiveCharge: ramp-up headroom — "
                    "surplus=%.0fW ema=%.2fA committed=%.1fA gap=+%.2fA "
                    "voltage=%.1fV confidence=%s",
                    surplus_w, ema_current_a, self._committed_current,
                    gap, voltage, self._confidence,
                )

        self._confidence = compute_confidence(
            net_tracker=self._net_tracker,
            ev_tracker=self._ev_tracker,
            alignment_active=self._alignment.active,
            target_current=ema_current_a,
            last_committed=self._committed_current,
            sample_interval=float(self._sample_interval),
            settling=self._alignment.settling,
        )

        control_reason = ""
        if coherence < 0.3:
            control_reason = "low_coherence"
        elif self._alignment.active:
            control_reason = "alignment_active"
        elif self._alignment.settling:
            control_reason = "settling_window"

        skew_threshold = compute_adaptive_skew_threshold(self._net_tracker, self._ev_tracker)
        if skew is None:
            alignment_ok = False
            alignment_reason = "no data"
        elif skew > skew_threshold:
            alignment_ok = False
            alignment_reason = "skew too high"
        elif (
            self._net_tracker.staleness is not None
            and self._net_tracker.staleness > float(self._sample_interval) * 3.0
        ):
            alignment_ok = False
            alignment_reason = "net stale"
        elif (
            self._ev_tracker.staleness is not None
            and self._ev_tracker.staleness > float(self._sample_interval) * 3.0
        ):
            alignment_ok = False
            alignment_reason = "ev stale"
        else:
            alignment_ok = True
            alignment_reason = "ok"

        return {
            "surplus_w": surplus_w,
            "raw_current_a": raw_current_a,
            "raw_floored": raw_floored,
            "ema_current_a": ema_current_a,
            "coherence": coherence,
            "skew": skew,
            "control_reason": control_reason,
            "alignment_ok": alignment_ok,
            "alignment_reason": alignment_reason,
        }

    # ------------------------------------------------------------------
    # Phase 3: Solar done
    # ------------------------------------------------------------------

    def _evaluate_solar_done(self, solar_w: float | None, now: datetime) -> None:
        """Update solar_done state based on solar sensor."""
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
            self._solar_done = True

    # ------------------------------------------------------------------
    # Phase 4: Charge tonight auto-off
    # ------------------------------------------------------------------

    def _evaluate_tonight_auto_off(self, cable_connected: bool | None) -> None:
        """Check auto-off triggers for charge_tonight."""
        if (
            self._charge_tonight
            and self._cable_prev is not None
            and cable_connected is not None
            and self._cable_prev
            and not cable_connected
        ):
            _LOGGER.info("AdaptiveCharge: charge_tonight auto-off — cable unplugged")
            self._tonight_reason = "auto_off: cable unplugged"
            self._charge_tonight = False

        if (
            self._charge_tonight
            and self._prev_solar_done
            and not self._solar_done
        ):
            _LOGGER.info("AdaptiveCharge: charge_tonight auto-off — solar_done ended")
            self._tonight_reason = "auto_off: solar_done ended"
            self._charge_tonight = False

        self._prev_solar_done = self._solar_done

    # ------------------------------------------------------------------
    # Phase 5: Force charge / need / tonight evaluation
    # ------------------------------------------------------------------

    def _evaluate_force_charge(
        self,
        current_range: float | None,
        presence: bool | None,
        cable_connected: bool | None,
        battery_pct: float | None = None,
        forecast_kwh: float | None = None,
        solar_to_ev_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate force charge, need, tonight condition, and earliest-start gate.

        Asymmetric hysteresis: the upper limit is exactly effective_range
        (desired + buffer) and the hysteresis band extends only downward.
          start threshold = effective_range - hysteresis_km
          stop  threshold = effective_range

        Both buffer and hysteresis are percentages of desired_range:
          effective_range = desired_range × (1 + buffer%)
          hysteresis_km   = desired_range × hysteresis%
        This keeps them directly comparable so the constraint hyst ≤ buffer
        (enforced in the config flow) guarantees the start threshold never
        drops below desired_range.

        Low power protection: when the vehicle battery SoC is below
        ``_low_power_threshold`` and the solar forecast is insufficient (or
        unavailable) to cover the shortfall, force charging is activated
        regardless of the surplus/tonight conditions.

        The **primary** method uses the lifetime solar-to-EV ratio to estimate
        how much of the remaining forecast will actually reach the car:
          energy_needed   = (threshold − battery_pct) / 100 × effective_capacity
          expected_ev_kwh = forecast_kwh × solar_to_ev_ratio
          force_charge    = expected_ev_kwh < energy_needed

        Capacity evaluation uses the best available source in priority order:
          1. Manually configured ``_battery_capacity_kwh`` (config flow, > 0)
          2. Auto-detected ``_estimated_battery_capacity_kwh`` (derived from
             wall-measured energy ÷ SoC delta, so it already includes AC→battery
             charging losses — no separate efficiency factor needed)

        When no capacity or ratio is available, the optional
        ``_low_power_forecast_threshold_kwh`` serves as a **backup**: if > 0,
        the forecast is compared against this fixed threshold. When set to 0
        (the default) and the precise mode cannot run, a conservative approach
        is taken and force charging is activated to protect the battery.
        """
        effective_range = self._desired_range * (1.0 + self._charge_buffer / 100.0)
        hysteresis_km = self._desired_range * (self._range_hysteresis_pct / 100.0)
        prev_need_active = self._need_active
        if current_range is not None:
            if self._need_active:
                # Stop when range reaches effective_range (desired + buffer)
                self._need_active = current_range < effective_range
            else:
                # Start when range drops below effective_range - hysteresis
                self._need_active = current_range < (effective_range - hysteresis_km)
        else:
            self._need_active = False
        need = self._need_active

        tonight_reentry = (
            not prev_need_active
            and self._need_active
            and self._force_charge_prev
            and self._force_source == "charge_tonight"
        )
        if tonight_reentry:
            self._tonight_reentry = True

        # Earliest start gate: tonight only triggers after configured start hour.
        # Handles overnight wrapping: if start=22:00 and night_off=05:00,
        # then 23:00 and 01:00 are both valid (after start OR before night_off).
        now_time = datetime.now()
        current_minutes = now_time.hour * 60 + now_time.minute
        start_minutes = self._tonight_start_hour * 60 + self._tonight_start_minute
        off_minutes = self._night_off_hour * 60 + self._night_off_minute
        if start_minutes >= off_minutes:
            # Overnight window (e.g. 22:00–05:00): valid if after start OR before off
            after_start = current_minutes >= start_minutes or current_minutes < off_minutes
        else:
            # Same-day window (e.g. 08:00–17:00): valid if between start and off
            after_start = start_minutes <= current_minutes < off_minutes
        tonight_condition = (
            self._charge_tonight
            and bool(presence)
            and bool(cable_connected)
            and need
            and self._solar_done
            and after_start
        )

        # Low power protection: force charge when SoC is below threshold and
        # either (a) we are inside the charge-tonight window, or (b) the solar
        # forecast does not promise enough generation to handle it.
        # A threshold of 0 disables the feature entirely.
        low_power_active = False
        if (
            self._low_power_threshold > 0
            and battery_pct is not None
            and battery_pct < self._low_power_threshold
            and bool(presence)
            and bool(cable_connected)
        ):
            if after_start:
                # Inside the tonight window → always force charge to protect
                # the battery; solar is not expected during this period.
                low_power_active = True
            elif forecast_kwh is None:
                # No forecast at all → force charge
                low_power_active = True
            else:
                # Resolve effective capacity: manual config takes priority,
                # then fall back to the auto-detected estimate.
                effective_capacity = (
                    self._battery_capacity_kwh
                    if self._battery_capacity_kwh > 0
                    else self._estimated_battery_capacity_kwh
                )
                # Keep forecast logic simple and explainable: use the
                # lifetime solar-to-EV ratio directly.
                control_factor = solar_to_ev_ratio if solar_to_ev_ratio is not None else 0.0
                if (
                    effective_capacity > 0
                    and control_factor > 0
                ):
                    # Precise mode: wall-energy-based capacity already includes
                    # charging losses, so energy_needed is pre-overhead.
                    energy_needed_kwh = max(
                        0.0,
                        (self._low_power_threshold - battery_pct) / 100.0 * effective_capacity,
                    )
                    expected_ev_kwh = forecast_kwh * control_factor
                    low_power_active = expected_ev_kwh < energy_needed_kwh
                else:
                    # Fallback: use manual kWh threshold if configured, otherwise
                    # conservative force charge to protect the battery.
                    if self._low_power_forecast_threshold_kwh > 0:
                        low_power_active = forecast_kwh < self._low_power_forecast_threshold_kwh
                    else:
                        low_power_active = True

        force_charge = self._charge_now or tonight_condition or low_power_active
        # import_priority acts like a permanent charge_now — force charge whenever
        # the cable is connected (regardless of solar, range, or tonight schedule).
        import_priority_force = (
            self._charging_priority == PRIORITY_IMPORT
            and bool(cable_connected)
        )
        force_charge = force_charge or import_priority_force
        if force_charge:
            if self._charge_now:
                self._force_source = "charge_now_switch"
            elif tonight_condition:
                self._force_source = "charge_tonight"
            elif import_priority_force:
                self._force_source = "import_priority"
            else:
                self._force_source = "low_power"

        return {
            "effective_range": effective_range,
            "hysteresis_km": hysteresis_km,
            "need": need,
            "tonight_condition": tonight_condition,
            "low_power_active": low_power_active,
            "force_charge": force_charge,
        }

    # ------------------------------------------------------------------
    # Energy accumulation
    # ------------------------------------------------------------------

    def _accumulate_energy(
        self,
        sensor_data: dict[str, Any],
        mono_now: float,
    ) -> None:
        """Accumulate energy charged (split solar/import)."""
        if self._last_energy_mono is None:
            self._last_energy_mono = mono_now
            return

        dt_h = (mono_now - self._last_energy_mono) / 3600.0
        self._last_energy_mono = mono_now

        if dt_h <= 0 or dt_h > _MAX_ENERGY_DT_HOURS:
            # Skip if dt is unreasonable (> 6 minutes = missed ticks)
            return

        ev_w = sensor_data.get("ev_w", 0.0) or 0.0
        computed_net_w = sensor_data.get("computed_net_w", 0.0) or 0.0

        # --- Solar production accumulation ---
        solar_w_val = sensor_data.get("solar_w")
        if solar_w_val is not None and solar_w_val > _SOLAR_RATIO_MIN_POWER_W:
            solar_production_wh = solar_w_val * dt_h
            self._solar_production_wh += solar_production_wh
            self._session_solar_production_wh += solar_production_wh
            self._store.add_solar_production(solar_production_wh)

        # --- Energy charged accumulation ---
        if self._charging_on and ev_w > 0:
            energy_wh = ev_w * dt_h
            self._energy_total_wh += energy_wh
            self._energy_session_wh += energy_wh

            # Split: if net_w > 0, grid is importing → that portion is import energy
            # solar fraction = ev_w - net_w (clamped to [0, ev_w])
            if computed_net_w > 0:
                import_portion = min(ev_w, computed_net_w)
                solar_portion = max(ev_w - computed_net_w, 0.0)
            else:
                # Net exporting: all EV power is solar-sourced
                import_portion = 0.0
                solar_portion = ev_w

            solar_wh = solar_portion * dt_h
            import_wh = import_portion * dt_h
            self._energy_solar_wh += solar_wh
            self._energy_import_wh += import_wh
            self._energy_session_solar_wh += solar_wh
            self._energy_session_import_wh += import_wh
            # Persist energy charged
            self._store.add_energy_charged(energy_wh, solar_wh, import_wh)

    def _compute_energy_needed_full_kwh(
        self,
        battery_pct: float | None,
        charging_overhead_pct: float | None,
    ) -> float | None:
        """Estimate wall energy still needed for 100% SoC (incl. overhead)."""
        if battery_pct is None:
            return None

        effective_capacity = (
            self._battery_capacity_kwh
            if self._battery_capacity_kwh > 0
            else self._estimated_battery_capacity_kwh
        )
        if effective_capacity <= 0:
            return None

        # Remaining capacity expressed using the same basis as effective_capacity.
        remaining_battery_kwh = max(0.0, (100.0 - battery_pct) / 100.0 * effective_capacity)
        if remaining_battery_kwh <= 0:
            return 0.0

        # If no overhead is provided, or we are using an estimated capacity that may already
        # be wall-side (including charging losses), return the remaining energy as-is.
        if charging_overhead_pct is None:
            return round(remaining_battery_kwh, 2)

        used_config_capacity = self._battery_capacity_kwh > 0
        if not used_config_capacity:
            # Effective capacity is estimated and may already include overhead; avoid
            # double-counting by not applying the efficiency factor again.
            return round(remaining_battery_kwh, 2)

        efficiency = max(0.05, 1.0 - (charging_overhead_pct / 100.0))
        return round(remaining_battery_kwh / efficiency, 2)

    def reset_session_energy(self) -> None:
        """Reset per-session energy counters (called on cable plug-in)."""
        self._energy_session_wh = 0.0
        self._energy_session_solar_wh = 0.0
        self._energy_session_import_wh = 0.0
        self._session_solar_production_wh = 0.0
        # Snapshot the current SoC as the session baseline for capacity estimation.
        self._session_start_soc = _get_float_state(self.hass, self._battery_sensor)
        # Snapshot EV battery energy remaining for battery-side delta tracking.
        self._session_start_battery_kwh = _get_float_state(self.hass, self._ev_battery_energy_sensor)
        # Reset battery-snapshot tracking so _compute_overhead_pct starts fresh.
        self._prev_battery_energy_kwh = self._session_start_battery_kwh
        self._session_battery_wall_snapshot_wh = 0.0

    def _compute_session_battery_delta(self) -> float | None:
        """Return the battery-side energy delta for the current session (kWh).

        Uses the EV battery energy remaining sensor: end − start snapshot.
        Returns None if the sensor is not configured or currently unavailable.

        The start snapshot is captured lazily: if it was never set (e.g. the
        integration restarted while the cable was already connected, or the
        sensor was temporarily unavailable at actual plug-in time) the first
        valid sensor reading becomes the new baseline and 0.0 is returned.

        Side-effect: whenever the sensor reports a new value, the current
        session wall energy (_energy_session_wh) is captured in
        _session_battery_wall_snapshot_wh.  _compute_overhead_pct uses this
        snapshot instead of the live wall total to prevent a sawtooth pattern
        (see _session_battery_wall_snapshot_wh docstring in __init__).
        """
        current = _get_float_state(self.hass, self._ev_battery_energy_sensor)
        if current is None:
            return None
        # Lazily capture snapshot when not yet set (restart / sensor-not-ready
        # at plug-in time).  Return 0.0 for this tick; subsequent ticks will
        # produce the real delta.
        if self._session_start_battery_kwh is None:
            self._session_start_battery_kwh = current
            self._prev_battery_energy_kwh = current
            self._session_battery_wall_snapshot_wh = self._energy_session_wh
            return 0.0
        # Capture a matched wall-energy snapshot whenever the battery sensor
        # reports a new reading.  This ensures _compute_overhead_pct always
        # uses a (battery, wall) pair measured at the same instant rather than
        # a stale battery value paired with an ever-growing wall accumulator.
        if current != self._prev_battery_energy_kwh:
            self._prev_battery_energy_kwh = current
            self._session_battery_wall_snapshot_wh = self._energy_session_wh
        delta = current - self._session_start_battery_kwh
        return round(max(delta, 0.0), 2)

    def _compute_overhead_pct(self, session_battery_delta: float | None = None) -> float | None:
        """Return the rolling charging overhead percentage.

        overhead% = (1 − battery_received / wall_energy) × 100

        Uses lifetime totals from the persistent store.  When the cable is
        currently connected and at least CAPACITY_MIN_ENERGY_KWH has been
        charged in the current session, the in-progress session data is blended
        in so the metric updates live during charging rather than only at
        session end.  The current session is excluded once the cable
        disconnects (``_cable_prev`` becomes False) to prevent double-counting
        when the same session data is later committed to the store.

        The live blend uses _session_battery_wall_snapshot_wh (the wall energy
        captured at the last battery sensor change) rather than the live
        _energy_session_wh accumulator.  This prevents a ~2 pp sawtooth that
        would otherwise appear every time the car API delivers a new battery
        reading: between API polls (typically ~3 min) the wall total grows
        every 10 s while the battery value is frozen, driving the ratio higher
        until the next battery update snaps it back down.  By using the wall
        snapshot taken at the same instant as the battery reading, both sides
        of the ratio advance together and the displayed value stays stable.

        Args:
            session_battery_delta: Pre-computed battery delta for the current
                session (kWh).  When provided, avoids a redundant sensor read
                in _compute_session_battery_delta.

        Returns None when insufficient data.
        """
        wall = self._store.get("overhead_wall_wh")
        battery = self._store.get("overhead_battery_wh")

        # Live blend: add current session's partial data while charging.
        # _cable_prev holds the most recently observed cable state — updated
        # by _detect_cable_plugin (Phase 6) before _build_data_dict calls this.
        if self._cable_prev:
            # Use the wall snapshot from the last battery sensor change rather
            # than the live accumulator (see docstring above).
            session_wall_wh = self._session_battery_wall_snapshot_wh
            if (
                session_battery_delta is not None
                and session_wall_wh / 1000.0 >= self._CAPACITY_MIN_ENERGY_KWH
                and session_battery_delta >= self._CAPACITY_MIN_ENERGY_KWH
            ):
                wall += session_wall_wh
                battery += session_battery_delta * 1000.0

        if wall > 0 and battery > 0:
            return round(max(0.0, (1.0 - battery / wall)) * 100.0, 1)
        return None

    # ------------------------------------------------------------------
    # Phase 6: Cable plug-in detection
    # ------------------------------------------------------------------

    def _detect_cable_plugin(
        self,
        cable_connected: bool | None,
        force_charge: bool,
        ema_current_a: float,
    ) -> None:
        """Detect cable plug-in/disconnect events and schedule delayed action."""
        if (
            self._controller_enabled
            and cable_connected is not None
            and self._cable_prev is not None
            and cable_connected != self._cable_prev
        ):
            if cable_connected and not self._cable_prev:
                if self._pending_plugin_task and not self._pending_plugin_task.done():
                    self._pending_plugin_task.cancel()
                self._pending_plugin_task = self.hass.async_create_task(
                    self._action_plug_in_delayed(force_charge, ema_current_a),
                    eager_start=False,
                )
            elif not cable_connected and self._cable_prev:
                # Cable disconnected — stop charging if active, then reset
                # current to max so the EVSE is ready for the next session
                # (safe: no spike risk since the car is physically gone).
                self._cancel_pending()
                self._pending_task = self.hass.async_create_task(
                    self._debounced(0, self._action_cable_disconnected),
                    eager_start=False,
                )
        if cable_connected is not None:
            self._cable_prev = cable_connected

    # EV_ZERO_STALE_S: seconds of zero EV power (while charging_on) before
    # the coordinator declares the car stopped independently.
    _EV_ZERO_STALE_S: float = 60.0

    def _detect_stale_charge(self, ev_w: float, mono_now: float) -> None:
        """Reset _charging_on if the car has stopped drawing power for a sustained period.

        Covers the case where the car independently stops charging (e.g. reaches
        its charge limit, user disables charging via the car app) — the coordinator
        would otherwise keep _charging_on=True indefinitely.
        """
        if not self._charging_on:
            self._ev_zero_since = None
            return

        if ev_w > 0:
            self._ev_zero_since = None
            return

        # ev_w == 0 while we think we're charging
        if self._ev_zero_since is None:
            self._ev_zero_since = mono_now
            return

        if (mono_now - self._ev_zero_since) >= self._EV_ZERO_STALE_S:
            _LOGGER.info(
                "AdaptiveCharge: EV power has been 0 for %.0fs while charging_on=True "
                "— resetting to stopped (car stopped independently)",
                mono_now - self._ev_zero_since,
            )
            self._charging_on = False
            self._ev_zero_since = None
            self._set_mode(MODE_STOPPED, "car_stopped_independently", "auto_rule")
            self._last_action = "stale_charge_reset"
            self._last_reason = "car_stopped_independently"
            self._last_action_ts = mono_now
            self._last_off_time = mono_now
            self._committed_current = None
            self._last_committed_int = None
            self._last_commit_reason = "stale_charge_reset"
            self._finalize_session_if_needed("stale_charge_reset")

    # ------------------------------------------------------------------
    # Session finalizer
    # ------------------------------------------------------------------

    # EMA weight for the solar capture factor (same as capacity EMA).
    _CAPTURE_FACTOR_EMA_ALPHA: float = 0.3

    def _finalize_session_if_needed(self, trigger: str) -> None:
        """Central session finalization: capacity estimate, overhead, capture factor.

        Idempotent — only runs when there is meaningful session data
        (session_start_soc set, session energy > 0).

        Triggers: cable_disconnect, stale_charge_reset, shutdown.
        """
        if self._session_start_soc is None and self._energy_session_wh <= 0:
            return

        _LOGGER.info(
            "AdaptiveCharge: finalizing session (trigger=%s, "
            "session_wh=%.1f, session_solar_wh=%.1f)",
            trigger, self._energy_session_wh, self._energy_session_solar_wh,
        )

        # 1. Update battery capacity estimate + overhead
        self._update_capacity_estimate()

        # 2. Update rolling solar capture factor
        if (
            self._session_solar_production_wh > 0
            and self._energy_session_solar_wh >= 0
        ):
            session_factor = min(
                self._energy_session_solar_wh / self._session_solar_production_wh, 1.0
            )
            if self._solar_capture_factor > 0:
                self._solar_capture_factor = (
                    self._CAPTURE_FACTOR_EMA_ALPHA * session_factor
                    + (1.0 - self._CAPTURE_FACTOR_EMA_ALPHA) * self._solar_capture_factor
                )
            else:
                self._solar_capture_factor = session_factor
            self._store.set_solar_capture_factor(self._solar_capture_factor)
            _LOGGER.info(
                "AdaptiveCharge: solar capture factor updated to %.4f "
                "(session=%.4f, trigger=%s)",
                self._solar_capture_factor, session_factor, trigger,
            )

    # ------------------------------------------------------------------
    # Build data dict
    # ------------------------------------------------------------------

    def _build_data_dict(
        self,
        sensor_data: dict[str, Any],
        analysis: dict[str, Any],
        force_data: dict[str, Any],
        display_ema: float,
        display_available: float,
        now: datetime,
        solar_to_ev_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Assemble the coordinator data dict."""
        computed_net_w = sensor_data["computed_net_w"]
        skew = analysis["skew"]
        # Compute session battery delta ONCE to avoid double sensor reads
        # with side-effects (snapshot capture).
        session_battery_delta = self._compute_session_battery_delta()
        charging_overhead_pct = self._compute_overhead_pct(session_battery_delta)
        energy_needed_full_kwh = self._compute_energy_needed_full_kwh(
            sensor_data.get("battery_pct"),
            charging_overhead_pct,
        )
        return {
            "net_w": computed_net_w,
            "net_power_valid": sensor_data.get("net_power_valid", True),
            "ev_w": sensor_data["ev_w"],
            "voltage": sensor_data["voltage"],
            "surplus_w": analysis["surplus_w"],
            "raw_current_a": analysis["raw_current_a"],
            "raw_floored": analysis["raw_floored"],
            "ema_current_a": display_ema,
            "solar_w": sensor_data["solar_w"],
            "solar_done": self._solar_done,
            "force_charge": force_data["force_charge"],
            "force_source": self._force_source if force_data["force_charge"] else "",
            "low_power_active": force_data["low_power_active"],
            "low_power_threshold_pct": self._low_power_threshold,
            "low_power_forecast_threshold_kwh": self._low_power_forecast_threshold_kwh,
            "presence": sensor_data["presence"],
            "cable_connected": sensor_data["cable_connected"],
            "current_range": sensor_data["current_range"],
            "battery_pct": sensor_data.get("battery_pct"),
            "charge_limit_pct": sensor_data.get("charge_limit_pct"),
            "forecast_kwh": sensor_data.get("forecast_kwh"),
            "desired_range": self._desired_range,
            "effective_range": force_data["effective_range"],
            "range_hysteresis_pct": self._range_hysteresis_pct,
            "range_hysteresis_km": round(force_data["hysteresis_km"], 1),
            "charge_buffer": self._charge_buffer,
            "max_current_limit": self._max_current_limit,
            "min_current_limit": self._min_current_limit,
            "surplus_start_threshold_a": self._surplus_start_threshold_a,
            "surplus_stop_threshold_a": self._surplus_stop_threshold_a,
            "charge_now": self._charge_now,
            "charge_tonight": self._charge_tonight,
            "tonight_condition": force_data["tonight_condition"],
            "tonight_reason": self._tonight_reason,
            "tonight_reentry": self._tonight_reentry,
            "tonight_start_time": f"{self._tonight_start_hour:02d}:{self._tonight_start_minute:02d}",
            "night_off_time": f"{self._night_off_hour:02d}:{self._night_off_minute:02d}",
            "need": force_data["need"],
            "charging_enabled": self._charging_enabled,
            "controller_enabled": self._controller_enabled,
            "charging_active": self._charging_on,
            "current_mode": self._current_mode,
            "last_action": self._last_action,
            "last_reason": self._last_reason,
            "target_current": round(self._target_current, 2),
            "current_setting": self._last_committed_int,
            "available_current": display_available,
            "charging_on": self._charging_on,
            "sample_count": self._net_tracker.interval_count,
            "last_updated": now.isoformat(),
            # --- Import guard ---
            "import_guard_active": self._import_guard_active,
            "import_guard_state": self._import_guard_state,
            "import_guard_reason": self._import_guard_reason,
            "import_watts": max(computed_net_w, 0.0),
            "time_in_import_state": format_duration(
                time.monotonic() - self._import_guard_state_since
                if self._import_guard_state_since is not None
                else 0.0
            ),
            # --- Mode tracking ---
            "mode_reason": self._mode_reason,
            "mode_source": self._mode_source,
            "mode_since": self._mode_since,
            "last_transition": self._last_transition,
            # --- Timestamps ---
            "last_action_ts": self._last_action_ts,
            "last_current_set_ts": self._last_current_set_ts,
            "last_switch_toggle_ts": self._last_switch_toggle_ts,
            # --- Alignment & coherence diagnostics ---
            "alignment_ok": analysis["alignment_ok"],
            "alignment_reason": analysis["alignment_reason"],
            "alignment_active": self._alignment.active,
            "settling_active": self._alignment.settling,
            "confidence_level": self._confidence,
            "measurement_coherence": round(analysis["coherence"], 3),
            "estimated_skew_seconds": (
                round(skew, 3) if skew is not None else None
            ),
            "estimated_lag_seconds": (
                round(self._alignment.estimated_lag, 2)
                if self._alignment.estimated_lag is not None
                else (
                    0.0
                    if (
                        self._net_tracker.interval_count >= _LAG_WARMUP_SAMPLES
                        and self._ev_tracker.interval_count >= _LAG_WARMUP_SAMPLES
                    )
                    else None
                )
            ),
            "net_update_interval_s": (
                round(self._net_tracker.avg_interval, 2)
                if self._net_tracker.avg_interval is not None
                else None
            ),
            "ev_update_interval_s": (
                round(self._ev_tracker.avg_interval, 2)
                if self._ev_tracker.avg_interval is not None
                else None
            ),
            "voltage_update_interval_s": (
                round(self._voltage_tracker.avg_interval, 2)
                if self._voltage_tracker.avg_interval is not None
                else None
            ),
            "net_update_interval_p95": (
                round(self._net_tracker.interval_p95, 2)
                if self._net_tracker.interval_p95 is not None
                else None
            ),
            "ev_update_interval_p95": (
                round(self._ev_tracker.interval_p95, 2)
                if self._ev_tracker.interval_p95 is not None
                else None
            ),
            "last_sample_age_net_s": (
                round(self._net_tracker.sample_age, 2)
                if self._net_tracker.sample_age is not None
                else None
            ),
            "last_sample_age_ev_s": (
                round(self._ev_tracker.sample_age, 2)
                if self._ev_tracker.sample_age is not None
                else None
            ),
            "last_applied_current_a": self._last_committed_int,
            "committed_current": self._committed_current,
            "last_control_reason": self._last_commit_reason,
            # --- Energy accumulation ---
            "energy_total_kwh": round(self._energy_total_wh / 1000.0, 3),
            "energy_solar_kwh": round(self._energy_solar_wh / 1000.0, 3),
            "energy_import_kwh": round(self._energy_import_wh / 1000.0, 3),
            "energy_session_kwh": round(self._energy_session_wh / 1000.0, 3),
            "energy_session_solar_kwh": round(self._energy_session_solar_wh / 1000.0, 3),
            "energy_session_import_kwh": round(self._energy_session_import_wh / 1000.0, 3),
            "solar_production_kwh": round(self._solar_production_wh / 1000.0, 3),
            "battery_capacity_kwh": self._battery_capacity_kwh,
            "estimated_battery_capacity_kwh": round(self._estimated_battery_capacity_kwh, 2),
            # --- Solar-to-EV ratio (lifetime KPI) ---
            "solar_to_ev_ratio": (
                round(solar_to_ev_ratio, 4) if solar_to_ev_ratio is not None else None
            ),
            # --- Solar capture factor (operational control) ---
            "solar_capture_factor": (
                round(self._solar_capture_factor, 4)
                if self._solar_capture_factor > 0 else None
            ),
            # --- EV battery-side metrics ---
            "ev_battery_energy_kwh": sensor_data.get("ev_battery_energy_kwh"),
            "ev_energy_added_kwh": sensor_data.get("ev_energy_added_kwh"),
            "session_battery_delta_kwh": session_battery_delta,
            "charging_overhead_pct": charging_overhead_pct,
            "energy_needed_full_kwh": energy_needed_full_kwh,
            # --- Charging priority ---
            "charging_priority": self._charging_priority,
        }

    # ------------------------------------------------------------------
    # Import guard (configurable fail-safe)
    # ------------------------------------------------------------------

    def _check_import_guard(self, net_w: float, mono_now: float) -> bool:
        """Return True if grid import has exceeded threshold for long enough.

        Uses debounce (require sustained import for N seconds) and hysteresis
        (require import < threshold - margin for M seconds before clearing).
        """
        threshold = self._import_guard_threshold
        duration = self._import_guard_duration
        clear_threshold = threshold - self._import_guard_hysteresis

        if net_w > threshold:
            # Import above threshold — start or continue debounce timer
            self._import_below_since = None  # reset clear timer
            if self._import_exceed_since is None:
                self._import_exceed_since = mono_now
                self._import_guard_reason = "transient spike ignored"
            elif (mono_now - self._import_exceed_since) >= duration:
                self._import_guard_active = True
                elapsed = mono_now - self._import_exceed_since
                self._import_guard_reason = (
                    f"sustained import {elapsed:.0f}s > {threshold:.0f}W"
                )
                if self._import_guard_state == IMPORT_GUARD_OK:
                    self._import_guard_state_since = mono_now
                if self._import_guard_state == IMPORT_GUARD_OK:
                    self._import_guard_state = IMPORT_GUARD_REDUCING
                return True
        elif net_w <= clear_threshold:
            # Import below hysteresis threshold — start clear timer
            self._import_exceed_since = None
            if self._import_below_since is None:
                self._import_below_since = mono_now
            elif (mono_now - self._import_below_since) >= self._import_guard_clear_duration:
                self._import_guard_active = False
                self._import_guard_state = IMPORT_GUARD_OK
                self._import_guard_reason = ""
                self._import_guard_state_since = mono_now
                self._import_below_since = None
                self._import_guard_last_reduce_time = None
                self._import_guard_zero_since = None
        else:
            # Between clear_threshold and threshold — hold current state (dead zone)
            self._import_exceed_since = None
            # Don't reset _import_below_since — allow clearing to continue
            # if it was already below the clear threshold
        return False

    # ------------------------------------------------------------------
    # Controller enable/disable
    # ------------------------------------------------------------------

    async def _async_controller_shutdown_sequence(self) -> None:
        """Shutdown sequence when controller is disabled.

        Policy: if the controller had started charging (_charging_on == True),
        stop charging.  This matches the existing stop behaviour used by
        _action_stop_surplus / _action_stop_force.
        """
        if not self._charging_on:
            return
        _LOGGER.info("AdaptiveCharge: controller disabled — running shutdown sequence")
        self._cancel_pending()
        self._last_reason = "controller_disabled"
        await self._disable_charging()
        self._charging_on = False
        self._set_mode(MODE_STOPPED, "controller_disabled", "user_toggle")
        self._last_action = "controller_disabled_stop"
        self._last_action_ts = time.monotonic()
        self._last_off_time = time.monotonic()
        self._committed_current = None
        self._last_committed_int = None
        self._last_commit_reason = "controller_disabled"
        self._import_guard_zero_since = None

    # ------------------------------------------------------------------
    # Control logic
    # ------------------------------------------------------------------

    async def _run_control_logic(
        self,
        force_charge: bool,
        raw_floored: int,
        ema_current: float,
        mono_now: float,
        import_safety: bool,
        coherence: float,
        control_reason: str,
        cable_connected: bool | None = None,
        net_power_valid: bool = True,
    ) -> None:
        """Evaluate and schedule control actions."""
        force_changed = force_charge != self._force_charge_prev

        if force_changed:
            self._force_charge_prev = force_charge
            if force_charge:
                self._cancel_pending()
                self._pending_task = self.hass.async_create_task(
                    self._debounced(5, self._action_start_force),
                    eager_start=False,
                )
                return
            else:
                self._cancel_pending()
                self._pending_task = self.hass.async_create_task(
                    self._debounced(3, self._action_stop_force),
                    eager_start=False,
                )
                return

        if force_charge:
            return

        # Export priority: block all surplus charging so all solar is exported.
        # Force charge (charge_now / charge_tonight) overrides this — handled above.
        if self._charging_priority == PRIORITY_EXPORT:
            if self._charging_on and self._current_mode == MODE_SURPLUS:
                if self._pending_task is None or self._pending_task.done():
                    self._pending_task = self.hass.async_create_task(
                        self._debounced(self._stop_delay, self._action_stop_surplus),
                        eager_start=False,
                    )
            return

        # --- Import safety: escalation ladder ---
        # Step 1: Reduce current by 1A (soft mitigation)
        # Step 2: Hold / settle window to observe net import improvement
        # Step 3: Reduce to 0A (keep session alive, no power draw)
        # Step 4: Hold at 0A for up to ZERO_HOLD_S before hard stop
        # Step 5: Only then hard stop / charger off (hard mitigation)
        if import_safety and self._charging_on and self._current_mode == MODE_SURPLUS:
            # Respect settle window after last reduction
            if (
                self._import_guard_last_reduce_time is not None
                and (mono_now - self._import_guard_last_reduce_time) < self._import_guard_settle
            ):
                return  # Still in settle window — observe before next action

            current = self._committed_current or 0.0
            if current > 0.0:
                # Escalation: reduce by 1A (soft mitigation)
                new_target = current - 1.0
                new_target = max(new_target, 0.0)
                self._import_guard_last_reduce_time = mono_now
                self._import_guard_state = IMPORT_GUARD_REDUCING
                if new_target == 0.0:
                    self._import_guard_zero_since = mono_now
                await self._commit_current(
                    new_target, mono_now, reason="import_guard_reduce"
                )
                self._last_reason = "import_guard_reduce"
            else:
                # At 0A — hold for ZERO_HOLD_S before escalating to hard stop
                if self._import_guard_zero_since is None:
                    self._import_guard_zero_since = mono_now
                zero_elapsed = mono_now - self._import_guard_zero_since
                if zero_elapsed >= DEFAULT_IMPORT_GUARD_ZERO_HOLD_S:
                    # Held at 0A long enough — escalate to hard stop
                    self._cancel_pending()
                    self._pending_task = self.hass.async_create_task(
                        self._debounced(0, self._action_stop_surplus),
                        eager_start=False,
                    )
                    self._import_guard_state = IMPORT_GUARD_STOPPED
                    self._last_commit_reason = "import_guard_escalate_stop"
                    self._last_reason = "import_guard_escalate_stop"
                    self._last_action_ts = time.monotonic()
                    self._import_guard_zero_since = None
                # else: continue holding at 0A (session alive, no power)
            return

        # --- Start surplus charging ---
        if ema_current >= self._surplus_start_threshold_a and not self._charging_on:
            # Don't start when cable is not confirmed connected (None=unknown or False=disconnected)
            if cable_connected is not True:
                _LOGGER.debug(
                    "AdaptiveCharge: surplus start blocked — cable not confirmed "
                    "connected (cable=%s, ema=%.2fA threshold=%.1fA)",
                    cable_connected, ema_current, self._surplus_start_threshold_a,
                )
                return
            # Don't start when net power sensor is invalid/unavailable
            if not net_power_valid:
                _LOGGER.debug(
                    "AdaptiveCharge: surplus start blocked — net power sensor "
                    "invalid/unavailable (ema=%.2fA threshold=%.1fA)",
                    ema_current, self._surplus_start_threshold_a,
                )
                return
            # Respect min-off time
            if self._last_off_time is not None:
                off_elapsed = mono_now - self._last_off_time
                if off_elapsed < DEFAULT_MIN_OFF_TIME_S:
                    _LOGGER.debug(
                        "AdaptiveCharge: surplus start blocked by min-off-time "
                        "(%.0fs / %.0fs), ema=%.2fA threshold=%.1fA",
                        off_elapsed, DEFAULT_MIN_OFF_TIME_S,
                        ema_current, self._surplus_start_threshold_a,
                    )
                    return
            # Only schedule if not already pending — avoid resetting the
            # debounce timer every tick which would prevent it from completing.
            if self._pending_task is None or self._pending_task.done():
                capped_limit = min(self._max_current_limit, MAX_CURRENT_ABS)
                start_a = max(int(self._surplus_start_threshold_a), min(int(ema_current), int(capped_limit)))
                self._pending_task = self.hass.async_create_task(
                    self._debounced(self._start_delay, self._action_start_surplus, start_a),
                    eager_start=False,
                )
            return

        # --- Stop surplus charging ---
        if ema_current < self._surplus_stop_threshold_a and self._charging_on and self._current_mode == MODE_SURPLUS:
            # Respect min-on time
            if self._last_on_time is not None:
                on_elapsed = mono_now - self._last_on_time
                if on_elapsed < DEFAULT_MIN_ON_TIME_S:
                    return
            # Only schedule if not already pending — avoid resetting the
            # debounce timer every tick which would prevent it from completing.
            if self._pending_task is None or self._pending_task.done():
                self._pending_task = self.hass.async_create_task(
                    self._debounced(self._stop_delay, self._action_stop_surplus),
                    eager_start=False,
                )
            return

        # --- Modulate current (hysteresis + rate limiting) ---
        if (
            self._charging_on
            and self._current_mode == MODE_SURPLUS
            and self._committed_current is not None
        ):
            await self._try_modulate(ema_current, mono_now, net_power_valid=net_power_valid)

    async def _try_modulate(self, ema_current: float, mono_now: float, *, net_power_valid: bool = True) -> None:
        """Apply hysteresis and rate limiting to modulate current."""
        current_setpoint = self._committed_current
        if current_setpoint is None:
            return

        capped = min(self._max_current_limit, MAX_CURRENT_ABS)
        target = min(max(ema_current, 0.0), capped)
        delta = target - current_setpoint

        # During alignment or settling, only allow decreases (safety), hold otherwise
        if (self._alignment.active or self._alignment.settling) and delta > 0:
            _LOGGER.debug(
                "AdaptiveCharge: modulate up blocked by alignment/settling "
                "(alignment=%s settling=%s) delta=+%.2fA",
                self._alignment.active, self._alignment.settling, delta,
            )
            return

        # Confidence gating
        if delta > 0 and self._confidence == CONFIDENCE_LOW:
            _LOGGER.debug(
                "AdaptiveCharge: modulate up blocked by low confidence, delta=+%.2fA",
                delta,
            )
            return

        # Net power validity gating — block upward modulation when net sensor
        # is invalid/unavailable (surplus is unreliable); downward is allowed.
        if delta > 0 and not net_power_valid:
            _LOGGER.debug(
                "AdaptiveCharge: modulate up blocked — net power sensor "
                "invalid/unavailable, delta=+%.2fA",
                delta,
            )
            return

        # Hysteresis check
        if delta > 0 and delta < self._hysteresis_up:
            return
        if delta < 0 and abs(delta) < self._hysteresis_down:
            return

        # Rate limiting: max step per modulation
        step = min(abs(delta), float(self._max_step_a))
        if delta > 0:
            new_target = current_setpoint + step
        else:
            new_target = current_setpoint - step

        new_target = min(max(new_target, 0.0), capped)

        # Cooldown / minimum modulation interval
        if delta > 0 and self._last_up_time is not None:
            up_elapsed = mono_now - self._last_up_time
            up_cooldown = float(max(self._modulate_min_interval, 0))
            if up_elapsed < up_cooldown:
                _LOGGER.debug(
                    "AdaptiveCharge: modulate up blocked by cooldown "
                    "(%.0fs / %.0fs) target=%.1fA",
                    up_elapsed, up_cooldown, new_target,
                )
                return

        reason = "modulate_up" if delta > 0 else "modulate_down"
        await self._commit_current(new_target, mono_now, reason=reason)

    async def _commit_current(
        self, target: float, mono_now: float, reason: str = ""
    ) -> None:
        """Commit a new current setpoint to the actuator."""
        capped = min(self._max_current_limit, MAX_CURRENT_ABS)
        target = min(max(target, 0.0), capped)

        current_int = self._last_committed_int if self._last_committed_int is not None else 0
        # Quantize float target to EVSE integer amps.
        # - Upward modulation: nearest integer (half-up), and ensure +1A step
        #   once modulation has been approved by hysteresis/gating.
        # - Downward modulation: floor (conservative, avoids over-reducing).
        # - Other paths (start/force/import-guard): direct integer truncation.
        if reason == "modulate_up":
            rounded = int(math.floor(target + 0.5))
            target_int = max(current_int + 1, rounded)
        elif reason == "modulate_down":
            target_int = int(math.floor(target))
        else:
            target_int = int(target)

        target_int = min(max(target_int, 0), int(capped))

        # Idempotent: skip if same integer value already sent
        if target_int == self._last_committed_int:
            return

        if target_int >= 0:
            _LOGGER.debug(
                "AdaptiveCharge: commit %dA (float=%.2f, reason=%s, confidence=%s)",
                target_int, target, reason, self._confidence,
            )
            await self._set_charge_current(target_int)
            # Keep committed_current aligned to what was actually sent (integer A).
            self._committed_current = float(target_int)
            self._last_committed_int = target_int
            self._last_commit_reason = reason

            if "up" in reason:
                self._last_up_time = mono_now
            elif "down" in reason:
                self._last_down_time = mono_now

            self._last_action = f"modulate_{target_int}A"
            self._last_reason = reason
            self._last_action_ts = mono_now

            # Start settling window to avoid self-induced dip flapping
            self._alignment.start_settling(
                mono_now, self._settling_duration_s
            )

    def _set_mode(self, new_mode: str, reason: str, source: str) -> None:
        """Update the mode state machine with tracking metadata."""
        prev = self._current_mode
        if new_mode != prev:
            self._last_transition = f"{prev} -> {new_mode}: {reason}"
        self._current_mode = new_mode
        self._mode_reason = reason
        self._mode_source = source
        self._mode_since = datetime.now().isoformat()

    def _cancel_pending(self) -> None:
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        self._pending_task = None

    async def _debounced(self, delay: int, action, *args) -> None:
        """Wait delay seconds then execute action."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await action(*args)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _action_start_force(self) -> None:
        max_a = int(self._max_current_limit)
        source = self._force_source or "unknown"
        # Tonight reentry (range dropped back) uses lower current;
        # charge_now always overrules and uses full power.
        if self._tonight_reentry and source == "charge_tonight":
            start_a = min(DEFAULT_TONIGHT_REENTRY_CURRENT_A, max_a)
            self._tonight_reentry = False
        else:
            start_a = max_a
        _LOGGER.info("AdaptiveCharge: start_force at %dA (source=%s)", start_a, source)
        await self._set_charge_current(start_a)
        await asyncio.sleep(5)
        await self._enable_charging()
        await asyncio.sleep(2)
        # Re-confirm current after enabling — some cars (e.g. Tesla) reset
        # the current to 0 when it is set while the charge switch is off.
        await self._set_charge_current(start_a)
        self._charging_on = True
        self._set_mode(MODE_FORCE, "force_charge_active", source)
        self._last_action = "start_force"
        self._last_reason = "force_charge_active"
        self._last_action_ts = time.monotonic()
        self._last_on_time = time.monotonic()
        self._committed_current = float(start_a)
        self._last_committed_int = start_a
        self._last_commit_reason = "start_force"

    async def _action_stop_force(self) -> None:
        source = self._force_source or "unknown"
        _LOGGER.info("AdaptiveCharge: stop_force (source=%s)", source)
        await self._disable_charging()
        self._charging_on = False
        self._set_mode(MODE_STOPPED, "force_charge_stopped", source)
        self._last_action = "stop_force"
        self._last_reason = "force_charge_stopped"
        self._last_action_ts = time.monotonic()
        self._last_off_time = time.monotonic()
        self._committed_current = None
        self._last_committed_int = None
        self._last_commit_reason = "stop_force"

    async def _action_start_surplus(self, current_a: int) -> None:
        _LOGGER.info("AdaptiveCharge: start_surplus at %dA", current_a)
        await self._set_charge_current(current_a)
        await asyncio.sleep(5)
        await self._enable_charging()
        await asyncio.sleep(2)
        # Re-confirm current after enabling — some cars reset the current
        # when it is set while the charge switch is off.
        await self._set_charge_current(current_a)
        self._charging_on = True
        self._set_mode(MODE_SURPLUS, "surplus_above_threshold", "auto_rule")
        self._last_action = f"start_surplus_{current_a}A"
        self._last_reason = "surplus_above_threshold"
        self._last_action_ts = time.monotonic()
        self._last_on_time = time.monotonic()
        self._committed_current = float(current_a)
        self._last_committed_int = current_a
        self._last_commit_reason = "start_surplus"

    async def _action_stop_surplus(self) -> None:
        _LOGGER.info("AdaptiveCharge: stop_surplus")
        await self._disable_charging()
        self._charging_on = False
        source = "import_guard" if self._import_guard_active else "auto_rule"
        reason = "import_guard_escalate_stop" if self._import_guard_active else "surplus_below_threshold"
        self._set_mode(MODE_STOPPED, reason, source)
        self._last_action = "stop_surplus"
        self._last_reason = reason
        self._last_action_ts = time.monotonic()
        self._last_off_time = time.monotonic()
        self._committed_current = None
        self._last_committed_int = None
        self._last_commit_reason = "stop_surplus"
        self._import_guard_active = False
        self._import_guard_state = IMPORT_GUARD_OK
        self._import_guard_last_reduce_time = None
        self._import_guard_zero_since = None
        self._import_below_since = None
        self._import_exceed_since = None

    async def _action_cable_disconnected(self) -> None:
        """Handle cable disconnection: stop charging if active, then reset current to max.

        Resetting to max is safe here because the car is physically unplugged —
        there is no risk of a current spike. It ensures the EVSE is in a ready
        state for the next session (or for charging elsewhere at full power).
        """
        # Finalize the session (capacity, overhead, capture factor) before
        # energy counters are reset by the next plug-in.
        self._finalize_session_if_needed("cable_disconnect")
        if self._charging_on:
            await self._action_stop_surplus()
        max_a = int(min(self._max_current_limit, MAX_CURRENT_ABS))
        await self._set_charge_current(max_a)
        _LOGGER.info("AdaptiveCharge: cable disconnected — current reset to %dA", max_a)

    # Minimum SoC change (%) and energy (kWh) required for a reliable estimate.
    _CAPACITY_MIN_SOC_DELTA: float = 5.0
    _CAPACITY_MIN_ENERGY_KWH: float = 0.5
    # EMA weight for new session estimates (0.3 = ~3-4 sessions to stabilise).
    _CAPACITY_EMA_ALPHA: float = 0.3
    # Sanity bounds for the estimate (kWh).
    _CAPACITY_MIN_KWH: float = 5.0
    _CAPACITY_MAX_KWH: float = 200.0

    def _update_capacity_estimate(self) -> None:
        """Estimate battery capacity from the just-completed charging session.

        When the EV battery energy sensor is configured, the estimate uses
        direct battery-side kWh delta — this is more accurate than the
        SoC%-based method because:
          • SoC is rounded to whole-percent or 0.5% steps → noisy
          • Battery energy remaining has 0.01 kWh resolution

        As a side-effect, when both wall energy and battery-side delta are
        available, the **charging overhead** (AC→DC losses) is computed and
        accumulated for the rolling overhead percentage.

        Fallback: if the battery energy sensor is unavailable, the legacy
        SoC-based method is used (wall energy / SoC delta × 100).

        The wall-energy-based estimate already includes AC→battery charging
        losses, so ``energy_needed`` in the low-power check is automatically
        pre-overhead — no separate efficiency factor is required.

        Only updates when at least 0.5 kWh was added in the session and either
        the SoC increased by ≥5% or the battery-side kWh delta is ≥0.5 kWh.
        """
        if self._session_start_soc is None:
            return
        end_soc = _get_float_state(self.hass, self._battery_sensor)
        if end_soc is None:
            return
        soc_delta = end_soc - self._session_start_soc
        wall_energy_kwh = self._energy_session_wh / 1000.0

        # --- Battery-side delta (preferred when available) ---
        battery_delta_kwh: float | None = None
        if self._session_start_battery_kwh is not None:
            end_battery = _get_float_state(self.hass, self._ev_battery_energy_sensor)
            if end_battery is not None:
                delta = end_battery - self._session_start_battery_kwh
                if delta >= self._CAPACITY_MIN_ENERGY_KWH:
                    battery_delta_kwh = delta

        # --- Overhead computation ---
        if battery_delta_kwh is not None and wall_energy_kwh >= self._CAPACITY_MIN_ENERGY_KWH:
            self._overhead_total_wall_wh += wall_energy_kwh * 1000.0
            self._overhead_total_battery_wh += battery_delta_kwh * 1000.0
            self._store.add_overhead(wall_energy_kwh * 1000.0, battery_delta_kwh * 1000.0)
            session_overhead_pct = max(0.0, (1.0 - battery_delta_kwh / wall_energy_kwh)) * 100.0
            _LOGGER.info(
                "AdaptiveCharge: session charging overhead %.1f%% "
                "(wall=%.2f kWh, battery=%.2f kWh)",
                session_overhead_pct, wall_energy_kwh, battery_delta_kwh,
            )

        # --- Capacity estimation ---
        # Prefer battery-side estimate when we have both kWh delta and SoC delta.
        if battery_delta_kwh is not None and soc_delta >= self._CAPACITY_MIN_SOC_DELTA:
            # Battery-side: direct kWh ÷ SoC% = true usable capacity
            raw_estimate = (battery_delta_kwh * 100.0) / soc_delta
        elif soc_delta >= self._CAPACITY_MIN_SOC_DELTA and wall_energy_kwh >= self._CAPACITY_MIN_ENERGY_KWH:
            # Fallback: wall energy ÷ SoC% (includes losses)
            raw_estimate = (wall_energy_kwh * 100.0) / soc_delta
        else:
            return

        raw_estimate = max(self._CAPACITY_MIN_KWH, min(self._CAPACITY_MAX_KWH, raw_estimate))

        if self._estimated_battery_capacity_kwh > 0:
            self._estimated_battery_capacity_kwh = (
                self._CAPACITY_EMA_ALPHA * raw_estimate
                + (1.0 - self._CAPACITY_EMA_ALPHA) * self._estimated_battery_capacity_kwh
            )
        else:
            self._estimated_battery_capacity_kwh = raw_estimate

        self._store.set_battery_capacity_estimate(self._estimated_battery_capacity_kwh)
        source = "battery_sensor" if battery_delta_kwh is not None else "wall"
        _LOGGER.info(
            "AdaptiveCharge: battery capacity estimate updated to %.1f kWh "
            "(source=%s, soc_delta=%.1f%%, energy=%.2f kWh, raw=%.1f kWh)",
            self._estimated_battery_capacity_kwh,
            source,
            soc_delta,
            battery_delta_kwh if battery_delta_kwh is not None else wall_energy_kwh,
            raw_estimate,
        )

    async def _action_plug_in_delayed(self, force_charge: bool, ema_current_a: float) -> None:
        # Immediately set current to 0A so the EVSE cannot charge at any rate
        # during the evaluation delay, regardless of its parked/default setting.
        await self._set_charge_current(0)
        await asyncio.sleep(2)
        # Reset per-session energy counters on cable plug-in
        self.reset_session_energy()
        # Re-read current EMA to avoid stale value from 2 seconds ago
        current_ema = self._ema_filter.value if self._ema_filter.value is not None else ema_current_a
        ema_floored = max(int(current_ema), 0)
        if force_charge:
            await self._action_start_force()
        elif ema_floored > 0:
            await self._action_start_surplus(ema_floored)
        else:
            await self._action_stop_surplus()

    # ------------------------------------------------------------------
    # Actuator helpers
    # ------------------------------------------------------------------

    async def _set_charge_current(self, current_a: int) -> bool:
        """Set the charge current on the configured number entity.

        Returns True on success, False on failure.
        """
        if self._charge_current_number:
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": self._charge_current_number, "value": current_a},
                    blocking=True,
                )
                self._last_current_set_ts = time.monotonic()
                return True
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to set charge current: %s", exc)
                return False
        return True

    async def _enable_charging(self) -> bool:
        """Enable charging via configured switch or virtual state.

        Returns True on success, False on failure.
        """
        if self._charge_switch:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": self._charge_switch},
                    blocking=True,
                )
                self._last_switch_toggle_ts = time.monotonic()
                self._charging_enabled = True
                return True
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to enable charging: %s", exc)
                return False
        else:
            self._charging_enabled = True
            return True

    async def _disable_charging(self) -> bool:
        """Disable charging via configured switch or virtual state.

        Returns True on success, False on failure.
        """
        if self._charge_switch:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": self._charge_switch},
                    blocking=True,
                )
                self._last_switch_toggle_ts = time.monotonic()
                self._charging_enabled = False
                return True
            except (HomeAssistantError, ServiceNotFound) as exc:
                _LOGGER.warning("Failed to disable charging: %s", exc)
                return False
        else:
            self._charging_enabled = False
            return True

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------

    async def async_service_force_start(self) -> None:
        """Service: force start charging."""
        if not self._controller_enabled:
            _LOGGER.warning("AdaptiveCharge: force_start ignored — controller disabled")
            return
        self._charge_now = True
        self._cancel_pending()
        self._pending_task = self.hass.async_create_task(
            self._action_start_force(), eager_start=False
        )

    async def async_service_force_stop(self) -> None:
        """Service: force stop charging."""
        if not self._controller_enabled:
            _LOGGER.warning("AdaptiveCharge: force_stop ignored — controller disabled")
            return
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
        self._tonight_reason = "enabled via service"
        self._charge_tonight = True

    async def async_service_disable_tonight(self) -> None:
        """Service: disable charge tonight."""
        self._tonight_reason = "disabled via service"
        self._charge_tonight = False

    # ------------------------------------------------------------------
    # Public properties (used by time/number/switch entities)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Public setters (used by switch/number entities)
    # ------------------------------------------------------------------

    def set_charge_now(self, value: bool) -> None:
        """Set the charge_now flag."""
        self._charge_now = value

    def set_charge_tonight(self, value: bool) -> None:
        """Set the charge_tonight flag."""
        self._tonight_reason = "enabled via switch" if value else "disabled via switch"
        self._charge_tonight = value

    def set_desired_range(self, value: float) -> None:
        """Set the desired range in km."""
        self._desired_range = value

    def set_max_current_limit(self, value: float) -> None:
        """Set the max current limit in A."""
        self._max_current_limit = value

    def set_charging_priority(self, value: str) -> None:
        """Set the charging priority mode."""
        self._charging_priority = value

    @property
    def charging_priority(self) -> str:
        """Return the current charging priority mode."""
        return self._charging_priority

    def restore_energy_state(
        self, total_wh: float, solar_wh: float, import_wh: float
    ) -> None:
        """Restore cumulative energy counters from persistent state."""
        self._energy_total_wh = total_wh
        self._energy_solar_wh = solar_wh
        self._energy_import_wh = import_wh

    def set_controller_enabled(self, value: bool) -> None:
        """Set the controller enabled flag (master switch).

        Disabling schedules a shutdown sequence if charging is active.
        """
        prev = self._controller_enabled
        self._controller_enabled = value
        if prev and not value and self._charging_on:
            # Schedule shutdown sequence via task so it runs in event loop
            self.hass.async_create_task(
                self._async_controller_shutdown_sequence(),
                eager_start=False,
            )
