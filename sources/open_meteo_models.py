"""Open-Meteo Forecast API — sharp deterministic model anchors.

Returns one series per model (GFS, ECMWF, ICON, GEM, HRRR). HRRR is high-res
and especially valuable for the same-day picture. Also exposes a historical
fetch used by calibration/backtest to compare past forecasts against obs.
"""

from __future__ import annotations

from datetime import date, datetime

from config import (DETERMINISTIC_MODELS, LAT, LON, NIGHT_WINDOW_HOURS,
                    TIMEZONE)
from settlement import local_day_bounds
from sources.common import get_json, parse_local_times

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Overnight conditions used by the radiational-cooling predictor.
CONDITION_VARS = "cloud_cover,wind_speed_10m"


def _parse(data: dict) -> dict[str, tuple[list[datetime], list[float]]]:
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if key == "time" or not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "det_")
        out[label] = (times, values)
    return out


def fetch(forecast_days: int = 2) -> dict[str, tuple[list[datetime], list[float]]]:
    """Live deterministic forecasts, {model_label: (times, temps_f)}."""
    data = get_json(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _parse(data)


def fetch_historical(start: date, end: date,
                     ttl: int = 24 * 3600) -> dict[str, tuple[list[datetime], list[float]]]:
    """Archived past *forecasts* over [start, end] for bias calibration.

    The historical-forecast archive stores what each model predicted, letting us
    measure systematic error against what KDFW actually recorded.
    """
    data = get_json(HISTORICAL_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }, ttl=ttl)
    return _parse(data)


def _parse_conditions(data: dict):
    hourly = data["hourly"]
    return (parse_local_times(hourly["time"]),
            hourly["cloud_cover"], hourly["wind_speed_10m"])


def _overnight_mean(times, cloud, wind, day: date):
    """(mean_cloud_pct, mean_wind_kmh) over the overnight window for `day`.

    Window is [day_start + NIGHT_WINDOW_HOURS] local — the pre-dawn hours that
    typically produce the daily low. (None, None) if no points in window."""
    start, _ = local_day_bounds(day)
    h0, h1 = NIGHT_WINDOW_HOURS
    cs, ws = [], []
    for t, c, w in zip(times, cloud, wind):
        if c is None or w is None:
            continue
        hours = (t.astimezone(start.tzinfo) - start).total_seconds() / 3600
        if h0 <= hours < h1:
            cs.append(c)
            ws.append(w)
    if not cs:
        return None, None
    return sum(cs) / len(cs), sum(ws) / len(ws)


def night_conditions(day: date, forecast_days: int = 2):
    """Forecast (mean_cloud_pct, mean_wind_kmh) for `day`'s overnight window."""
    data = get_json(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": CONDITION_VARS,
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _overnight_mean(*_parse_conditions(data), day)


def historical_night_conditions(start: date, end: date,
                                ttl: int = 24 * 3600) -> dict[date, tuple]:
    """{day: (mean_cloud_pct, mean_wind_kmh)} over [start, end] for calibration."""
    data = get_json(HISTORICAL_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": CONDITION_VARS,
        "timezone": TIMEZONE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }, ttl=ttl)
    times, cloud, wind = _parse_conditions(data)
    out: dict[date, tuple] = {}
    from datetime import timedelta
    day = start
    while day <= end:
        c, w = _overnight_mean(times, cloud, wind, day)
        if c is not None:
            out[day] = (c, w)
        day += timedelta(days=1)
    return out


# Remaining-hours convective fields for the daily-low humility trigger.
CONVECTIVE_VARS = "precipitation_probability,cape"


def _window_max(times, pop, cape, day: date, now: datetime):
    """(max_pop, max_cape) over the remaining window [now, settlement-day end)
    for `day`.

    These are the hours that could still set a new daily low via a storm
    downdraft. (None, None) for whichever field has no points in window."""
    start, end = local_day_bounds(day)
    ps, cs = [], []
    for t, p, c in zip(times, pop, cape):
        t = t.astimezone(start.tzinfo)
        if now <= t < end:
            if p is not None:
                ps.append(p)
            if c is not None:
                cs.append(c)
    return (max(ps) if ps else None, max(cs) if cs else None)


def convective_window(day: date, now: datetime, forecast_days: int = 2):
    """Forecast (max_pop_pct, max_cape) over [now, settlement-day end) for
    `day` at KDFW."""
    data = get_json(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": CONVECTIVE_VARS,
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    return _window_max(times, hourly["precipitation_probability"],
                       hourly["cape"], day, now)
