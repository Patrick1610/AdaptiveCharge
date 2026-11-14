# Wishlist — Future Optimizations

Ideas for future improvements that go beyond the current PR scope.
Sorted by estimated impact.

---

## AdaptiveCharge 5.x — requested optimizations

### Automatic planning power from Enphase operating limits

Requested on 2026-11-14; **not implemented**. The current `planning_power()`
uses the configured charger power and an optional fixed vehicle cap; it does
not consume Enphase safe-mode status or learn available charging capacity.

Desired behavior for both Tesla and ID.3:

- Plan supplemental Manual charging at the available Enphase capacity. With
  three phases at nominal 230 V, 8 A per phase is about **5.52 kW**, and 16 A
  per phase is about **11.04 kW**. Prefer reported voltage, phase count and
  operating limits when available rather than assuming a fixed 5.2 kW.
- A reported Enphase safe-mode limit reduces planning power; confirmed normal
  operation restores the normal 16 A three-phase assumption, bounded by the
  installation and actual vehicle limits. Separate these operating limits from
  an intentional vehicle cap so an old test value does not permanently constrain
  normal planning.
- Recalculate the remaining windows, finish estimate and any deadline shortfall
  when the effective capacity changes. Preserve the command, departure and energy
  accounting; capacity changes do not create a new Start or cancel a session.
- Do not infer maximum capacity from low Green surplus, a phase-switch pause,
  zero power, or battery taper. Stable Manual measurements can validate the
  assumed capacity; they are not by themselves proof of safe mode.
- Verify which Enphase HA entities expose safe mode and effective limits before
  implementation. If unavailable, retain a documented planning fallback with
  an explicit uncertainty warning; missing status must not itself block charging
  or erase a command. Delayed VW measurements are not a capacity gate.
- Keep Enphase responsible for phases, amperage and electrical protection.
  This optimization changes the planning assumption, not those actuator settings.
- Expose the effective planning kW and its source/reason in sensor attributes.

Acceptance checks: normal → safe → normal replanning, one- and three-phase
limits, vehicle caps, missing status and restart recovery, plus Green low-power
and phase-pause cases that must not incorrectly reduce the normal capacity.
Verify unchanged energy allowance and no new Start/cancellation or amp/phase writes.

Reference: [Enphase dynamic load balancing technical brief](https://enphase.com/media/23317)
documents an 8 A fail-safe limit on IQ Gateway/local-connection failure.

### Preserve commands across charger/Gateway faults and restarts

Clarified by Patrick on 2026-11-14: this concerns loss/reconnection/restart of
the **charger or Gateway**, not physically unplugging the vehicle cable.
Safe mode, communication loss, device restart and a temporary charging pause
must not terminate the command or reset its energy allowance. Resume the same
command and replan when capacity/status recovers. A confirmed physical cable
disconnect, vehicle swap or explicit Stop still ends the old command.

Status: requirement recorded; full fault/restart behavior is **not accepted yet**.
The b7 Tesla active-restart trial failed with actuator feedback timeout. The b8
ID.3 trial verified the same command and physical charging after HA restarted;
this does not prove charger/Gateway restart recovery or Tesla recovery on b8.
Do not infer a physical cable replacement from a changed device session timestamp
alone without checking that source's meaning. A genuine reconnect must still be
distinguished from a fault; never transfer a command to another vehicle.

Acceptance: independently test HA restart, Gateway loss/recovery and charger
restart, including changed/missing device session timestamps and delayed vehicle
updates. Check retained command/departure/energy, automatic recovery without a
new Start, and that physical unplug/vehicle swap and Stop retain priority.

### Consolidated iteration proposal before the next acceptance run

Patrick requested this concrete list first on 2026-11-14, then one coherent code
iteration followed by tests. This is a proposal, **not an installed release**.

1. Implement automatic planning capacity as specified above. Account for an
   intentional Tesla current limit too: Enphase normal/16 A does not guarantee
   11 kW if the vehicle itself is restricted to 8 A. Remove obsolete test-only
   planning overrides without silently increasing a real vehicle limit.
2. Harden command continuity across charger/Gateway faults, changing device
   timestamps and HA restart as specified above. Temporary unknown data can wait
   for recovery but cannot silently cancel or grant a fresh energy allowance.
3. Complete single-controller commissioning: audit live old automations/scripts,
   their callers and actual actuator targets; archive and disable obsolete control
   automations while retaining the price/forecast/measurement sources needed by v5.
   Verify no old script is running, no old request is active/pending and the new
   command/generation survives isolation. Old EV helpers alone are not proof of
   isolation from independent legacy Tesla automations.
4. Keep Enever Zonneplan quarter prices as the current import source. Retain the
   no-price departure fallback and automatic repricing. Add clear capacity-source,
   recovery-state and cost-completeness attributes; unknown export value must
   continue to permit normal import-price planning.
5. Design Zonneplan contract costs separately: avoid duplicating purchase fees,
   distinguish energy tax/saldering from market value, apply daylight/positive-base
   and annual-cap rules for Zonnebonus. Full export-now/buy-later optimization is
   not achieved by adding a sell-price field: the current planner sorts on buy
   prices and assumes autonomous Green as its baseline. Scope this explicitly
   before claiming full economic optimization; do not silently add a solar actuator.
   See [the complete decision proposal](docs/ECONOMISCHE_PLANNING.md) for required
   inputs, 2026/2027 tax treatment, bounded cost-only scope versus active Green
   deferral, acceptance tests and effort estimates. Neither variant is installed.
   Patrick subsequently narrowed the candidate to command-bound economic Green
   deferral with sufficient known marginal replacement capacity and rolling price
   coverage. The proposal now details this compact variant separately from the
   earlier broad optimizer, including revised effort and illustrative benefits.
6. Retest the existing short-block and continuous-quarter handling rather than
   rewriting it: minimum three minutes, cheapest feasible adjacent quarter,
   preserved energy budget, no repeated short Manual/Green oscillations.

Single-controller audit completed on 2026-11-14: inspected 174 automations and
64 scripts. All 12 identified legacy EV control automations are now off (seven
disabled in this pass, five already off); the seven associated legacy EV scripts
are idle. Their configurations and before/after evidence were archived in HA at
`/config/ev_v2_control_20261106/adaptive_charge_preview_20261113/legacy_isolation_20261114/receipt.json`.
The receipt is `verified_native_isolated`: all legacy automations off, scripts
idle, current Tesla generation 6 command preserved, and no new control fault.
Scripts remain installed for recoverability; this does not claim that a manually
invoked legacy script is impossible. No integration code upgrade or new physical
acceptance test was performed as part of this isolation.

Test order after implementation, review and release/installed-file verification:

- Software: capacity normal/safe/vehicle limits, missing/stale sources, restart
  persistence, changed device session timestamps, physical unplug and Stop races;
  tariff gaps/fallback/return and minimum-block/energy conservation regressions.
- Tesla with the actual connected vehicle: independently verify cheapest quarters
  and physical power, target raise/lower and departure changes on the same command,
  a true goal stop versus tolerance/energyguard, and post-goal bounded refill.
- Retry active Tesla HA-restart recovery on the corrected release; the failed b7
  result remains failed. Test charger/Gateway fault recovery separately; successful
  HA restart is not proof of those faults.
- Verify explicit Stop prevents old timers from resuming. Physical disconnect and
  Tesla/ID.3 swap need an actual cable event; coordinate those steps with Patrick.
- Observe physical safe mode and real low-surplus Green pauses when available;
  simulated software inputs are not physical acceptance. Do not force arbitrary
  hardware/network failures or manufacture vehicle values to obtain a pass.
- Use a finite test receipt with actual start/end, energy ceiling and current
  vehicle limits before execution. Preserve the user's live command until the
  concrete test sequence takes ownership; do not silently move its departure.

---

## High Impact

### 1. Smart Night Charging with Tariff Awareness
Use dynamic electricity tariffs (e.g. Tibber, ENTSO-E) to schedule overnight charging at the cheapest hours instead of simply charging from `tonight_start` to `night_off`. Could integrate with existing forecast sensors.

### 2. Battery SoC-Based Charging Logic
Instead of relying only on range (km), allow direct SoC percentage targets. Some EV APIs report SoC more reliably than estimated range. Add a `CONF_TARGET_SOC` config option alongside the existing range target.

### 3. Forecast-Based Pre-Emptive Charging
Use solar forecast data (already wired via `CONF_FORECAST_SENSORS`) to decide:
- Skip night charging if tomorrow's forecast covers the range deficit
- Start charging earlier if tomorrow is cloudy and surplus won't cover need
- Show a "forecast confidence" indicator

### 4. Multi-Phase Support (1P/3P Detection)
Currently hardcoded to 3-phase (`surplus_w / (voltage × 3)`). Add a config option for phase count (1, 2, or 3) so the integration works correctly for single-phase installations common in many countries.

---

## Medium Impact

### 5. Energy Dashboard Integration
Expose `energy_total_wh`, `energy_solar_wh`, and `energy_import_wh` as proper HA energy sensors with `state_class: total_increasing` so they appear in the Home Assistant Energy dashboard automatically.

### 6. Auto-Detect Charge Completion
Detect when the EV stops drawing power (EV power drops to 0 while cable connected and charging was active) and automatically switch mode to `stopped`. Currently relies on the EV or charger to signal completion.

### 7. Configurable Alignment Engine Parameters
Expose alignment engine tuning knobs (EV step threshold, timeout min/max, EMA span) in the options flow for advanced users. Currently these use hardcoded defaults from `const.py`.

### 8. WebSocket / Push-Based Sensor Updates
Replace polling (`async_track_time_interval`) with state change listeners (`async_track_state_change_event`) for the key power sensors. This would reduce latency and eliminate the fixed sample interval limitation.

### 9. Charge Session Tracking
Track individual charge sessions (start time, end time, energy charged, solar fraction, cost estimate) and expose them as a sensor with history. Useful for EV charging analytics.

---

## Lower Impact / Nice-to-Have

### 10. Config Flow Input Validation Improvements
- Validate that selected sensor entities actually exist and are available during config flow
- Warn if net power sensor doesn't have expected `unit_of_measurement`
- Preview computed surplus during setup to verify sign convention

### 11. Localization (i18n)
Add translations for Dutch (`nl.json`), German (`de.json`), and French (`fr.json`) since the user base is primarily European.

### 12. Graphical Debugging Dashboard
Provide a default Lovelace dashboard YAML snippet that shows all key sensors, mode state, and import guard status in a single view. Include example automations.

### 13. HACS Default Repository
Once stable, apply for inclusion in the HACS default repository list so users don't need to add it as a custom repository.

### 14. Config Entry Migration Version Bump
Add `async_migrate_entry()` to handle schema changes (e.g. if `CONF_CHARGE_LIMIT_SENSOR` needs to be renamed to `CONF_CHARGE_LIMIT_NUMBER` for existing installs). Currently relies on backwards-compatible optional fields.

### 15. Rate Limit Service Calls Across Integrations
Some chargers (e.g. Easee, Zaptec) have API rate limits. Add a configurable minimum interval between `number.set_value` / `switch.turn_on` calls to avoid hitting these limits.

### 16. Automated Integration Tests
Add integration tests that use `pytest-homeassistant-custom-component` to test the full lifecycle (setup → config flow → state updates → teardown) against a mock HA instance.

---

_Last updated: 2026-03-02_
