"""Coordinator for AdaptiveCharge."""
from __future__ import annotations

import asyncio
import logging
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
from .const import (
    CONF_CABLE_SENSOR,
    CONF_CHARGE_CURRENT_NUMBER,
    CONF_CHARGE_SWITCH,
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENT_RANGE_SENSOR,
    CONF_DESIRED_RANGE,
    CONF_EV_POWER_SENSOR,
    CONF_IMPORT_GUARD_CLEAR_DURATION_S,
    CONF_IMPORT_GUARD_DURATION,
    CONF_IMPORT_GUARD_HYSTERESIS_W,
    CONF_IMPORT_GUARD_SETTLE_S,
    CONF_IMPORT_GUARD_THRESHOLD,
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
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DEFAULT_ALIGNMENT_TIMEOUT_MAX,
    DEFAULT_ALIGNMENT_TIMEOUT_MIN,
    DEFAULT_COOLDOWN_DOWN_S,
    DEFAULT_COOLDOWN_UP_S,
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
    DEFAULT_IMPORT_SAFETY_DURATION_S,
    DEFAULT_IMPORT_SAFETY_THRESHOLD_W,
    DEFAULT_MAX_CURRENT_LIMIT,
    DEFAULT_MAX_STEP_A,
    DEFAULT_RANGE_HYSTERESIS_PCT,
    DEFAULT_TONIGHT_REENTRY_CURRENT_A,
    DEFAULT_TONIGHT_START_HOUR,
    DEFAULT_TONIGHT_START_MINUTE,
    DEFAULT_NIGHT_OFF_HOUR,
    DEFAULT_NIGHT_OFF_MINUTE,
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
)

_LOGGER = logging.getLogger(__name__)

MAX_CURRENT_ABS = 16

# Minimum tracker samples before lag can fall back to 0.0 (warmup threshold)
_LAG_WARMUP_SAMPLES = 5


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

        # Mutable runtime state
        self._desired_range: float = float(options.get(CONF_DESIRED_RANGE, DEFAULT_DESIRED_RANGE))
        self._max_current_limit: float = DEFAULT_MAX_CURRENT_LIMIT
        self._charge_buffer: float = 0.0
        self._range_hysteresis_pct: float = DEFAULT_RANGE_HYSTERESIS_PCT
        self._tonight_start_hour: int = DEFAULT_TONIGHT_START_HOUR
        self._tonight_start_minute: int = DEFAULT_TONIGHT_START_MINUTE
        self._night_off_hour: int = DEFAULT_NIGHT_OFF_HOUR
        self._night_off_minute: int = DEFAULT_NIGHT_OFF_MINUTE

        self._charge_now: bool = False
        self._charge_tonight: bool = False
        self._charging_enabled: bool = False

        # Master controller switch — default OFF for safe first install
        self._controller_enabled: bool = False

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

        # Previous values for step detection
        self._prev_ev_w: float | None = None
        self._prev_net_w: float | None = None
        self._prev_solar_done: bool = False

        # Mode tracking (reason / source / timestamps)
        self._mode_reason: str = "initializing"
        self._mode_source: str = "startup"
        self._mode_since: str = datetime.now().isoformat()
        self._last_transition: str = ""

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
        force_data = self._evaluate_force_charge(
            sensor_data["current_range"],
            sensor_data["presence"],
            sensor_data["cable_connected"],
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

        # --- Phase 8: Control logic ---
        if self._controller_enabled:
            await self._run_control_logic(
                force_charge=force_data["force_charge"],
                raw_floored=analysis["raw_floored"],
                ema_current=analysis["ema_current_a"],
                mono_now=mono_now,
                import_safety=import_guard_triggered,
                coherence=analysis["coherence"],
                control_reason=analysis["control_reason"],
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
            sensor_data, analysis, force_data, display_ema, display_available, now
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
        solar_raw = _get_float_state(self.hass, self._solar_sensor)
        presence = _get_bool_state(self.hass, self._presence_entity)
        cable_connected = _get_bool_state(self.hass, self._cable_sensor)
        current_range = _get_float_state(self.hass, self._current_range_sensor)

        net_w = _to_watts(net_raw, self._net_power_sensor, self.hass) if net_raw is not None else None
        consumption_w = _to_watts(consumption_raw, self._consumption_sensor, self.hass) if consumption_raw is not None else None
        production_w = _to_watts(production_raw, self._production_sensor, self.hass) if production_raw is not None else None
        ev_w = _to_watts(ev_raw, self._ev_power_sensor, self.hass) if ev_raw is not None else 0.0
        voltage = voltage_raw if voltage_raw is not None else 230.0
        solar_w = _to_watts(solar_raw, self._solar_sensor, self.hass) if solar_raw is not None else None

        if self._net_power_mode == MODE_NET_ONLY:
            computed_net_w = net_w if net_w is not None else 0.0
        else:
            if consumption_w is not None and production_w is not None:
                computed_net_w = consumption_w - production_w
            elif consumption_w is not None:
                computed_net_w = consumption_w
            else:
                computed_net_w = 0.0

        return {
            "computed_net_w": computed_net_w,
            "ev_w": ev_w,
            "voltage": voltage,
            "solar_w": solar_w,
            "presence": presence,
            "cable_connected": cable_connected,
            "current_range": current_range,
        }

    # ------------------------------------------------------------------
    # Phase 2: Alignment, EMA, confidence
    # ------------------------------------------------------------------

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
    ) -> dict[str, Any]:
        """Evaluate force charge, need, tonight condition, and earliest-start gate."""
        effective_range = self._desired_range * (1.0 + self._charge_buffer / 100.0)
        hysteresis_km = effective_range * (self._range_hysteresis_pct / 100.0)
        prev_need_active = self._need_active
        if current_range is not None:
            if self._need_active:
                self._need_active = current_range < (effective_range + hysteresis_km)
            else:
                self._need_active = current_range < effective_range
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
        force_charge = self._charge_now or tonight_condition
        if force_charge:
            self._force_source = "charge_now_switch" if self._charge_now else "charge_tonight"

        return {
            "effective_range": effective_range,
            "hysteresis_km": hysteresis_km,
            "need": need,
            "tonight_condition": tonight_condition,
            "force_charge": force_charge,
        }

    # ------------------------------------------------------------------
    # Phase 6: Cable plug-in detection
    # ------------------------------------------------------------------

    def _detect_cable_plugin(
        self,
        cable_connected: bool | None,
        force_charge: bool,
        ema_current_a: float,
    ) -> None:
        """Detect cable plug-in events and schedule delayed action."""
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
        if cable_connected is not None:
            self._cable_prev = cable_connected

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
    ) -> dict[str, Any]:
        """Assemble the coordinator data dict."""
        computed_net_w = sensor_data["computed_net_w"]
        skew = analysis["skew"]
        return {
            "net_w": computed_net_w,
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
            "presence": sensor_data["presence"],
            "cable_connected": sensor_data["cable_connected"],
            "current_range": sensor_data["current_range"],
            "desired_range": self._desired_range,
            "effective_range": force_data["effective_range"],
            "range_hysteresis_pct": self._range_hysteresis_pct,
            "range_hysteresis_km": round(force_data["hysteresis_km"], 1),
            "charge_buffer": self._charge_buffer,
            "max_current_limit": self._max_current_limit,
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
            "time_in_import_state": (
                round(time.monotonic() - self._import_guard_state_since, 1)
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
        stop charging and reset current to default.  This matches the existing
        stop behaviour already used by _action_stop_surplus / _action_stop_force.
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
        await asyncio.sleep(10)
        await self._set_charge_current(int(self._max_current_limit))

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

        # --- Import safety: escalation ladder ---
        # Step 1: Reduce current by 1A (soft mitigation)
        # Step 2: Hold / settle window to observe net import improvement
        # Step 3: Reduce to 0A (minimum current, charger stays on)
        # Step 4: Only then hard stop / charger off (hard mitigation)
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
                await self._commit_current(
                    new_target, mono_now, reason="import_guard_reduce"
                )
                self._last_reason = "import_guard_reduce"
            else:
                # Already at 0A — escalate to hard stop after settle window
                self._cancel_pending()
                self._pending_task = self.hass.async_create_task(
                    self._debounced(0, self._action_stop_surplus),
                    eager_start=False,
                )
                self._import_guard_state = IMPORT_GUARD_STOPPED
                self._last_commit_reason = "import_guard_escalate_stop"
                self._last_reason = "import_guard_escalate_stop"
                self._last_action_ts = time.monotonic()
            return

        # --- Start surplus charging ---
        if ema_current >= 1.0 and not self._charging_on:
            # Respect min-off time
            if self._last_off_time is not None:
                off_elapsed = mono_now - self._last_off_time
                if off_elapsed < DEFAULT_MIN_OFF_TIME_S:
                    return
            # Only schedule if not already pending — avoid resetting the
            # debounce timer every tick which would prevent it from completing.
            if self._pending_task is None or self._pending_task.done():
                capped_limit = min(self._max_current_limit, MAX_CURRENT_ABS)
                start_a = max(1, min(int(ema_current), int(capped_limit)))
                self._pending_task = self.hass.async_create_task(
                    self._debounced(self._start_delay, self._action_start_surplus, start_a),
                    eager_start=False,
                )
            return

        # --- Stop surplus charging ---
        if ema_current < 1.0 and self._charging_on and self._current_mode == MODE_SURPLUS:
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
            await self._try_modulate(ema_current, mono_now)

    async def _try_modulate(self, ema_current: float, mono_now: float) -> None:
        """Apply hysteresis and rate limiting to modulate current."""
        current_setpoint = self._committed_current
        if current_setpoint is None:
            return

        capped = min(self._max_current_limit, MAX_CURRENT_ABS)
        target = min(max(ema_current, 0.0), capped)
        delta = target - current_setpoint

        # During alignment or settling, only allow decreases (safety), hold otherwise
        if (self._alignment.active or self._alignment.settling) and delta > 0:
            return

        # Confidence gating
        if delta > 0 and self._confidence == CONFIDENCE_LOW:
            return

        # Hysteresis check
        if delta > 0 and delta < DEFAULT_HYSTERESIS_UP:
            return
        if delta < 0 and abs(delta) < DEFAULT_HYSTERESIS_DOWN:
            return

        # Rate limiting: max 1A per step
        step = min(abs(delta), float(DEFAULT_MAX_STEP_A))
        if delta > 0:
            new_target = current_setpoint + step
        else:
            new_target = current_setpoint - step

        new_target = min(max(new_target, 0.0), capped)

        # Cooldown
        if delta > 0:
            if self._last_up_time is not None:
                up_elapsed = mono_now - self._last_up_time
                if up_elapsed < DEFAULT_COOLDOWN_UP_S:
                    return

        reason = "modulate_up" if delta > 0 else "modulate_down"
        await self._commit_current(new_target, mono_now, reason=reason)

    async def _commit_current(
        self, target: float, mono_now: float, reason: str = ""
    ) -> None:
        """Commit a new current setpoint to the actuator."""
        capped = min(self._max_current_limit, MAX_CURRENT_ABS)
        target = min(max(target, 0.0), capped)
        target_int = max(int(target), 0)

        # Idempotent: skip if same integer value already sent
        if target_int == self._last_committed_int:
            return

        if target_int >= 0:
            _LOGGER.debug(
                "AdaptiveCharge: commit %dA (float=%.2f, reason=%s, confidence=%s)",
                target_int, target, reason, self._confidence,
            )
            await self._set_charge_current(target_int)
            self._committed_current = target
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
                mono_now, DEFAULT_SETTLING_DURATION_S
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
        source = self._force_source or "charge_now_switch"
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
        source = self._force_source or "charge_now_switch"
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
        await asyncio.sleep(10)
        await self._set_charge_current(int(self._max_current_limit))

    async def _action_start_surplus(self, current_a: int) -> None:
        _LOGGER.info("AdaptiveCharge: start_surplus at %dA", current_a)
        await self._set_charge_current(current_a)
        await asyncio.sleep(5)
        await self._enable_charging()
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
        self._import_below_since = None
        self._import_exceed_since = None
        await asyncio.sleep(10)
        await self._set_charge_current(int(self._max_current_limit))

    async def _action_plug_in_delayed(self, force_charge: bool, ema_current_a: float) -> None:
        await asyncio.sleep(2)
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

    @property
    def tonight_start_hour(self) -> int:
        """Return the configured earliest charge start hour."""
        return self._tonight_start_hour

    @property
    def tonight_start_minute(self) -> int:
        """Return the configured earliest charge start minute."""
        return self._tonight_start_minute

    @property
    def night_off_hour(self) -> int:
        """Return the configured night-off hour."""
        return self._night_off_hour

    @property
    def night_off_minute(self) -> int:
        """Return the configured night-off minute."""
        return self._night_off_minute

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

    def set_charge_buffer(self, value: float) -> None:
        """Set the charge buffer percentage (0-25%)."""
        self._charge_buffer = value

    def set_range_hysteresis_pct(self, value: float) -> None:
        """Set the range hysteresis percentage (0-10%)."""
        self._range_hysteresis_pct = value

    def set_tonight_start_time(self, hour: int, minute: int) -> None:
        """Set the earliest charge tonight start time."""
        self._tonight_start_hour = hour
        self._tonight_start_minute = minute

    def set_night_off_time(self, hour: int, minute: int) -> None:
        """Set the nightly charge_tonight reset time and reschedule."""
        self._night_off_hour = hour
        self._night_off_minute = minute
        self._schedule_night_off()

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