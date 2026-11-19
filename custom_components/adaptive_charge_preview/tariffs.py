"""Normalize explicitly configured contract components without double taxation."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.helpers.sun import get_astral_event_date

from .core.models import Tariff, finite

ZONNEPLAN_BONUS = .10
ZONNEPLAN_BONUS_CAP_KWH = 7500


def export_tariffs(hass: Any, rows: list[Tariff], config: dict[str, Any], now: float) -> tuple[list[Tariff], list[str]]:
    """Derive export only from known included tax and dated contract settings.

    The input buy is already all-in. Subtract its included tax and import fee,
    then add the separately configured export fee. No 2026 tax is extrapolated.
    Unknown bonus entitlement uses the base export value, visibly conservative;
    a year-bound counter and physical maximum export bound can prove headroom.
    """
    if config.get("export_contract", "unknown") != "zonneplan":
        return rows, []
    tax = finite(config.get("zonneplan_tax_eur_kwh"))
    import_fee = finite(config.get("zonneplan_import_fee_eur_kwh", .02))
    export_fee = finite(config.get("zonneplan_export_fee_eur_kwh", .02))
    try:
        zone = ZoneInfo(hass.config.time_zone)
        expires = datetime.fromisoformat(str(config.get("zonneplan_tax_valid_until", ""))).date()
    except (ValueError, KeyError):
        return rows, ["export_contract_tax_unconfirmed"]
    if tax is None or not 0 <= tax <= 1 or import_fee is None or export_fee is None or not 0 <= import_fee <= 1 or not -1 <= export_fee <= 1:
        return rows, ["export_contract_tax_unconfirmed"]
    if any(datetime.fromtimestamp(r.end-.001, zone).date() > expires for r in rows if r.end > now):
        return rows, ["export_contract_tax_expired"]
    warnings = []
    counter = hass.states.get(config.get("bonus_export_ytd_entity", ""))
    used = finite(counter.state) if counter else None
    year = datetime.fromtimestamp(now, zone).year
    confirmed_counter = bool(counter and counter.attributes.get("unit_of_measurement") == "kWh" and used is not None and used >= 0 and
                             counter.attributes.get("year") in (None,year) and (counter.attributes.get("year") == year or config.get("bonus_export_year") == year) and
                             -60 <= now-counter.last_reported.timestamp() <= 36*3600)
    maximum = finite(config.get("maximum_export_kw", 22))
    allowance = max(0, ZONNEPLAN_BONUS_CAP_KWH-used) if confirmed_counter else None
    entitled = config.get("zonneplan_bonus_eligible") is True
    if entitled and allowance is None:
        warnings.append("zonnebonus_counter_unknown_using_base_export")
    days = {}
    result = []
    for row in rows:
        day = datetime.fromtimestamp(row.start, zone).date()
        if day not in days:
            rise, setting = get_astral_event_date(hass,"sunrise",day), get_astral_event_date(hass,"sunset",day)
            days[day] = (rise.timestamp(),setting.timestamp()) if rise and setting else None
        daylight = days[day]
        cuts = sorted({row.start,row.end,*([v for v in daylight if row.start < v < row.end] if daylight else [])})
        for a,b in zip(cuts,cuts[1:]):
            base = row.buy-tax-import_fee+export_fee
            in_day = bool(daylight and daylight[0] <= a < daylight[1])
            same_year = datetime.fromtimestamp(b-.001,zone).year == year
            # Worst-case export since the actual counter sample, not planned
            # export: a plan can never spend or reset the real annual allowance.
            sample = counter.last_reported.timestamp() if confirmed_counter else now
            headroom = allowance is not None and maximum is not None and 0 < maximum <= 100 and allowance >= maximum*max(0,b-sample)/3600
            bonus = ZONNEPLAN_BONUS*max(0,base) if entitled and in_day and same_year and headroom else 0
            if entitled and in_day and base > 0 and not headroom and allowance != 0:
                warnings.append("zonnebonus_headroom_unconfirmed_using_base_export")
            result.append(replace(row,start=a,end=b,sell=base+bonus))
    return result, list(dict.fromkeys(warnings))


def export_is_known(warnings: list[str]) -> bool:
    """A conservative, disclosed bonus base still has a valid export value."""
    return not any(w in warnings for w in ("export_price_unknown_cost_estimate_incomplete", "export_contract_tax_unconfirmed", "export_contract_tax_expired"))
