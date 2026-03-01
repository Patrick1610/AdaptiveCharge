# AdaptiveCharge

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/Patrick1610/AdaptiveCharge.svg)](https://github.com/Patrick1610/AdaptiveCharge/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Version 3.0.0**

A Home Assistant custom integration that intelligently controls EV charging based on solar surplus power. It maximises self-consumption of solar energy by dynamically adjusting the charge current and automatically starting/stopping charging when surplus is available.

Works with any EV charger (EVSE) that exposes a `switch` and/or a `number` entity to Home Assistant — no proprietary cloud connection required.

---

## Features

- **Surplus charging**: automatically charges your EV using excess solar production
- **Force charge**: override to charge at maximum current immediately
- **Charge Tonight**: overnight scheduling using a desired range target
- **kW / W auto-detection**: works with sensors reporting in either unit
- **Configurable smoothing**: rolling-window deque smoothing prevents rapid current changes
- **Debounced start/stop/modulate**: prevents relay chatter with configurable delays
- **Solar Done detection**: detects when solar generation has finished for the day
- **Alignment engine**: handles sensor-lag and charger-reaction-lag intelligently
- **Import guard**: multi-stage escalation to prevent sustained grid import
- **Voltage sensor fallback**: works without a live voltage sensor using a configurable default
- **Full debug attributes**: every sensor exposes source values, mode and last action

---

## How It Works

### Surplus Calculation

The integration computes how much power is available for the EV **on top of what the house needs**:

```
surplus_w = (0 − net_w) + ev_w
```

The EV power is added back because it is already included in the net measurement. This gives the surplus **excluding** what the EV is already using.

**Sign convention for net power:**
```
net_w > 0  →  importing from grid (house load > solar)
net_w < 0  →  exporting to grid  (solar > house load)
```

**Example:**
```
Solar production : 4 000 W
House load       : 1 000 W
EV charging      : 2 000 W (≈ 8.7 A × 230 V × 3 phases)

net_w    = 1 000 + 2 000 − 4 000 = −1 000 W  (exporting 1 000 W)
surplus  = (0 − (−1 000)) + 2 000 = 3 000 W
```
The controller sees 3 000 W available → 3 000 / (230 × 3) ≈ **4.35 A** raw current → floored to **4 A**.

### Current Calculation

```
raw_current_a    = surplus_w / (voltage × phases)
smoothed_a       = mean(samples within smoothing_window)
smoothed_floored = clamp(floor(smoothed_a), 0, max_current_limit)
```

- `voltage` defaults to the configured **Voltage Fallback** (default 230 V). If a **Voltage Sensor** entity is provided and returns a value, that live reading is used instead.
- `phases` is always 3 (three-phase assumed).
- `max_current_limit` is adjustable at runtime via the `number.max_current_limit_a` entity (default 16 A).

**Example with smoothing:**
```
Smoothing window: 120 s, sample interval: 10 s → up to 12 samples stored

Samples (A): [3, 4, 4, 5, 5, 5, 6, 6, 5, 5, 4, 4]
Mean = 4.67 A → floored to 4 A (delivered to charger)
```

---

## Installation via HACS

1. Open **HACS** in Home Assistant.
2. Go to **Integrations** → three-dot menu → **Custom repositories**.
3. Add `https://github.com/Patrick1610/AdaptiveCharge` with category **Integration**.
4. Click **Download** on the _AdaptiveCharge_ card.
5. Restart Home Assistant.

---

## Configuration

Navigate to **Settings → Devices & Services → Add Integration** and search for _AdaptiveCharge_.

### Step 1 – Net Power Mode

Choose how your household net power is measured:

| Option | Description |
|--------|-------------|
| `Single net power sensor` | One sensor gives import (+) / export (−) in W or kW |
| `Separate consumption & production sensors` | Two sensors: house load and solar yield |

### Step 2a – Net Power Sensor _(net_only mode)_

Select a `sensor` entity. **Sign convention**: positive = importing from grid, negative = exporting.

- **Invert sensor sign**: enable this toggle if your meter reports the opposite sign (positive = export, negative = import). AdaptiveCharge will multiply the reading by −1.

### Step 2b – Consumption & Production _(consumption_production mode)_

- **Consumption Sensor**: total house load (W or kW, always positive)
- **Production Sensor**: solar generation (W or kW, always positive)

The integration computes `net = consumption − production`.

### Step 3 – Charger Sensors

- **EV Power Sensor**: the power currently drawn by the EVSE/charger (W or kW) — *required*
- **Voltage Sensor** *(optional)*: a live sensor for mains voltage (V). When the sensor is unavailable or not provided, the **Voltage Fallback** value is used instead.
- **Voltage Fallback**: the fixed voltage (V) to use when no sensor is configured or when the sensor is unavailable. Default: **230 V**.

> **Tip**: If your grid voltage is stable (e.g. always ~230 V or ~120 V), you can skip the voltage sensor and rely on the fallback alone.

### Step 4 – Vehicle Entities

- **Vehicle Presence**: `device_tracker` entity (`home` = present)
- **Cable Sensor**: `binary_sensor` — on when cable is plugged in
- **Current Range Sensor**: `sensor` reporting current battery range in km

### Step 5 – Night Charging

Configure the overnight charge schedule:

- **Target Range (km)**: the battery range target for the Charge Tonight feature (default: 100 km).
- **Charging Window Start**: time (hh:mm) when the night charging window opens (default: 22:00).
- **Charging Window End**: time (hh:mm) when the Charge Tonight switch is automatically turned off (default: 07:00).

### Step 6 – Solar Sensor _(optional)_

A `sensor` for total solar yield (W or kW). Used to detect _Solar Done_ state.

### Step 7 – Actuators

- **Charge Switch** *(optional)*: `switch` entity to enable/disable the EVSE
- **Charge Current Number** *(optional)*: `number` entity to set the charge current (A)
- **Max Charge Current (A)**: the maximum current AdaptiveCharge will ever send to the charger (default: 16 A). Set this to your charger's or cable's rated maximum. This can also be adjusted at runtime via the `number.max_current_limit_a` entity.

If the switch and current number are left empty, the integration tracks state internally but does not issue actual commands.

### Step 8 – Advanced Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| Smoothing Window | 120 s | Length of the rolling window for current averaging |
| Sample Interval | 10 s | How often sensor values are read and logic executed |
| Solar Done Threshold | 50 W | Solar production below which _solar done_ timer starts |
| Solar Done Duration | 600 s | How long production must stay below threshold before solar_done triggers |
| Start Delay | 30 s | Debounce before starting surplus charging |
| Stop Delay | 30 s | Debounce before stopping surplus charging |
| Modulate Min Interval | 30 s | Minimum time between current modulation calls |

---

## How It Stabilizes

The controller uses a multi-layered approach to prevent flapping, overreaction, and stale-data faults:

### 1. Sample-Based Freshness

Every poll cycle updates the `last_seen` timestamp of each sensor — even when the value hasn't changed. This prevents false staleness detection when power is steady.

### 2. Dynamic Alignment

When the charger setpoint changes by a significant amount (>400 W), the controller enters an **alignment phase**. During this phase:

- Upward current adjustments are blocked.
- Downward safety adjustments remain responsive.
- The phase ends when the net power sensor reacts in the expected direction, or when a dynamic timeout expires.

The timeout is learned from observed reaction lag:

```
timeout = min(max(2 × median_lag, 8 s), 60 s)
```

**Why this matters:** When the integration raises the charge current from 4 A to 5 A, the charger takes a few seconds to react. During that time the net power sensor has not yet moved. Without alignment, the controller would see the surplus unchanged and immediately raise current again — causing oscillation. The alignment phase suppresses upward changes until the charger's reaction is confirmed.

**Example:**
```
t = 0 s   : surplus = 3 000 W → set current to 4 A
t = 1 s   : charger starts drawing more → net_w drops by ~690 W
             alignment phase ends (reaction confirmed)
t = 12 s  : surplus = 1 200 W → set current to 1 A
             new alignment phase begins, timeout = 8 s
t = 20 s  : net_w confirmed → alignment ends, next step allowed
```

### 3. Settling Window

After every setpoint commit, a short settling window (10 s) suppresses further upward steps. This prevents "self-induced dip" flapping where the controller reacts to the transient caused by its own action.

### 4. Measurement Coherence

The controller computes a **coherence score** (0–1) from the timestamp skew between net and EV power streams, and their individual reliabilities. When coherence is low, upward changes are gated.

**Example:**
```
Net power update interval : 5 s (fresh, reliable)
EV power update interval  : 25 s (slow / stale)
Skew                      : 20 s apart

Coherence score → 0.3 (low)  → upward step blocked
```

### 5. Float-Based Control with Hysteresis

Internal calculations use float current values. Integer rounding only happens at the final actuator call. A ±1 A hysteresis dead zone prevents rapid toggling at integer boundaries (e.g. 1 ↔ 2 A).

### 6. Rate Limiting & Cooldown

- Max 1 A change per step.
- 45 s minimum between upward steps.
- Downward steps and safety actions are immediate.

### 7. Confidence Gating

Each tick computes a confidence level (HIGH/MEDIUM/LOW) from data staleness, alignment state, settling state, and target stability. Upward changes require at least MEDIUM confidence.

### 8. Import Guard (Enhanced)

The import guard prevents grid import while surplus charging. It uses a multi-stage approach:

**Debounce**: Import must exceed the threshold (default 200 W) for a sustained period (default 30 s) before any action is taken. Short transient spikes are ignored.

**Escalation Ladder**:
1. **Reduce current** — decrease by 1 A (soft mitigation)
2. **Settle window** — wait 30 s to observe if net import improves
3. **Reduce to 0 A** — if import persists, reduce to 0 A (charger stays connected)
4. **Hard stop** — only if 0 A doesn't resolve import, disable the charger relay

**Hysteresis**: Import must drop below `threshold − margin` (default 150 W) for 20 s before the guard clears. This prevents rapid flip-flop at the threshold boundary.

**Guard States**: `ok` → `reducing` → `stopped` (visible in the Import Guard State sensor)

### 9. Mode Tracking

Every mode transition records:
- `mode_reason`: why the mode is currently active
- `mode_source`: what triggered it (e.g. `auto_rule`, `charge_now_switch`, `user_toggle`, `import_guard`)
- `mode_since`: ISO timestamp of when the current mode was entered
- `last_transition`: `previous_mode → current_mode: reason`

### 10. Charge Tonight Auto-Off

The Charge Tonight switch automatically turns off when:
- The EV cable is unplugged
- The `solar_done` condition transitions from active to inactive (solar production recovers)

### Diagnostics

The **Alignment Diagnostics** sensor exposes:

| Attribute | Description |
|-----------|-------------|
| `alignment_active` | True during alignment phase |
| `settling_active` | True during settling window |
| `confidence_level` | LOW / MEDIUM / HIGH |
| `measurement_coherence` | 0..1 coherence score |
| `estimated_skew_seconds` | Current timestamp skew between net and EV |
| `estimated_lag_seconds` | Learned median reaction lag |
| `net_update_interval_s` | EWMA of net power update interval |
| `ev_update_interval_s` | EWMA of EV power update interval |
| `last_sample_age_net_s` | Seconds since last net power poll |
| `last_sample_age_ev_s` | Seconds since last EV power poll |
| `last_applied_current_a` | Last integer setpoint sent to charger |
| `last_control_reason` | Why the last decision was made |

The **Import Guard State** sensor exposes:

| Attribute | Description |
|-----------|-------------|
| `import_guard_reason` | Reason string (e.g. "transient spike ignored", "sustained import 35s > 200W") |
| `time_in_import_state` | Seconds in current guard state |
| `import_watts` | Current grid import power (W) |

The **Mode** sensor exposes:

| Attribute | Description |
|-----------|-------------|
| `mode_reason` | Why the current mode is active |
| `mode_source` | What triggered it (auto_rule, charge_now_switch, user_toggle, import_guard) |
| `mode_since` | ISO timestamp of when current mode was entered |
| `last_transition` | Previous → current mode with reason |

---

## kW vs W Auto-Detection

For every power sensor the integration checks the `unit_of_measurement` attribute:

1. If the attribute contains `kW`, the value is multiplied by 1000.
2. If no unit is present and the absolute value is less than 20, the value is assumed to be kW and multiplied by 1000.
3. Otherwise the value is used as-is (W).

The voltage sensor is always used as-is (V).

---

## Smoothing Algorithm

Every `sample_interval` seconds a new `(timestamp, raw_current_a)` sample is appended to a deque. Samples older than `smoothing_window` seconds are pruned. `smoothed_a` is the arithmetic mean of all remaining samples.

```
raw_current_a    = surplus_w / (voltage × 3)
smoothed_a       = mean(samples within smoothing_window)
smoothed_floored = clamp(floor(smoothed_a), 0, max_current_limit)
```

**Example — effect of window size on stability:**
```
Cloud shadow passes (10 s): surplus drops from 3 000 W to 500 W then recovers

With 30 s window  (3 samples): mean ≈ 2.1 A → floored 2 A (reactive, more steps)
With 120 s window (12 samples): mean ≈ 3.5 A → floored 3 A (stable, fewer steps)
```

Using a longer smoothing window reduces current oscillations caused by cloud cover fluctuations.

---

## Control Logic

### Start Surplus Charging
_Condition_: `smoothed_floored > 0` and not currently charging

→ After `start_delay` seconds: set current to `smoothed_floored` A, wait 5 s, enable charging.

### Stop Surplus Charging
_Condition_: `smoothed_floored < 1` and currently charging in surplus mode

→ After `stop_delay` seconds: disable charging, wait 10 s, reset current to 16 A.

### Modulate Current
_Condition_: charging in surplus mode and `raw_floored` changed

→ After `modulate_min_interval` seconds: set current to `raw_floored` A (if > 0).

### Force Charge
_Condition_: `Charge Now` switch turned on

→ After 5 s debounce: set current to 16 A, wait 5 s, enable charging.

_Condition_: `Charge Now` switch turned off

→ After 3 s debounce: disable charging, wait 10 s, reset current to 16 A.

### Cable Plug-In
When the cable sensor transitions off → on:

- if force charge active → start_force
- elif smoothed_floored > 0 → start_surplus
- else → stop_surplus (stay off, current reset)

---

## Entity Reference

### Sensors

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.net_surplus_excl_ev_w` | W | Surplus available for EV charging |
| `sensor.available_current_raw_a` | A | Instantaneous available current |
| `sensor.available_charge_current_raw_floored_a` | A | Raw current clamped 0–16 A |
| `sensor.available_charge_current_smoothed_a` | A | Smoothed available current |
| `sensor.available_charge_current_smoothed_floored_a` | A | Smoothed current clamped 0–16 A |
| `sensor.computed_net_power_w` _(debug)_ | W | Net power after unit conversion |
| `sensor.computed_ev_power_w` _(debug)_ | W | EV power after unit conversion |
| `sensor.voltage_used_v` _(debug)_ | V | Voltage used for calculation (sensor or fallback) |
| `sensor.solar_done_status` _(debug)_ | on/off | Whether solar generation has ended |
| `sensor.ema_current_a` | A | EMA-filtered current driving control decisions |
| `sensor.alignment_diagnostics` _(debug)_ | LOW/MED/HIGH | Alignment engine state and diagnostics |
| `sensor.import_guard_state` _(debug)_ | ok/reducing/stopped | Import guard escalation state |
| `sensor.mode` | — | Current control mode with transition history |
| `sensor.last_action` | — | Description of the last control action |
| `sensor.last_reason` | — | Reason for the last decision |
| `sensor.target_current` | A | Target current currently being pursued |
| `sensor.current_setting` | A | Last current setpoint committed to charger |
| `sensor.available_current_decision` | A | Current after alignment/coherence gating |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.force_charge` | True when Charge Now switch is on |
| `binary_sensor.charging_active` | True when the integration believes charging is active |

### Number Entities

| Entity | Range | Description |
|--------|-------|-------------|
| `number.desired_range_km` | 0–1000 km | Target range for overnight charging |
| `number.max_current_limit_a` | 0–16 A | Maximum allowed charge current |

### Switches

| Entity | Description |
|--------|-------------|
| `switch.controller_enabled` | Master on/off for the control logic (default: off) |
| `switch.charge_now` | Force charge at maximum current immediately |
| `switch.charge_tonight` | Enable overnight charge-to-range scheduling |

### Services

| Service | Description |
|---------|-------------|
| `adaptive_charge.force_start` | Enable Charge Now and start immediately |
| `adaptive_charge.force_stop` | Disable Charge Now and stop |
| `adaptive_charge.set_desired_range` | Set desired range (km) |
| `adaptive_charge.enable_tonight` | Turn on Charge Tonight |
| `adaptive_charge.disable_tonight` | Turn off Charge Tonight |

---

## Example Entity Mappings

AdaptiveCharge is compatible with any EVSE that exposes its entities to Home Assistant. Below are examples for common integrations:

### Tessie / Tesla Integration

| AdaptiveCharge field | Example entity |
|-------------------|-----------------------|
| EV Power Sensor | `sensor.my_car_charger_power` |
| Voltage Sensor *(optional)* | `sensor.my_car_charger_voltage` |
| Vehicle Presence | `device_tracker.my_car` |
| Cable Sensor | `binary_sensor.my_car_charging_cable_connected` |
| Current Range Sensor | `sensor.my_car_battery_range` |
| Charge Switch | `switch.my_car_charger` |
| Charge Current Number | `number.my_car_charging_amps` |

### go-e Charger

| AdaptiveCharge field | Example entity |
|-------------------|-----------------------|
| EV Power Sensor | `sensor.goe_power` |
| Voltage Sensor *(optional)* | `sensor.goe_voltage` |
| Charge Switch | `switch.goe_charging_allowed` |
| Charge Current Number | `number.goe_max_current` |

> If your charger integration does not expose a voltage sensor, set the **Voltage Fallback** to your local grid voltage (e.g. 230 V for Europe, 120 V for North America).

---

## Debug Attributes

Every sensor exposes the following extra attributes:

| Attribute | Description |
|-----------|-------------|
| `mode` | Current control mode (`force`, `surplus`, `stopped`) |
| `last_action` | Description of the last control action taken |
| `last_updated` | ISO timestamp of the last data update |
| `charging_on` | Whether the integration believes charging is active |
| `sample_count` | Number of samples in the smoothing deque |

---

## Troubleshooting

**Charging never starts**
- Check that `sensor.net_surplus_excl_ev_w` shows a positive value during peak solar hours.
- Verify sign convention: net power must be negative (exporting) to show surplus.
- Increase `smoothing_window` if values are unstable.
- Ensure `switch.controller_enabled` is turned on.

**Charging keeps starting and stopping**
- Increase `start_delay` and `stop_delay` to add more hysteresis.
- Increase `smoothing_window` to smooth out short-term fluctuations.

**kW sensors not converting correctly**
- Ensure the sensor has `unit_of_measurement: kW` in its attributes.
- If missing, the heuristic (value < 20 → treat as kW) may misfire; add a unit attribute template sensor.

**Voltage calculation seems wrong**
- Check `sensor.voltage_used_v` to see which voltage value is being used.
- If no voltage sensor is configured, the Voltage Fallback value is used (configurable in Step 3).
- If a voltage sensor is configured but returning `unavailable`, the Voltage Fallback is used as backup.

**Force charge not working**
- Ensure `Charge Switch` is configured and the entity is available.
- Check Home Assistant logs for service call errors.
