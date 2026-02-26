"""Constants for Stormbreaker Surplus EV Charge."""
from __future__ import annotations

DOMAIN = "stormbreaker_charge"

# --- Configuration Keys ---
CONF_NET_POWER_MODE = "net_power_mode"
CONF_NET_POWER_SENSOR = "net_power_sensor"
CONF_CONSUMPTION_SENSOR = "consumption_sensor"
CONF_PRODUCTION_SENSOR = "production_sensor"
CONF_EV_POWER_SENSOR = "ev_power_sensor"
CONF_VOLTAGE_SENSOR = "voltage_sensor"
CONF_PRESENCE_ENTITY = "presence_entity"
CONF_CABLE_SENSOR = "cable_sensor"
CONF_CURRENT_RANGE_SENSOR = "current_range_sensor"
CONF_DESIRED_RANGE = "desired_range"
CONF_SOLAR_SENSOR = "solar_sensor"
CONF_CHARGE_SWITCH = "charge_switch"
CONF_CHARGE_CURRENT_NUMBER = "charge_current_number"
CONF_SMOOTHING_WINDOW = "smoothing_window"
CONF_SAMPLE_INTERVAL = "sample_interval"
CONF_SOLAR_DONE_THRESHOLD = "solar_done_threshold"
CONF_SOLAR_DONE_DURATION = "solar_done_duration"
CONF_START_DELAY = "start_delay"
CONF_STOP_DELAY = "stop_delay"
CONF_MODULATE_MIN_INTERVAL = "modulate_min_interval"

# --- Net Power Mode Values ---
MODE_NET_ONLY = "net_only"
MODE_CONSUMPTION_PRODUCTION = "consumption_production"

# --- Charge Mode Values ---
MODE_FORCE = "force"
MODE_SURPLUS = "surplus"
MODE_STOPPED = "stopped"

# --- Defaults ---
DEFAULT_DESIRED_RANGE = 100.0
DEFAULT_SMOOTHING_WINDOW = 120
DEFAULT_SAMPLE_INTERVAL = 10
DEFAULT_SOLAR_DONE_THRESHOLD = 50
DEFAULT_SOLAR_DONE_DURATION = 600
DEFAULT_START_DELAY = 30
DEFAULT_STOP_DELAY = 90
DEFAULT_MODULATE_MIN_INTERVAL = 30
DEFAULT_MAX_CURRENT_LIMIT = 16

# Anti-flap / hysteresis
DEFAULT_HYSTERESIS_BAND = 1.0  # Amps
DEFAULT_MIN_START_CURRENT = 2  # Amps

# Rate limiting
DEFAULT_COOLDOWN_PERIOD = 60  # Seconds
DEFAULT_MAX_STEP_SIZE = 1  # Amps

# Timestamp coherency
DEFAULT_STALE_THRESHOLD = 10  # Seconds

# Minimum on/off times
DEFAULT_MIN_ON_TIME = 300  # Seconds (5 min)
DEFAULT_MIN_OFF_TIME = 120  # Seconds (2 min)

# Safety rails
SURPLUS_CLAMP_W = 20000  # ±20 kW
FALLBACK_VOLTAGE_V = 230.0  # Default voltage when sensor unavailable

# --- Platforms ---
PLATFORMS = ["sensor", "binary_sensor", "number", "switch"]

# --- Service names ---
SERVICE_FORCE_START = "force_start"
SERVICE_FORCE_STOP = "force_stop"
SERVICE_SET_DESIRED_RANGE = "set_desired_range"
SERVICE_ENABLE_TONIGHT = "enable_tonight"
SERVICE_DISABLE_TONIGHT = "disable_tonight"
