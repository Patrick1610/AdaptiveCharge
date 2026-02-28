"""Constants for AdaptiveCharge."""
from __future__ import annotations

DOMAIN = "adaptive_charge"

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
MODE_NIGHT_TARGET = "night_target"

# --- Confidence Levels ---
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# --- Defaults ---
DEFAULT_DESIRED_RANGE = 100.0
DEFAULT_SMOOTHING_WINDOW = 120
DEFAULT_SAMPLE_INTERVAL = 10
DEFAULT_SOLAR_DONE_THRESHOLD = 50
DEFAULT_SOLAR_DONE_DURATION = 600
DEFAULT_START_DELAY = 30
DEFAULT_STOP_DELAY = 30
DEFAULT_MODULATE_MIN_INTERVAL = 30
DEFAULT_MAX_CURRENT_LIMIT = 16
DEFAULT_CHARGE_BUFFER = 0
DEFAULT_RANGE_HYSTERESIS_PCT = 3.0
DEFAULT_TONIGHT_REENTRY_CURRENT_A = 5

# --- Import guard (configurable fail-safe) ---
CONF_IMPORT_GUARD_THRESHOLD = "import_guard_threshold_w"
CONF_IMPORT_GUARD_DURATION = "import_guard_duration_s"
DEFAULT_IMPORT_GUARD_THRESHOLD_W = 200.0
DEFAULT_IMPORT_GUARD_DURATION_S = 30.0

# --- Import guard enhanced (debounce + hysteresis + escalation) ---
CONF_IMPORT_GUARD_HYSTERESIS_W = "import_guard_hysteresis_w"
CONF_IMPORT_GUARD_CLEAR_DURATION_S = "import_guard_clear_duration_s"
CONF_IMPORT_GUARD_SETTLE_S = "import_guard_settle_s"
DEFAULT_IMPORT_GUARD_HYSTERESIS_W = 50.0
DEFAULT_IMPORT_GUARD_CLEAR_DURATION_S = 20.0
DEFAULT_IMPORT_GUARD_SETTLE_S = 30.0

# Import guard states
IMPORT_GUARD_OK = "ok"
IMPORT_GUARD_REDUCING = "reducing"
IMPORT_GUARD_STOPPED = "stopped"

# --- Alignment engine defaults ---
DEFAULT_EV_STEP_THRESHOLD_W = 400.0
DEFAULT_ALIGNMENT_TIMEOUT_MIN = 8.0
DEFAULT_ALIGNMENT_TIMEOUT_MAX = 60.0
DEFAULT_EMA_SPAN_S = 8.0
DEFAULT_IMPORT_SAFETY_THRESHOLD_W = 100.0
DEFAULT_IMPORT_SAFETY_DURATION_S = 3.0

# --- Controller stabilization defaults ---
DEFAULT_HYSTERESIS_UP = 1.0
DEFAULT_HYSTERESIS_DOWN = 1.0
DEFAULT_MAX_STEP_A = 1
DEFAULT_COOLDOWN_UP_S = 45.0
DEFAULT_COOLDOWN_DOWN_S = 0.0
DEFAULT_MIN_ON_TIME_S = 300.0
DEFAULT_MIN_OFF_TIME_S = 120.0
DEFAULT_SETTLING_DURATION_S = 10.0

# --- Service call discipline ---
DEFAULT_MIN_SWITCH_TOGGLE_INTERVAL_S = 10.0

# --- Platforms ---
PLATFORMS = ["sensor", "binary_sensor", "number", "switch"]

# --- Service names ---
SERVICE_FORCE_START = "force_start"
SERVICE_FORCE_STOP = "force_stop"
SERVICE_SET_DESIRED_RANGE = "set_desired_range"
SERVICE_ENABLE_TONIGHT = "enable_tonight"
SERVICE_DISABLE_TONIGHT = "disable_tonight"
