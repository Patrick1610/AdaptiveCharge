# AdaptiveCharge

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/Patrick1610/AdaptiveCharge.svg)](https://github.com/Patrick1610/AdaptiveCharge/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration that intelligently controls EV charging based on solar surplus power. It maximises self-consumption of solar energy by dynamically adjusting the charge current and automatically starting/stopping charging when surplus is available.

---

## Features

- **Surplus charging**: automatically charges your EV using excess solar production
- **Force charge**: override to charge at maximum current immediately
- **Charge Tonight**: overnight scheduling using a desired range target, with auto-off on cable unplug or solar recovery
- **Invert net power**: toggle for sensors with reversed sign convention
- **Charge limit control**: optional number entity to set/reset the vehicle's charge limit %
- **kW / W auto-detection**: works with sensors reporting in either unit
- **EMA smoothing**: exponential moving-average filter for stable current decisions
- **Debounced start/stop/modulate**: prevents relay chatter with configurable delays
- **Solar Done detection**: detects when solar generation has finished for the day
- **Enhanced import guard**: multi-stage protection (debounce → reduce → stop) with hysteresis
- **Dynamic measurement alignment**: adaptive skew detection and coherence scoring
- **Low-power protection**: force-charges if battery SoC drops below threshold, with solar forecast awareness
- **Persistent storage**: energy counters survive restarts, reloads, and reboots without ever briefly dropping to zero
- **Utility meters**: optional daily/monthly/yearly energy tracking sensors (standard HA helper, opt-in)
- **Mode tracking**: reason, source, timestamps, and transition history on every mode change
- **Solar-to-EV ratio**: lifetime percentage of solar energy that reached the EV vs. total produced (displayed as `%` with 2 decimal places)
- **Battery energy tracking**: live battery-side energy delta and AC→DC charging overhead per session (requires EV battery energy sensor)
- **Charging priority**: five selectable modes control whether surplus, export, or import is preferred — without affecting any monitoring sensor values
- **Expert mode**: unlocks advanced controller-tuning sensors, parameters, and priority bias
- **Full debug attributes**: every sensor exposes source values, mode and last action

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

If your sensor uses the opposite convention (positive = export), enable **Invert Net Power** to flip the sign automatically.

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
- **Battery Level Sensor** _(optional)_: `sensor` reporting battery SoC %
- **EV Battery Energy Sensor** _(optional)_: `sensor` reporting energy remaining in the battery (kWh). Enables live battery-side energy delta and AC→DC overhead tracking.
- **EV Energy Added Sensor** _(optional)_: `sensor` reporting session energy added by the charger (kWh). Used together with the battery energy sensor for capacity estimation.

### Step 5 – Charge Buffer, Hysteresis & Default Limit

| Parameter | Default | Description |
|-----------|---------|-------------|
| Charge Buffer | 0 % | Extra buffer above desired range |
| Range Hysteresis | 3.0 % | Dead zone to prevent start/stop oscillation near target |
| Default Charge Limit | 80 % | Vehicle charge limit % to reset to when cable is disconnected |

### Step 6 – Surplus Thresholds & Current Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| Surplus Start Threshold | 2 A | Minimum EMA current to start surplus charging |
| Surplus Stop Threshold | 1 A | EMA current below which surplus charging stops |
| Min Current | 0 A | Minimum charge current allowed |
| Max Current | 16 A | Maximum charge current allowed |

### Step 7 – Night Charging Window

Set the start and end times (HH:MM) for the night charging window. The **Charge Tonight** switch activates charging at the start time and auto-resets at the end time.

| Parameter | Default | Description |
|-----------|---------|-------------|
| Night Charging Start | 22:00 | Earliest time to start overnight charging |
| Night Charging End | 05:00 | Time at which Charge Tonight auto-disables |

### Step 8 – Solar & Forecast Sensors _(optional)_

One or more `sensor` entities for total solar yield (W or kW). Used to detect _Solar Done_ state and accumulate the solar-to-EV ratio.
Optionally add one or more **Remaining Forecast Today** sensors (e.g. Solcast `remaining_today`) — multiple values are summed.

### Step 9 – Actuators _(optional)_

- **Charge Switch**: `switch` entity to enable/disable the EVSE
- **Charge Current Number**: `number` entity to set the charge current (A)
- **Charge Limit Number** _(optional)_: `number` entity to set the vehicle's charge limit (%)

If left empty the integration tracks state internally but does not issue actual commands.

### Step 10 – Advanced Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| Smoothing Window | 120 s | Length of the rolling window for current averaging |
| Sample Interval | 10 s | How often sensor values are read and logic executed |
| Solar Done Threshold | 50 W | Solar production below which _solar done_ timer starts |
| Solar Done Duration | 600 s | How long production must stay below threshold before solar_done triggers |
| Start Delay | 30 s | Debounce before starting surplus charging |
| Stop Delay | 30 s | Debounce before stopping surplus charging |
| Modulate Min Interval | 30 s | Minimum time between current modulation calls |
| Import Guard Threshold | 200 W | Grid import above which the import guard activates |
| Import Guard Duration | 30 s | How long import must exceed threshold before action |
| Enable Utility Meters | off | Opt-in for daily/monthly/yearly period tracking sensors |

### Step 11 – Expert Mode _(optional)_

Enable **Expert Mode** to access advanced controller-tuning parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max Step (A) | 1 A | Maximum current change per modulation step |
| Hysteresis Up (A) | 0.2 A | Minimum EMA increase required before stepping up |
| Hysteresis Down (A) | 1.0 A | Minimum EMA decrease required before stepping down |
| Settling Duration (s) | 10 s | Post-commit window during which upward steps are suppressed |
| Priority Bias | 500 W | Power offset for `zero_prefer_export` / `zero_prefer_import` modes (converted to A at runtime) |

Expert mode also **automatically enables** two normally-hidden diagnostic sensors:
- **Alignment Diagnostics** — full alignment engine state
- **Input Skew** — real-time timestamp skew between net and EV power streams

These sensors are only enabled when expert mode is on. Turning expert mode off leaves them enabled so no data or dashboards are disrupted.

---

## How It Stabilizes

The controller uses a multi-layered approach to prevent flapping, overreaction, and stale-data faults:

### 1. Sample-Based Freshness

Every poll cycle updates the `last_seen` timestamp of each sensor — even when the value hasn't changed. This prevents false staleness detection when power is steady.

### 2. Dynamic Alignment

When the charger setpoint changes by a significant amount (>400W), the controller enters an **alignment phase**. During this phase:

- Upward current adjustments are blocked.
- Downward safety adjustments remain responsive.
- The phase ends when the net power sensor reacts in the expected direction, or when a dynamic timeout expires.

The timeout is learned from observed reaction lag: `timeout = min(max(2 × median_lag, 8s), 60s)`.

### 3. Settling Window

After every setpoint commit, a short settling window (10s) suppresses further upward steps. This prevents "self-induced dip" flapping where the controller reacts to the transient caused by its own action.

### 4. Measurement Coherence

The controller computes a **coherence score** (0–1) from the timestamp skew between net and EV power streams, and their individual reliabilities. When coherence is low, upward changes are gated.

### 5. Float-Based Control with Hysteresis

Internal calculations use float current values. Integer rounding only happens at the final actuator call. A configurable hysteresis dead zone prevents rapid toggling at integer boundaries.

### 6. Rate Limiting & Cooldown

- Max 1A change per step (configurable in Expert Mode).
- 45s minimum between upward steps.
- Downward steps and safety actions are immediate.

### 7. Confidence Gating

Each tick computes a confidence level (HIGH/MEDIUM/LOW) from data staleness, alignment state, settling state, and target stability. Upward changes require at least MEDIUM confidence.

### 8. Import Guard (Enhanced)

The import guard prevents grid import while surplus charging. It uses a multi-stage approach:

**Debounce**: Import must exceed the threshold (default 200W) for a sustained period (default 30s) before any action is taken. Short transient spikes are ignored.

**Escalation Ladder**:
1. **Reduce current** — decrease by 1A (soft mitigation)
2. **Settle window** — wait 30s to observe if net import improves
3. **Reduce to 0A** — if import persists, reduce to 0A (charger stays connected)
4. **Hard stop** — only if 0A doesn't resolve import, disable the charger relay

**Hysteresis**: Import must drop below `threshold − margin` (default 150W) for 20s before the guard clears. This prevents rapid flip-flop at the threshold boundary.

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

### 11. Persistent Storage & Reload Stability

Energy counters are persisted to `.storage/adaptive_charge.counters.<entry_id>` using HA's `Store` helper and are restored **before the first coordinator tick** on each reload. This means:

- `Energy Charged` (a `TOTAL_INCREASING` sensor) never briefly drops to 0 on reload — **utility meters tracking it will not incorrectly count the recovery as new energy**.
- `Solar to EV Ratio` caches its last known value and restores it immediately on reload, preventing a brief unavailable/zero window.
- Throttled writes (max once per 30s) avoid disk I/O spam.

### 12. Solar-to-EV Ratio & Solar Capture Factor

**Solar to EV Ratio** (sensor) is a **lifetime KPI** — it tracks what fraction of _all_ solar energy produced has actually reached the EV battery, across every charging session:

```
ratio = min(energy_solar_wh / solar_production_total_wh, 1.0)
```

This is displayed as a percentage (0–100 %) and serves as a **dashboard metric** for long-term self-consumption tracking.

**Solar Capture Factor** (attribute on the ratio sensor) is a **rolling operational metric** — it is an EMA of the per-session solar capture efficiency, updated at the end of each charging session. Low-power forecast logic uses this factor (not the lifetime KPI) to estimate how much of the remaining solar forecast will benefit the EV:

```
control_factor = solar_capture_factor if available, else lifetime ratio (fallback)
expected_ev_kwh = forecast_kwh × control_factor
```

This separation ensures that short-term conditions (clouds, shading changes) are reflected faster in charging decisions, while the lifetime KPI remains stable for historical analysis.

### 12a. Fail-Safe Behaviour (v4.3.0)

**Net power sensor invalid/unavailable**: when the net power sensor returns `unknown`, `unavailable`, or no value:
- The surplus calculation falls back to 0 W (safe default)
- Surplus **start** and **modulate-up** are **blocked** — the integration will not begin or increase charging based on an unreliable reading
- Safety actions (**stop**, **modulate-down**, **import guard**) continue to work normally
- A `net_power_valid` flag is exposed in the data dict and logged for diagnostics

**Cable sensor unknown**: surplus charging only starts when `cable_connected` is explicitly `True`. Unknown (`None`) or disconnected (`False`) both block surplus start. Force charge still works when triggered by `Charge Now`, `Charge Tonight`, or `import_priority`.

### 12b. Session Finalizer (v4.3.0)

Battery capacity estimation, overhead calculation, and solar capture factor are now finalized via a **central session finalizer** triggered by:
1. Cable disconnection
2. Stale charge detection (car stopped independently for >60 s)
3. HA shutdown/unload (best-effort)

This ensures session data is persisted even when the cable remains connected (e.g. car reaches its own charge limit).

### 13. Battery Energy Tracking _(optional)_

When the **EV Battery Energy Sensor** is configured:

- **Battery Energy Delta**: live reading of how much energy has entered the battery this session (`current_battery_kwh − session_start_battery_kwh`), updated every coordinator tick.
- **Charging Overhead**: rolling AC→DC conversion loss percentage, **updated live** during charging by blending the current session's partial data with the lifetime totals. Falls back to lifetime history-only when the cable is disconnected.

```
overhead% = (1 − battery_received_kwh / wall_energy_kwh) × 100
```

Live blending only activates once at least 0.5 kWh has been charged in the current session to avoid noisy readings at session start.

The live blend uses a **wall-energy snapshot** — the wall value captured at the exact moment the battery sensor last changed — rather than the continuously-growing live accumulator. This keeps the displayed overhead stable between car API polls (typically every ~3 minutes) and prevents a sawtooth pattern where the overhead would drift up between updates and snap back down on each new battery reading.

### 14. Charging Priority

The **Charging Priority** select entity (`select.adaptivecharge_charging_priority`) lets you shift the charging bias without affecting any monitoring sensor. `surplus_w`, `ema_current_a`, and all display values are always computed from real measurements; the priority only influences the internal control decision.

| Mode | Effect |
|------|--------|
| `balance` _(default)_ | Pure surplus — charges exactly when solar exceeds house load, targeting 0 W net. The original behaviour. |
| `zero_prefer_export` | Requires real surplus > **Priority Bias** (default 500 W) before starting or modulating. Biases slightly toward grid export. |
| `zero_prefer_import` | Starts and modulates even when importing up to **Priority Bias** (default 500 W). Biases slightly toward grid import. |
| `export_priority` | Stops any active surplus session and prevents new ones. All solar is exported. **Overridden** by `Charge Now`, `Charge Tonight`, and low-power protection when the solar forecast is insufficient. |
| `import_priority` | Permanent force-charge whenever the cable is connected — equivalent to leaving `Charge Now` on. Import guard is automatically bypassed. |

`zero_prefer_export` and `zero_prefer_import` are exact mirrors: same bias magnitude, opposite sign. The bias magnitude is set via **Priority Bias** in Expert Mode (0–2000 W, default 500 W).

#### Override hierarchy for `export_priority`

`export_priority` is a soft block — it only prevents *surplus* charging. The following always override it (highest to lowest priority):

1. **Charge Now** switch — force charge immediately
2. **Charge Tonight** switch — overnight range-target charging
3. **Low-power protection** — force charge when battery SoC is below the threshold *and* the solar forecast is genuinely insufficient to cover the deficit (the precise-mode forecast check determines this)



The **Alignment Diagnostics** sensor _(enabled automatically in Expert Mode)_ exposes:

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
| `sensor.adaptivecharge_net_surplus_excl_ev_w` | W | Surplus available for EV charging |
| `sensor.adaptivecharge_mode` | — | Current control mode (`force`, `surplus`, `stopped`, `night_target`, `off`) |
| `sensor.adaptivecharge_current_setting` _(diagnostic)_ | A | Last current value sent to charger |
| `sensor.adaptivecharge_available_current_decision` _(diagnostic)_ | A | EMA-smoothed available current used for decisions |
| `sensor.adaptivecharge_last_action` _(diagnostic)_ | — | Most recent control action |
| `sensor.adaptivecharge_last_reason` _(diagnostic)_ | — | Reason for last action |
| `sensor.adaptivecharge_import_guard_state` _(diagnostic)_ | — | Import guard state: `ok`, `reducing`, `stopped` |
| `sensor.adaptivecharge_version` _(diagnostic)_ | — | Integration version from manifest |
| `sensor.adaptivecharge_energy_charged_kwh` | kWh | Cumulative energy charged (TOTAL_INCREASING, utility-meter safe) |
| `sensor.adaptivecharge_solar_to_ev_ratio` | — | Lifetime fraction of solar production that reached the EV (0–1) |
| `sensor.adaptivecharge_range_upper_limit_km` | km | Range upper threshold — charging stops here |
| `sensor.adaptivecharge_range_lower_limit_km` | km | Range lower threshold — charging starts when below this |
| `sensor.adaptivecharge_alignment_diagnostics` _(diagnostic, expert)_ | — | Alignment engine internals; auto-enabled in Expert Mode |
| `sensor.adaptivecharge_input_skew` _(diagnostic, expert)_ | s | Timestamp skew between net and EV sensors; auto-enabled in Expert Mode |
| `sensor.adaptivecharge_charging_overhead_pct` _(optional)_ | % | Rolling AC→DC conversion loss %; live during charging session |
| `sensor.adaptivecharge_battery_energy_delta_kwh` _(optional)_ | kWh | Energy received by the battery this session (live, resets on cable plug-in) |

_Optional sensors are only created when the EV Battery Energy Sensor is configured._

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.adaptivecharge_force_charge` | True when Charge Now switch is on |
| `binary_sensor.adaptivecharge_charging_active` | True when actively controlling charging |
| `binary_sensor.adaptivecharge_low_power_active` | True when low-power protection is forcing a charge |

### Number Entities

| Entity | Range | Description |
|--------|-------|-------------|
| `number.adaptivecharge_desired_range_km` | 0–1000 km | Target range for overnight charging |

### Switches

| Entity | Description |
|--------|-------------|
| `switch.adaptivecharge_controller_enabled` | Master switch — enables/disables the charge controller |
| `switch.adaptivecharge_charge_now` | Force charge at maximum current immediately |
| `switch.adaptivecharge_charge_tonight` | Enable overnight charge-to-range scheduling |

### Select Entities

| Entity | Options | Description |
|--------|---------|-------------|
| `select.adaptivecharge_charging_priority` | `balance`, `zero_prefer_export`, `zero_prefer_import`, `export_priority`, `import_priority` | Charging priority mode (see §14) |

### Services

| Service | Description |
|---------|-------------|
| `adaptive_charge.force_start` | Enable Charge Now and start immediately |
| `adaptive_charge.force_stop` | Disable Charge Now and stop |
| `adaptive_charge.set_desired_range` | Set desired range (km) |
| `adaptive_charge.enable_tonight` | Turn on Charge Tonight |
| `adaptive_charge.disable_tonight` | Turn off Charge Tonight |

### Utility Meter Sensors _(opt-in)_

When **Enable Utility Meters** is turned on in Advanced Settings, these additional HA utility meter helpers are created, tracking the `Energy Charged` sensor:

| Entity | Period |
|--------|--------|
| `sensor.adaptivecharge_energy_charged_daily` | Daily |
| `sensor.adaptivecharge_energy_charged_monthly` | Monthly |
| `sensor.adaptivecharge_energy_charged_yearly` | Yearly |

> **Note**: Because `Energy Charged` is a `TOTAL_INCREASING` sensor and its value is now fully persisted before the first tick on every reload, these utility meters will never incorrectly accumulate energy during an integration restart.

---

## Mapping to Tesla / Tessie Entities

If you use [Tessie](https://tessie.com/) or the Tesla integration, map entities like this:

| AdaptiveCharge field | Tesla / Tessie entity |
|-------------------|-----------------------|
| EV Power Sensor | `sensor.my_car_charger_power` |
| Voltage Sensor | `sensor.my_car_charger_voltage` |
| Vehicle Presence | `device_tracker.my_car` |
| Cable Sensor | `binary_sensor.my_car_charging_cable_connected` |
| Current Range Sensor | `sensor.my_car_battery_range` |
| Battery Level Sensor | `sensor.my_car_battery_level` |
| EV Battery Energy Sensor | `sensor.my_car_energy_remaining` |
| EV Energy Added Sensor | `sensor.my_car_energy_added` |
| Charge Switch | `switch.my_car_charger` |
| Charge Current Number | `number.my_car_charging_amps` |
| Charge Limit Number | `number.my_car_charge_limit` |

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

**Charging keeps starting and stopping**
- Increase `start_delay` and `stop_delay` to add more hysteresis.
- Increase `smoothing_window` to smooth out short-term fluctuations.

**kW sensors not converting correctly**
- Ensure the sensor has `unit_of_measurement: kW` in its attributes.
- If missing, the heuristic (value < 20 → treat as kW) may misfire; add a unit attribute template sensor.

**Force charge not working**
- Ensure `Charge Switch` is configured and the entity is available.
- Check Home Assistant logs for service call errors.

**Utility meters jumped after an integration reload**
- This was a bug fixed in v4.1.5. The `Energy Charged` sensor now restores its value from the persistent store before the very first coordinator tick, so it never briefly shows 0 on reload.

**Charging Overhead or Battery Energy Delta showing Unknown after integration reload**
- Fixed in v4.1.6. The Battery Energy Delta sensor now lazily captures a new start snapshot the moment its source sensor becomes available — even if the integration was reloaded or restarted while the cable was already connected.

**Charging Overhead or Battery Energy Delta not updating during a session**
- The Battery Energy Delta is already updated live each coordinator tick.
- The Charging Overhead becomes live once ≥0.5 kWh has been charged in the current session (threshold avoids noisy early-session readings).
- Both sensors depend on the EV Battery Energy Sensor being configured and its update frequency — they are only as fresh as the source sensor.

**Charging Overhead shows a sawtooth / flickering pattern**
- Fixed in v4.2.2. The live overhead blend now uses a wall-energy snapshot (taken at the moment the battery sensor last changed) rather than the live wall accumulator. This prevents the ~2 pp sawtooth that previously appeared every time the car API delivered a new battery reading (~3 min apart).

**Alignment Diagnostics / Input Skew sensor not visible**
- These sensors are hidden by default. Enable **Expert Mode** in the integration options to make them appear automatically. Alternatively, enable them manually via **Settings → Devices & Services → AdaptiveCharge → Entities**.

---

## Changelog

### v4.3.0

**Critical fixes:**
- **Net sensor fail-safe**: invalid/unavailable net power sensor no longer treated as 0 W for control decisions. Surplus start and upward modulation are blocked; safety/downward actions remain active.
- **Cable guard strictened**: surplus start now requires `cable_connected = True`. Unknown (`None`) cable status blocks charging start (previously allowed as permissive fallback).
- **Solar ratio semantics split**: the lifetime Solar-to-EV Ratio remains a dashboard KPI. A new rolling Solar Capture Factor (EMA per session) is used for low-power forecast decisions, reflecting short-term conditions faster.
- **Session finalizer**: central `_finalize_session_if_needed()` ensures capacity estimates, overhead, and capture factor are persisted on cable disconnect, stale charge reset, _and_ HA shutdown — not only on cable-disconnect events.
- **Battery delta deduplication**: `_compute_session_battery_delta()` is called once per tick; the result is reused for both the data dict and overhead calculation, eliminating double sensor reads with side-effects.

**Improvements:**
- **Storage flush deduplication**: in-flight async flush task guard prevents parallel save scheduling.
- **Sensor clarity**: SolarToEvRatioSensor docstring and attributes updated; `solar_capture_factor` exposed as attribute.
- **38 new edge-case tests** covering net/cable unknown/unavailable, capture factor logic, session finalizer, and storage.

**Backward compatibility:**
- All existing entity IDs remain unchanged.
- `solar_capture_factor` is added as a new storage key (defaults to 0.0, merged on load from older stores).
- `net_power_valid` is a new key in the data dict (informational only — no entity change).
- Low-power forecast logic now prefers `solar_capture_factor`; it falls back to the lifetime ratio when the factor has not yet been populated (first session after upgrade).

**Migration notes:**
- No manual migration required. Upgrade and restart.
- The `solar_capture_factor` will be populated after the first completed charging session.
- Cable sensor: if you previously relied on surplus starting with an unknown cable state, this is now blocked. Ensure your cable sensor is configured and reporting correctly.

**Known limitations / follow-ups:**
- `solar_capture_factor` uses a session-level EMA — it does not weight by season or time-of-day. Future versions may add a sliding-window approach.
- The session finalizer is best-effort on shutdown; if HA crashes without a graceful shutdown, the most recent session data may be lost (same as before, but now also includes capture factor updates).
