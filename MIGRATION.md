# Migration Guide

## Upgrading to v4.1.5

### Bug Fixes

#### Energy Charged sensor no longer briefly shows 0 on reload

Previously, when the integration reloaded (e.g. after a config change or HA restart), the `Energy Charged` sensor would momentarily report `0.0 kWh` before restoring its previous value. Because `Energy Charged` is a `TOTAL_INCREASING` sensor, HA utility meters interpreted this as a counter reset followed by `X kWh` of new energy — causing daily/monthly/yearly meters to jump by the full session total.

**Fix**: Energy counters are now restored from the persistent store **before the first coordinator tick** on every reload. The sensor never sees 0 and utility meters are unaffected.

#### Solar to EV Ratio no longer briefly shows unavailable on reload

The `Solar to EV Ratio` sensor now caches its last known value and restores it immediately on reload, preventing a brief `unavailable` window that could distort energy statistics.

### Improvements

#### Charging Overhead is now live during a session

Previously, `Charging Overhead %` only updated once at the end of each charging session (on cable disconnect). It now **blends the current session's partial data** into the displayed value while the cable is connected, updating every coordinator tick. The live update activates once at least 0.5 kWh has been charged in the current session to avoid noisy early-session readings.

#### Expert mode automatically enables diagnostic entities

When **Expert Mode** is enabled, the `Alignment Diagnostics` and `Input Skew` sensors are now automatically enabled in the entity registry. Turning expert mode off leaves them enabled — no dashboards are disrupted.

### No Breaking Changes

- All existing entity IDs are preserved
- All existing config entries work without modification
- Utility meters created before this update will self-correct going forward; no manual reset is needed

---

## Upgrading to v1.2.0

### Deprecated Sensors

The following sensors have been **deprecated** (disabled by default for new installations).
They still exist and will continue to work for existing installations. If you have dashboards
using them, they will continue to function. However, we recommend migrating to the replacement sensors.

| Deprecated Sensor | Replacement | Reason |
|-------------------|-------------|--------|
| Available Current Raw (A) | EMA Current (A) | Controller now uses EMA-filtered current for all decisions |
| Available Charge Current Raw Floored (A) | Current Setting (A) | Raw floored is no longer used for control; use actual applied value |
| Available Charge Current Smoothed (A) | EMA Current (A) | Legacy smoothing replaced by EMA filter |
| Available Charge Current Smoothed Floored (A) | Current Setting (A) | Legacy smoothing replaced by EMA filter |

To re-enable a deprecated sensor, go to **Settings → Devices & Services → AdaptiveCharge → Entities** and enable it manually.

### Import Guard Changes

The import guard has been significantly improved:

| Parameter | Old Default | New Default | Why |
|-----------|-------------|-------------|-----|
| Threshold | 150 W | 200 W | Previous value was too sensitive, causing triggers on normal household loads |
| Duration | 10 s | 30 s | 10s was too short; transient spikes from appliances (kettle, oven, etc.) caused false triggers |

New configurable parameters (with safe defaults, backwards compatible):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `import_guard_hysteresis_w` | 50 W | Margin below threshold for clearing guard |
| `import_guard_clear_duration_s` | 20 s | How long import must stay below clear threshold |
| `import_guard_settle_s` | 30 s | Settle window between escalation steps |

**Behavior change**: Instead of immediately stopping the charger when current reaches 1A, the controller now:
1. Reduces current by 1A, then waits 30s (settle window)
2. Reduces further if import persists
3. Reduces to 0A (charger stays connected)
4. Only hard-stops (relay off) if 0A doesn't resolve import

### New Sensor Attributes

**Import Guard State** sensor now shows three states: `ok`, `reducing`, `stopped` (was: `ok`, `active`).
New attributes: `import_guard_reason`, `time_in_import_state`.

**Mode** sensor now has attributes: `mode_reason`, `mode_source`, `mode_since`, `last_transition`.

### Charge Tonight Auto-Off

**Charge Tonight** now automatically disables when:
- EV cable is unplugged
- Solar done condition ends (solar production recovers)

This was previously only reset at 05:00. The 05:00 reset still exists as a fallback.

### No Breaking Changes

- All existing entity IDs are preserved
- All existing config entries work without modification
- New defaults apply only to new installations; existing configs keep their values
