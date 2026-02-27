# Manual Test Checklist — AdaptiveCharge

## How to Use
Run each scenario in a live Home Assistant instance with the integration configured.
Check each item once verified. Where entity names are listed, they are the friendly
names as registered under the integration device.

---

## 1. First Install / Default State

- [ ] After installing the integration, **Controller Enabled** switch is `off`
- [ ] **Charging Active** binary sensor is `off`
- [ ] **Mode** sensor shows `off`
- [ ] No `number.set_value` or `switch.turn_on/off` calls are made to the charger
  (verify via HA logbook or Developer Tools › Events)

---

## 2. Enable Controller — No Charging Without Surplus

- [ ] Turn on **Controller Enabled** switch
- [ ] **Mode** sensor shows `stopped` (not `surplus` or `force_max`)
- [ ] Verify: charger switch is NOT turned on automatically
- [ ] **"enable ≠ charge" proof**: controller enabled does not mean charging starts

---

## 3. Surplus Charging Start

- [ ] Ensure solar is generating excess (net export, e.g. −1500 W)
- [ ] **Available Current Decision** sensor shows ≥1 A
- [ ] After `start_delay` seconds (default 30 s), charger starts
- [ ] **Charging Active** binary sensor flips to `on`
- [ ] **Mode** sensor shows `surplus`
- [ ] **Last Action** sensor shows `start_surplus_XA`
- [ ] **Last Reason** sensor shows `surplus_above_threshold`
- [ ] **Current Setting** matches what was sent to the charger number entity

---

## 4. FORCE_MAX — enable ≠ surplus

- [ ] Turn on **Charge Now** switch
- [ ] **Mode** sensor shows `force_max` / charger starts at 16 A
- [ ] **"force_max ≠ surplus" proof**: current = 16 A regardless of surplus level
- [ ] **EV power is NOT added to surplus** calculation while in FORCE_MAX
  (verify **Net Surplus Excl EV** stays coherent, does not spike up)
- [ ] Turn off **Charge Now** → charger stops, mode → `stopped`

---

## 5. Controller Disable While Charging

- [ ] Start surplus charging (Mode = `surplus`)
- [ ] Turn off **Controller Enabled** switch
- [ ] Shutdown sequence runs: charger switch turns off
- [ ] Current is reset to 16 A (default)
- [ ] **Charging Active** → `off`
- [ ] **Mode** → `off`
- [ ] **Last Reason** → `controller_disabled`
- [ ] No further service calls are made after shutdown

---

## 6. Anti-Flip / Hysteresis

- [ ] With surplus around 1 A boundary (e.g. 200–300 W net export), observe
  **Mode** and **Current Setting** over 5 minutes
- [ ] No rapid on/off flipping should occur
- [ ] Current changes should be at most 1 A per `modulate_min_interval` (default 30 s)
- [ ] **"no flip around 1A/2A" proof**: chart **Current Setting** — no oscillation

---

## 7. Measurement Alignment (Skew Handling)

- [ ] With sensors updating at different intervals (e.g. net every 30 s, EV every 5 s):
  - [ ] **Input Skew** sensor shows the measured skew in seconds
  - [ ] **Input Skew** attribute `alignment_ok` is `true` when skew is within threshold
  - [ ] **Input Skew** attribute `alignment_reason` shows `ok` or the specific reason
- [ ] Simulate a skew spike (disconnect EV power sensor briefly):
  - [ ] `alignment_ok` → `false`, `alignment_reason` → `ev stale`
  - [ ] No current modulation occurs during this period ("mismatch → hold safe")
  - [ ] When skew drops back within threshold, modulation resumes

---

## 8. Import Guard (Enhanced)

- [ ] During surplus charging, manually create a 250 W import situation
  (e.g. turn on a high-power appliance)
- [ ] **Import Watts** sensor shows the import value
- [ ] After `import_guard_duration` seconds (default 30 s), observe:
  - [ ] Current is reduced by 1 A
  - [ ] **Import Guard State** sensor shows `reducing`
  - [ ] **Import Guard State** attribute `import_guard_reason` shows `sustained import Xs > 200W`
  - [ ] **Last Reason** shows `import_guard_reduce`
- [ ] If import persists, observe escalation:
  - [ ] Current reduces step-by-step with 30 s settle window between each step
  - [ ] At 0 A, charger relay turns off (hard stop), state shows `stopped`
- [ ] Remove the extra load:
  - [ ] After 20 s below 150 W (hysteresis margin), **Import Guard State** → `ok`
- [ ] **Short spike proof**: import for < 30 s does not trigger the guard
- [ ] **Hysteresis proof**: import between 150-200 W does not cause rapid clear/re-trigger

---

## 9. No Spam Calls

- [ ] While charging at a stable current in surplus mode:
  - [ ] Open HA logbook and observe `number.set_value` calls for the charger
  - [ ] No repeated identical calls should appear
  - [ ] **"no spam calls" proof**: calls appear only when current actually changes

---

## 10. Diagnostics Visibility

For each entity below, confirm it is available and has a sensible value:

- [ ] **Mode** (`off` / `surplus` / `force_max` / `stopped`)
- [ ] **Input Skew (s)** — skew between net and EV sensor timestamps
- [ ] **Net Update Interval (s)** — detected cadence of net power sensor
- [ ] **EV Update Interval (s)** — detected cadence of EV power sensor
- [ ] **Import Guard State** — `ok`, `reducing`, or `stopped`
- [ ] **Import Guard State** attributes: `import_guard_reason`, `time_in_import_state`
- [ ] **Import Watts (W)** — grid import used by guard
- [ ] **Last Action** — most recent control action
- [ ] **Last Reason** — reason for that action
- [ ] **Mode** attributes: `mode_reason`, `mode_source`, `mode_since`, `last_transition`
- [ ] **Target Current (A)** — EMA decision value before idempotency
- [ ] **Current Setting (A)** — last value actually sent to charger
- [ ] **Available Current Decision (A)** — EMA-smoothed available current
- [ ] **Charging Active** binary sensor — read-only, true when controller controls charging
- [ ] **Controller Enabled** switch — master on/off

---

## 11. Charge Tonight (Night Target Hook)

- [ ] Turn on **Charge Tonight** switch
- [ ] Set **Desired Range** to a value above current range
- [ ] Ensure **Vehicle Presence** and **Cable Connected** are `on`
- [ ] When solar is done (solar sensor below threshold for `solar_done_duration`),
  charging starts automatically at max current
- [ ] When `desired_range` is reached, charging stops
- [ ] At 05:00, **Charge Tonight** is automatically turned off

---

## 12. Charge Tonight Auto-Off

- [ ] Enable **Charge Tonight**, then unplug the cable:
  - [ ] **Charge Tonight** switch automatically turns off
  - [ ] Log shows `charge_tonight auto-off — cable unplugged`
- [ ] Enable **Charge Tonight** while solar_done is active, then solar recovers (on→off):
  - [ ] **Charge Tonight** switch automatically turns off
  - [ ] Log shows `charge_tonight auto-off — solar_done ended`
- [ ] Verify manual override: turning **Charge Tonight** back on after auto-off works

---

## 13. Mode Reason Tracking

- [ ] In each mode, verify **Mode** entity attributes:
  - [ ] `mode_reason` shows why the current mode is active
  - [ ] `mode_source` shows what triggered it (e.g. `auto_rule`, `charge_now_switch`, `user_toggle`, `import_guard`)
  - [ ] `mode_since` shows ISO timestamp of when mode was entered
  - [ ] `last_transition` shows previous→current mode with reason

---

## Tests Run (automated)

```
pytest tests/ -v
232 passed in 0.28s
```

All tests cover:
- Unit logic for kW→W conversion, surplus, raw current, smoothing
- Force charge state transitions
- Solar done detection
- Alignment engine (EMAFilter, MeasurementTracker, AlignmentEngine, skew, coherence, confidence)
- Hysteresis logic, rate limiting, import safety, idempotent commits, min on/off times
- Settling window / self-induced dip prevention
- Tracker freshness with constant values
- Universality (no Enphase/DSMR hardcoding — tested at 5, 10, 30, 60 s intervals)
- controller_enabled gate, shutdown sequence policy, FORCE_MAX vs surplus,
  idempotent current calls, last_reason tracking
- **NEW**: Enhanced import guard with debounce + hysteresis (transient vs sustained)
- **NEW**: Escalation ladder (reduce → settle → 0A → hard stop)
- **NEW**: Mode reason tracking (mode_reason, mode_source, mode_since, last_transition)
- **NEW**: Charge Tonight auto-off (cable unplug, solar_done on→off)
- **NEW**: Import guard state tracking (ok/reducing/stopped)

## Simulation Scenarios

| Scenario | Expected Result |
|---|---|
| Net export 1500 W, EV off | surplus≈1500W, starts after 30s |
| Net export 300 W, EV 2000W | surplus≈2300W, ≈3A |
| Charge Now on | current=16A, mode=force_max |
| Controller off while charging | shutdown sequence runs, mode=off |
| Import 250W for 15s | no action (guard not triggered, need 30s) |
| Import 250W for 35s | current−1A (escalation ladder starts) |
| Import 250W sustained | 5A→4A→3A→2A→1A→0A→stop (each step 30s apart) |
| Import drops to 100W | guard clears after 20s below 150W (hysteresis) |
| EV skew spike 20s | modulation blocked, hold safe |
| Repeated same target | 0 additional service calls |
| Cable unplug with Charge Tonight on | Charge Tonight auto-off |
| solar_done on→off with Charge Tonight on | Charge Tonight auto-off |
