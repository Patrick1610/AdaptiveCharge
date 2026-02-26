# AdaptiveCharge

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/Patrick1610/AdaptiveCharge.svg)](https://github.com/Patrick1610/AdaptiveCharge/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration that intelligently controls EV charging based on solar surplus power. It maximises self-consumption of solar energy by dynamically adjusting the charge current and automatically starting/stopping charging when surplus is available.

---

## Features

- **Surplus charging**: automatically charges your EV using excess solar production
- **Force charge**: override to charge at maximum current immediately
- **Charge Tonight**: overnight scheduling using a desired range target
- **kW / W auto-detection**: works with sensors reporting in either unit
- **Configurable smoothing**: exponential moving-average-style deque smoothing prevents rapid current changes
- **Debounced start/stop/modulate**: prevents relay chatter with configurable delays
- **Solar Done detection**: detects when solar generation has finished for the day
- **Full debug attributes**: every sensor exposes source values, mode and last action

---

## Installation via HACS

1. Open **HACS** in Home Assistant.
2. Go to **Integrations** → three-dot menu → **Custom repositories**.
3. Add `https://github.com/Patrick1610/AdaptiveCharge` with category **Integration**.
4. Click **Download** on the _Stormbreaker Surplus EV Charge_ card.
5. Restart Home Assistant.

---

## Configuration

Navigate to **Settings → Devices & Services → Add Integration** and search for _Stormbreaker Surplus EV Charge_.

### Step 1 – Net Power Mode

Choose how your household net power is measured:

| Option | Description |
|--------|-------------|
| `Single net power sensor` | One sensor gives import (+) / export (−) in W or kW |
| `Separate consumption & production sensors` | Two sensors: house load and solar yield |

### Step 2a – Net Power Sensor _(net_only mode)_

Select a `sensor` entity. **Sign convention**: positive = importing from grid, negative = exporting.

### Step 2b – Consumption & Production _(consumption_production mode)_

- **Consumption Sensor**: total house load (W or kW, always positive)
- **Production Sensor**: solar generation (W or kW, always positive)

The integration computes `net = consumption − production`.

### Step 3 – Charger Sensors

- **EV Power Sensor**: the power currently drawn by the EVSE/charger (W or kW)
- **Voltage Sensor**: the mains voltage (V) used to convert surplus W → A

### Step 4 – Vehicle Entities

- **Vehicle Presence**: `device_tracker` entity (`home` = present)
- **Cable Sensor**: `binary_sensor` — on when cable is plugged in
- **Current Range Sensor**: `sensor` reporting current battery range in km

### Step 5 – Target Range

Set the desired range in km for overnight charging (default: 100 km).

### Step 6 – Solar Sensor _(optional)_

A `sensor` for total solar yield (W or kW). Used to detect _Solar Done_ state.

### Step 7 – Actuators _(optional)_

- **Charge Switch**: `switch` entity to enable/disable the EVSE
- **Charge Current Number**: `number` entity to set the charge current (A)

If left empty the integration tracks state internally but does not issue actual commands.

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

## Sign Convention for Net Power

```
net_w > 0  →  importing from grid (house load > solar)
net_w < 0  →  exporting to grid  (solar > house load)
```

Surplus available for EV:

```
surplus_w = (0 − net_w) + ev_w
```

The EV power is added back because it is already included in the net measurement; we want to know the surplus **excluding** what the EV is already using.

---

## kW vs W Auto-Detection

For every power sensor the integration checks the `unit_of_measurement` attribute:

1. If the attribute contains `kW`, the value is multiplied by 1000.
2. If no unit is present and the absolute value is less than 20, the value is assumed to be kW and multiplied by 1000.
3. Otherwise the value is used as-is (W).

The voltage sensor is always used as-is.

---

## Smoothing Algorithm

Every `sample_interval` seconds a new `(timestamp, raw_current_a)` sample is appended to a deque. Samples older than `smoothing_window` seconds are pruned. `smoothed_a` is the arithmetic mean of all remaining samples.

```
raw_current_a  = surplus_w / (voltage × 3)
smoothed_a     = mean(samples within smoothing_window)
smoothed_floored = clamp(floor(smoothed_a), 0, max_current_limit)
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
| `sensor.voltage_used_v` _(debug)_ | V | Voltage used for calculation |
| `sensor.solar_done_status` _(debug)_ | on/off | Whether solar generation has ended |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.force_charge` | True when Charge Now switch is on |

### Number Entities

| Entity | Range | Description |
|--------|-------|-------------|
| `number.desired_range_km` | 0–1000 km | Target range for overnight charging |
| `number.max_current_limit_a` | 0–16 A | Maximum allowed charge current |

### Switches

| Entity | Description |
|--------|-------------|
| `switch.charge_now` | Force charge at maximum current immediately |
| `switch.charge_tonight` | Enable overnight charge-to-range scheduling |
| `switch.charging_enable` | Virtual mirror of the EVSE enable state |

### Services

| Service | Description |
|---------|-------------|
| `stormbreaker_charge.force_start` | Enable Charge Now and start immediately |
| `stormbreaker_charge.force_stop` | Disable Charge Now and stop |
| `stormbreaker_charge.set_desired_range` | Set desired range (km) |
| `stormbreaker_charge.enable_tonight` | Turn on Charge Tonight |
| `stormbreaker_charge.disable_tonight` | Turn off Charge Tonight |

---

## Mapping to Tesla / Tessie Entities

If you use [Tessie](https://tessie.com/) or the Tesla integration, map entities like this:

| Stormbreaker field | Tesla / Tessie entity |
|-------------------|-----------------------|
| EV Power Sensor | `sensor.my_car_charger_power` |
| Voltage Sensor | `sensor.my_car_charger_voltage` |
| Vehicle Presence | `device_tracker.my_car` |
| Cable Sensor | `binary_sensor.my_car_charging_cable_connected` |
| Current Range Sensor | `sensor.my_car_battery_range` |
| Charge Switch | `switch.my_car_charger` |
| Charge Current Number | `number.my_car_charging_amps` |

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

## How It Stabilizes

Rapid toggling of the charge current is the most common complaint with surplus-based EV charging. This integration uses several complementary mechanisms to prevent it.

### Sample-based freshness

Every coordinator poll cycle is treated as a new sample for each measurement source, regardless of whether the sensor value has changed. The internal `MeasurementTracker` records the elapsed time between successive polls and maintains a rolling EWMA of the poll cadence (`avg_interval_s`) and its jitter. Freshness and staleness checks are therefore never fooled by sensors whose output stays constant for extended periods.

### Measurement coherence and adaptive skew detection

After a setpoint change the EV power sensor typically responds faster than the net-power sensor, or vice-versa. The integration computes an **alignment active** flag that is `True` whenever the timestamp skew between the two streams exceeds an adaptive threshold:

```
threshold = max(2 s,
                1.0 × max(net_interval, ev_interval)
              + 2.0 × max(net_jitter,   ev_jitter))
```

This threshold scales with the observed update cadence so that a slow (60 s) sensor installation gets a proportionally wider window while a fast (5 s) installation remains responsive.

While `alignment_active` is `True`:
- **Upward** current steps are blocked — the controller waits for coherent data.
- **Downward** safety steps proceed immediately.

### Settling window

Each time the controller commits a new integer setpoint it starts a **settling window** of `max(30 s, 2 × sample_interval)`. Inside this window, further upward adjustments are held so that the transient power response of the charger does not cause the controller to immediately raise the current again.

### Hysteresis and rate limiting

Before any setpoint change is sent to the charger:
- An upward change requires a surplus of at least **+1 A** above the current setpoint.
- A downward change requires a deficit of at least **−1 A** below the current setpoint.
- Only **1 A per decision cycle** is allowed (rate limiting).
- A **45 s cooldown** is enforced between successive upward steps.

These limits are float-based internally; integer rounding only happens at the final "apply to charger" step, eliminating hidden biases caused by early rounding.

### Minimum on/off times

- The charger will not stop surplus charging until it has been running for at least **5 minutes** (configurable).
- After a stop, the charger will not restart until at least **2 minutes** have elapsed.

---

## Diagnostics Attributes

The following additional attributes are exposed on every sensor to help diagnose stabilization behaviour:

| Attribute | Type | Description |
|-----------|------|-------------|
| `alignment_active` | bool | True when EV-step or skew-based coherence loss has been detected |
| `confidence_level` | str | `high` / `medium` / `low` — current data quality assessment |
| `measurement_coherence` | float 0–1 | 1.0 = fully aligned streams; 0.0 = fully incoherent |
| `estimated_skew_seconds` | float | Absolute timestamp difference between the last net and EV samples |
| `net_update_interval_s` | float | EWMA-estimated poll cadence of the net-power sensor |
| `ev_update_interval_s` | float | EWMA-estimated poll cadence of the EV-power sensor |
| `voltage_update_interval_s` | float | EWMA-estimated poll cadence of the voltage sensor |
| `last_sample_age_net_s` | float | Seconds since the last net-power sample arrived |
| `last_sample_age_ev_s` | float | Seconds since the last EV-power sample arrived |
| `last_applied_current_a` | int | Most recent integer setpoint sent to the charger |
| `last_commit_reason` | str | Reason code for the last setpoint decision (e.g. `modulate_up`, `import_safety_reduce`, `blocked_settling_window`) |

---

## Troubleshooting

**Charging never starts**
- Check that `sensor.net_surplus_excl_ev_w` shows a positive value during peak solar hours.
- Verify sign convention: net power must be negative (exporting) to show surplus.
- Increase `smoothing_window` if values are unstable.

**Charging keeps starting and stopping**
- Increase `start_delay` and `stop_delay` to add more hysteresis.
- Increase `smoothing_window` to smooth out short-term fluctuations.

**kW sensors not converting correctly**
- Ensure the sensor has `unit_of_measurement: kW` in its attributes.
- If missing, the heuristic (value < 20 → treat as kW) may misfire; add a unit attribute template sensor.

**Force charge not working**
- Ensure `Charge Switch` is configured and the entity is available.
- Check Home Assistant logs for service call errors.
