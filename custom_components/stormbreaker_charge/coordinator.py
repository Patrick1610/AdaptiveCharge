"""Coordinator for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from statistics import mean
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
    DEFAULT_IMPORT_SAFETY_DURATION_S,
    DEFAULT_IMPORT_SAFETY_THRESHOLD_W,
    DEFAULT_MAX_CURRENT_LIMIT,
    DEFAULT_MAX_STEP_A,
    DEFAULT_MIN_OFF_TIME_S,
    DEFAULT_MIN_ON_TIME_S,
    DEFAULT_MODULATE_MIN_INTERVAL,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_SETTLING_DURATION_S,
    DEFAULT_SMOOTHING_WINDOW,
    DEFAULT_SOLAR_DONE_DURATION,
    DEFAULT_SOLAR_DONE_THRESHOLD,
    DEFAULT_START_DELAY,
    DEFAULT_STOP_DELAY,
    DOMAIN,
    MODE_CONSUMPTION_PRODUCTION,
    MODE_FORCE,
    MODE_NET_ONLY,
    MODE_STOPPED,
    MODE_SURPLUS,
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

        # Solar done tracking
        self._solar_below_threshold_since: datetime | None = None
        self._solar_done: bool = False

        # Smoothing deque: list of (timestamp, current_a) — kept for UI sensors
        self._samples: deque[tuple[datetime, float]] = deque()

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

        # Import safety
        self._import_exceed_since: float | None = None

        # Previous values for step detection
        self._prev_ev_w: float | None = None
        self._prev_net_w: float | None = None

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
        mono_now = time.monotonic()

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

        # --- Update measurement trackers (every poll, regardless of value change) ---
        self._net_tracker.update(computed_net_w, mono_now)
        self._ev_tracker.update(ev_w, mono_now)
        self._voltage_tracker.update(voltage, mono_now)

        # --- EV step detection ---
        self._alignment.on_ev_power_change(self._prev_ev_w, ev_w, mono_now)

        # --- Net power change detection for alignment ---
        net_delta = computed_net_w - self._prev_net_w if self._prev_net_w is not None else 0.0
        self._alignment.on_net_power_update(mono_now, net_delta)
        self._alignment.check_timeout(mono_now)

        # --- Settling window check ---
        self._alignment.check_settling(mono_now)

        # --- Skew-based alignment activation ---
        skew = compute_skew(self._net_tracker, self._ev_tracker)
        coherence = compute_coherence(self._net_tracker, self._ev_tracker)

        self._prev_ev_w = ev_w
        self._prev_net_w = computed_net_w

        # --- Compute surplus (coherence-aware) ---
        surplus_w = (0.0 - computed_net_w) + ev_w
        raw_current_a = (surplus_w / (voltage * 3.0)) if voltage > 0 else 0.0
        capped = min(self._max_current_limit, MAX_CURRENT_ABS)

        # EMA-smoothed current for control decisions
        ema_current_a = self._ema_filter.update(raw_current_a, mono_now)
        ema_current_a = min(max(ema_current_a, 0.0), capped)

        # --- Legacy smoothing (kept for UI sensor compatibility) ---
        raw_floored = min(max(int(raw_current_a), 0), int(capped))
        self._samples.append((now, raw_current_a))
        cutoff = now - timedelta(seconds=self._smoothing_window)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        valid_samples = [v for _, v in self._samples]
        smoothed_a = mean(valid_samples) if valid_samples else 0.0
        smoothed_floored = min(max(int(smoothed_a), 0), int(capped))

        # --- Confidence ---
        self._confidence = compute_confidence(
            net_tracker=self._net_tracker,
            ev_tracker=self._ev_tracker,
            alignment_active=self._alignment.active,
            target_current=ema_current_a,
            last_committed=self._committed_current,
            sample_interval=float(self._sample_interval),
            settling=self._alignment.settling,
        )

        # --- Determine control reason prefix ---
        control_reason = ""
        if coherence < 0.3:
            control_reason = "low_coherence"
        elif self._alignment.active:
            control_reason = "alignment_active"
        elif self._alignment.settling:
            control_reason = "settling_window"

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
                if self._pending_plugin_task and not self._pending_plugin_task.done():
                    self._pending_plugin_task.cancel()
                self._pending_plugin_task = self.hass.async_create_task(
                    self._action_plug_in_delayed(force_charge, smoothed_floored),
                    eager_start=False,
                )
            self._cable_prev = cable_connected

        # --- Import safety check ---
        import_safety_triggered = self._check_import_safety(
            computed_net_w, mono_now
        )

        # --- Control logic ---
        await self._run_control_logic(
            force_charge=force_charge,
            smoothed_floored=smoothed_floored,
            raw_floored=raw_floored,
            ema_current=ema_current_a,
            mono_now=mono_now,
            import_safety=import_safety_triggered,
            coherence=coherence,
            control_reason=control_reason,
        )

        self._last_raw_floored = raw_floored

        data: dict[str, Any] = {
            "net_w": computed_net_w,
            "ev_w": ev_w,
            "voltage": voltage,
            "surplus_w": surplus_w,
            "raw_current_a": raw_current_a,
            "raw_floored": raw_floored,
            "smoothed_a": smoothed_a,
            "smoothed_floored": smoothed_floored,
            "ema_current_a": round(ema_current_a, 2),
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
            # --- Alignment & coherence diagnostics ---
            "alignment_active": self._alignment.active,
            "settling_active": self._alignment.settling,
            "confidence_level": self._confidence,
            "measurement_coherence": round(coherence, 3),
            "estimated_skew_seconds": (
                round(skew, 3) if skew is not None else None
            ),
            "estimated_lag_seconds": (
                round(self._alignment.estimated_lag, 2)
                if self._alignment.estimated_lag is not None
                else None
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
        # Push to coordinator listeners
        self.async_set_updated_data(data)
        return data

    # ------------------------------------------------------------------
    # Import safety
    # ------------------------------------------------------------------

    def _check_import_safety(self, net_w: float, mono_now: float) -> bool:
        """Return True if import has exceeded threshold for long enough."""
        threshold = DEFAULT_IMPORT_SAFETY_THRESHOLD_W
        duration = DEFAULT_IMPORT_SAFETY_DURATION_S

        if net_w > threshold:
            if self._import_exceed_since is None:
                self._import_exceed_since = mono_now
            elif (mono_now - self._import_exceed_since) >= duration:
                return True
        else:
            self._import_exceed_since = None
        return False

    # ------------------------------------------------------------------
    # Control logic
    # ------------------------------------------------------------------

    async def _run_control_logic(
        self,
        force_charge: bool,
        smoothed_floored: int,
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

        # --- Import safety: immediate reduction ---
        if import_safety and self._charging_on and self._current_mode == MODE_SURPLUS:
            # When committed_current is None the charger state is inconsistent;
            # treat as 0 so the logic correctly triggers a stop.
            new_target = (self._committed_current or 0.0) - 1.0
            if new_target < 1.0:
                self._cancel_pending()
                self._pending_task = self.hass.async_create_task(
                    self._debounced(0, self._action_stop_surplus),
                    eager_start=False,
                )
                self._last_commit_reason = "import_safety_stop"
            else:
                await self._commit_current(
                    new_target, mono_now, reason="import_safety_reduce"
                )
            return

        # --- Start surplus charging ---
        if ema_current >= 1.0 and not self._charging_on:
            # Respect min-off time
            if self._last_off_time is not None:
                off_elapsed = mono_now - self._last_off_time
                if off_elapsed < DEFAULT_MIN_OFF_TIME_S:
                    return
            self._cancel_pending()
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
            self._cancel_pending()
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

        if target_int > 0:
            _LOGGER.debug(
                "Stormbreaker: commit %dA (float=%.2f, reason=%s, confidence=%s)",
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

            # Start settling window to avoid self-induced dip flapping
            self._alignment.start_settling(
                mono_now, DEFAULT_SETTLING_DURATION_S
            )

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
        _LOGGER.info("Stormbreaker: start_force")
        await self._set_charge_current(MAX_CURRENT_ABS)
        await asyncio.sleep(5)
        await self._enable_charging()
        self._charging_on = True
        self._current_mode = MODE_FORCE
        self._last_action = "start_force"
        self._last_on_time = time.monotonic()
        self._committed_current = float(MAX_CURRENT_ABS)
        self._last_committed_int = MAX_CURRENT_ABS

    async def _action_stop_force(self) -> None:
        _LOGGER.info("Stormbreaker: stop_force")
        await self._disable_charging()
        self._charging_on = False
        self._current_mode = MODE_STOPPED
        self._last_action = "stop_force"
        self._last_off_time = time.monotonic()
        self._committed_current = None
        self._last_committed_int = None
        await asyncio.sleep(10)
        await self._set_charge_current(MAX_CURRENT_ABS)

    async def _action_start_surplus(self, current_a: int) -> None:
        _LOGGER.info("Stormbreaker: start_surplus at %dA", current_a)
        await self._set_charge_current(current_a)
        await asyncio.sleep(5)
        await self._enable_charging()
        self._charging_on = True
        self._current_mode = MODE_SURPLUS
        self._last_action = f"start_surplus_{current_a}A"
        self._last_on_time = time.monotonic()
        self._committed_current = float(current_a)
        self._last_committed_int = current_a
        self._last_commit_reason = "start_surplus"

    async def _action_stop_surplus(self) -> None:
        _LOGGER.info("Stormbreaker: stop_surplus")
        await self._disable_charging()
        self._charging_on = False
        self._current_mode = MODE_STOPPED
        self._last_action = "stop_surplus"
        self._last_off_time = time.monotonic()
        self._committed_current = None
        self._last_committed_int = None
        self._last_commit_reason = "stop_surplus"
        await asyncio.sleep(10)
        await self._set_charge_current(MAX_CURRENT_ABS)

    async def _action_plug_in_delayed(self, force_charge: bool, smoothed_floored: int) -> None:
        await asyncio.sleep(2)
        if force_charge:
            await self._action_start_force()
        elif smoothed_floored > 0:
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