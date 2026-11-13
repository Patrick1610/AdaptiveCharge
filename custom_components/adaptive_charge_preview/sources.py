"""Read existing HA states. This module has no service, network or refresh calls."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .core.models import Measurement, Tariff, VehicleSnapshot, finite, timestamp
from .const import DOMAIN, MAX_POWER_AGE


class SourceBindingError(ValueError):
    """Identify a selected field that would feed preview output back in."""

    def __init__(self, field: str, entity_id: str) -> None:
        self.field = field
        super().__init__(f"Preview entities cannot be input sources: {field}={entity_id}")


def validate_source_bindings(hass: HomeAssistant, configurations: list[dict[str, Any]]) -> None:
    """Refuse preview feedback loops without changing any entity registration."""
    registry = hass.data.get(er.DATA_REGISTRY)
    if registry is None or not hasattr(registry, "entities"):
        return  # Small pure observer tests need no loaded entity registry.
    for config in configurations:
        for key, entity_id in config.items():
            if key.endswith("_entity") and entity_id:
                registered = registry.async_get(entity_id)
                if registered is not None and registered.platform == DOMAIN:
                    raise SourceBindingError(key, entity_id)


def state(hass: HomeAssistant, entity_id: str | None) -> State | None:
    """Read only an explicitly selected source."""
    return hass.states.get(entity_id) if entity_id else None


def observed_at(source: State | None) -> float | None:
    """HA reception time, never substituted for vehicle measurement time."""
    return source.last_reported.timestamp() if source is not None else None


def capture_at(source: State | None) -> float | None:
    """Only documented upstream attributes count as vehicle capture timestamps."""
    if source is None:
        return None
    for key in ("data_captured_at", "carCapturedTimestamp", "measured_at"):
        if (parsed := timestamp(source.attributes.get(key))) is not None:
            return parsed
    return None


def measurement(hass: HomeAssistant, entity_id: str | None, common_capture: float | None = None) -> Measurement:
    """Preserve original units and separate capture from reception."""
    source = state(hass, entity_id)
    if source is None:
        return Measurement(None, entity_id or "")
    return Measurement(finite(source.state), entity_id or "", source.attributes.get("unit_of_measurement"), capture_at(source) or common_capture or tessie_capture(hass, source), observed_at(source))


def tessie_capture(hass: HomeAssistant, source: State) -> float | None:
    """Read the selected Tessie entity's charge-state timestamp, without I/O.

    HA 2026.11.1 exposes the charge values as sensors but not their upstream
    timestamp attributes. Its existing entity coordinator retains that timestamp.
    Match the exact published value before attaching it; never use last_reported.
    This compatibility adapter reads no credentials and never refreshes Tessie.
    """
    registry = hass.data.get(er.DATA_REGISTRY)
    registered = registry.async_get(source.entity_id) if registry and hasattr(registry, "entities") else None
    if registered is None or registered.platform != "tessie":
        return None
    component = hass.data.get(DATA_INSTANCES, {}).get(source.domain)
    entity = component.get_entity(source.entity_id) if component else None
    coordinator = getattr(entity, "coordinator", None)
    if coordinator is None or not type(coordinator).__module__.startswith("homeassistant.components.tessie.") or not coordinator.last_update_success:
        return None
    data = coordinator.data
    description = getattr(entity, "entity_description", None)
    key = getattr(description, "data_key", None) or getattr(description, "key", None)
    if not isinstance(data, dict) or key not in {"charge_state_usable_battery_level", "charge_state_battery_level", "charge_state_energy_remaining", "charge_state_est_battery_range", "charge_state_battery_range", "charge_state_ideal_battery_range"}:
        return None
    value, published = finite(data.get(key)), finite(source.state)
    if value is None or published is None:
        return None
    if "range" in key and source.attributes.get("unit_of_measurement") == "km":
        value *= 1.609344
    if abs(value - published) > max(0.005, abs(value) * 0.0001):
        return None
    captured = finite(data.get("charge_state_timestamp"))
    if captured is None:
        return None
    return timestamp(captured / 1000 if captured > 100_000_000_000 else captured)


def boolean(source: State | None) -> bool | None:
    """Unknown/unavailable is neither connected nor disconnected."""
    return {"on": True, "off": False, "home": True, "not_home": False}.get(source.state) if source else None


def connection_identity(hass: HomeAssistant, charger: dict[str, Any]) -> str | None:
    """One attachment-token adapter for observation and every executor check."""
    token = state(hass, charger.get("connection_entity"))
    source = token or state(hass, charger.get("plug_entity"))
    attribute = charger.get("connection_attribute", "")
    value = source.attributes.get(attribute) if source and attribute else token.state if token else None
    return str(value) if type(value) in (str, int, float) and str(value) not in {"unknown", "unavailable", ""} else None


def vehicle_snapshot(hass: HomeAssistant, profile: dict[str, Any], charger: dict[str, Any]) -> VehicleSnapshot:
    """Observe car-specific cable plus shared connection identity."""
    common = state(hass, profile.get("measurement_time_entity"))
    common_capture = timestamp(common.state) if common else None
    cable = state(hass, profile.get("cable_entity"))
    shared = state(hass, charger.get("plug_entity"))
    plug_token = state(hass, charger.get("connection_entity"))
    home = state(hass, profile.get("home_entity"))
    connected = boolean(cable) if cable else None
    if boolean(shared) is False:
        connected = False
    connection_id = connection_identity(hass, charger)
    # An edge time is safer than treating an old cached cable value as a new event.
    edge_times = [s.last_updated.timestamp() for s in (cable, shared, plug_token) if s is not None]
    charger_report = state(hass, charger.get("control_report_entity"))
    if charger_report and (reported := timestamp(charger_report.state)) is not None:
        edge_times.append(reported)
    soh = measurement(hass, profile.get("soh_entity"), common_capture) if profile.get("soh_entity") else Measurement(100.0, unit="%", measured_at=common_capture)
    return VehicleSnapshot(
        measurement(hass, profile.get("soc_entity"), common_capture),
        measurement(hass, profile.get("battery_entity"), common_capture),
        measurement(hass, profile.get("range_entity"), common_capture),
        measurement(hass, profile.get("limit_entity"), common_capture),
        soh, connected, boolean(home) if home else None, connection_id,
        max(edge_times) if edge_times else None,
        boolean(shared),
    )


def meter_kwh(hass: HomeAssistant, entity_id: str | None, now: float) -> float | None:
    """A stale/unknown meter cannot advance an energy budget."""
    m = measurement(hass, entity_id)
    if m.value is None or m.value < 0 or m.unit != "kWh" or m.received_at is None or not -60 <= now - m.received_at <= 600:
        return None
    return m.value


def power_kw(hass: HomeAssistant, entity_id: str | None, now: float) -> dict[str, Any]:
    """Independent physical indication; no automatic action follows zero power."""
    m = measurement(hass, entity_id)
    value = m.value / 1000 if m.value is not None and m.unit == "W" else m.value if m.unit == "kW" else None
    fresh = m.received_at is not None and -60 <= now - m.received_at <= MAX_POWER_AGE
    valid = value is not None and -0.05 <= value <= 22 and fresh
    return {"kw": value if valid else None, "received_at": m.received_at, "fresh": valid, "positive": bool(valid and value >= 0.2)}


def local_time(value: Any, timezone: str) -> float | None:
    """Parse a helper/price time using HA's timezone, rejecting ambiguous DST."""
    if isinstance(value, datetime):
        value = value.isoformat()
    direct = timestamp(value)
    if direct is not None:
        return direct
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        zone = ZoneInfo(timezone)
        if dt.replace(tzinfo=zone, fold=0).utcoffset() != dt.replace(tzinfo=zone, fold=1).utcoffset():
            return None
        result = dt.replace(tzinfo=zone).timestamp()
        return result if datetime.fromtimestamp(result, zone).replace(tzinfo=None) == dt else None
    except (ValueError, KeyError):
        return None


def price_rows(hass: HomeAssistant, config: dict[str, Any], now: float) -> tuple[list[Tariff], list[str]]:
    """Adapt explicit interval prices, including Enever's all-in price column.

    Missing end times use an explicitly configured interval; a price gap is not
    filled to the next sample. Export is a separate configured net price column.
    """
    source = state(hass, config.get("price_entity"))
    if source is None:
        return [], ["prices_missing"]
    if source.state in {"unknown", "unavailable"}:
        return [], ["prices_unavailable"]
    raw = []
    for attribute in config.get("price_attribute", "data").split(","):
        part = source.attributes.get(attribute.strip(), [])
        if not isinstance(part, (list, tuple)):
            return [], ["invalid_price_rows"]
        raw.extend(part)
    warnings: list[str] = []
    rows = []
    duration = float(config.get("price_interval_min", 15)) * 60
    export_key = config.get("export_price_key", "")
    if not export_key:
        warnings.append("export_price_unknown_cost_estimate_incomplete")
    # Forecast input is the existing aggregated hourly Wh dictionary. It is
    # advisory only; stale forecasts are discarded, never refreshed by preview.
    forecast = state(hass, config.get("forecast_entity"))
    forecasts: dict[float, float] = {}
    if forecast:
        forecast_at = timestamp(forecast.state)
        if forecast_at is not None and -60 <= now - forecast_at < 7200:
            periods = forecast.attributes.get("wh_period") or {}
            if not isinstance(periods, dict):
                periods = {}
                warnings.append("forecast_invalid_ignored")
            for key, value in periods.items():
                t, wh = local_time(key, hass.config.time_zone), finite(value)
                if t is not None and wh is not None and wh >= 0:
                    forecasts[t] = wh / 1000
        else:
            warnings.append("forecast_stale_ignored")
    house_measurement = measurement(hass, config.get("house_entity"))
    house = house_measurement.value
    if house_measurement.unit == "W" and house is not None:
        house /= 1000
    elif house_measurement.unit != "kW":
        house = None
    if house_measurement.received_at is None or now - house_measurement.received_at > 600:
        house = None
    house = max(0.0, house) if house is not None else float(config.get("house_kw", 0.5))
    seen: dict[tuple[float, float], tuple[float, float]] = {}
    for row in raw:
        if not isinstance(row, dict):
            return [], ["invalid_price_rows"]
        a = local_time(row.get(config.get("price_time_key", "time")), hass.config.time_zone)
        b = local_time(row.get("end"), hass.config.time_zone) if "end" in row else a + duration if a is not None else None
        buy = finite(row.get(config.get("import_price_key", "price")))
        sell = finite(row.get(export_key)) if export_key else 0.0
        if a is None or b is None or buy is None or sell is None or b <= a:
            return [], ["invalid_price_rows"]
        key = a, b
        if key in seen:
            if seen[key] != (buy, sell):
                return [], ["conflicting_price_rows"]
            continue
        seen[key] = buy, sell
        hour = int(a // 3600) * 3600
        solar = max(0.0, forecasts.get(float(hour), 0.0) - house)
        rows.append(Tariff(a, b, buy, sell, solar))
    return rows, warnings


def price_feed_live(hass: HomeAssistant, config: dict[str, Any], now: float, rows: list[Tariff] | None = None, warnings: list[str] | None = None) -> bool:
    """Available feed with valid intervals covering now; cached unavailable is not live."""
    if rows is None or warnings is None:
        rows, warnings = price_rows(hass, config, now)
    source = state(hass, config.get("price_entity"))
    return bool(source and source.state not in {"unknown", "unavailable"} and rows and not any(w in warnings for w in ("prices_missing", "prices_unavailable", "invalid_price_rows", "conflicting_price_rows")) and any(row.start <= now < row.end for row in rows))
