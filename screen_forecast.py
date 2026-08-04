"""Reduce an NWS hourly forecast to a climate day's high and low.

NWS hourly temperatures already arrive in Fahrenheit, so no conversion happens
here -- only windowing onto the right day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_MONTHS = {m.upper(): i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def climate_day_of_ticker(ticker: str):
    """The settlement day a Kalshi ticker refers to, from its middle segment.

    Parsed from the ticker rather than derived from close_time on purpose:
    KXHIGHAUS-26AUG04-T97 closes at 2026-08-05T05:59Z, which is Aug 5 in the
    city's LOCAL time and only resolves to Aug 4 in fixed standard time. The
    ticker states the day outright, so use it."""
    parts = (ticker or "").split("-")
    if len(parts) < 2 or len(parts[1]) != 7:
        return None
    stamp = parts[1].upper()
    try:
        year = 2000 + int(stamp[:2])
        month = _MONTHS[stamp[2:5]]
        day = int(stamp[5:])
        return date(year, month, day)
    except (ValueError, KeyError):
        return None


def lst_offset_hours(tzname: str) -> int:
    """UTC offset of the zone's STANDARD time, in whole hours.

    Taken in January, when no US zone is on daylight time. The climate day is a
    fixed-LST window; using the current local offset would shift the day
    boundary by an hour for half the year."""
    january = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(tzname))
    return int(january.utcoffset().total_seconds() // 3600)


def daily_extremes(periods: list, day: date, tzname: str) -> dict:
    """{'high': f, 'low': f} over the LST climate day, or Nones when absent."""
    offset = timedelta(hours=lst_offset_hours(tzname))
    temps = []
    for p in periods or []:
        raw = p.get("temperature")
        if raw is None:
            continue
        try:
            start = datetime.fromisoformat(str(p.get("startTime")))
        except (TypeError, ValueError):
            continue
        utc_offset = start.utcoffset()
        if utc_offset is None:
            continue
        # Shift into fixed standard time, then take the calendar date.
        lst = start - utc_offset + offset
        if lst.date() == day:
            temps.append(float(raw))
    if not temps:
        return {"high": None, "low": None}
    return {"high": max(temps), "low": min(temps)}


def fold_realized(forecast_value, realized_temps: list, variable: str):
    """The day's extreme over BOTH what happened and what is still forecast.

    For a climate day already in progress the remaining hourly periods no longer
    contain an extreme that has already occurred, so a forecast-only reduction is
    badly wrong: on 2026-08-03 Oklahoma City's low of 65 had passed, leaving the
    forecast-only "low" as that evening's 82. The settled extreme is the extreme
    of the realized readings and the remaining forecast together."""
    values = [float(t) for t in (realized_temps or []) if t is not None]
    if forecast_value is not None:
        values.append(float(forecast_value))
    if not values:
        return None
    return min(values) if variable == "low" else max(values)
