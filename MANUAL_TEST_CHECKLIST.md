# Manual Test Checklist — Stormbreaker Surplus EV Charge

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

## 8. Import Guard

- [ ] During surplus charging, manually create a 200 W import situation
  (e.g. turn on a high-power appliance)
- [ ] **Import Watts** sensor shows the import value
- [ ] After `import_guard_duration` seconds (default 10 s), observe:
  - [ ] Current is reduced by 1 A, OR charging stops if already at 1 A
  - [ ] **Import Guard State** sensor shows `active`
  - [ ] **Last Reason** shows `import_guard`
- [ ] Remove the extra load: import guard resets, **Import Guard State** → `ok`
- [ ] **Short spike proof**: import for < 10 s does not trigger the guard

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
- [ ] **Import Guard State** — `ok` or `active`
- [ ] **Import Watts (W)** — grid import used by guard
- [ ] **Last Action** — most recent control action
- [ ] **Last Reason** — reason for that action
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

## Tests Run (automated)

```
pytest tests/ -v
153 passed in 0.16s
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
- **NEW**: controller_enabled gate, shutdown sequence policy, FORCE_MAX vs surplus,
  idempotent current calls, import guard (short spike / sustained), last_reason tracking

## Simulation Scenarios

| Scenario | Expected Result |
|---|---|
| Net export 1500 W, EV off | surplus≈1500W, starts after 30s |
| Net export 300 W, EV 2000W | surplus≈2300W, ≈3A |
| Charge Now on | current=16A, mode=force_max |
| Controller off while charging | shutdown sequence runs, mode=off |
| Import 200W for 3s | no action (guard not triggered) |
| Import 200W for 12s | current−1A or stop (import guard) |
| EV skew spike 20s | modulation blocked, hold safe |
| Repeated same target | 0 additional service calls |
