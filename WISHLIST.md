# Wishlist — Future Optimizations

Ideas for future improvements that go beyond the current PR scope.
Sorted by estimated impact.

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
