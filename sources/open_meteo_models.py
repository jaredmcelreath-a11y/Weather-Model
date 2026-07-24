"""Open-Meteo Forecast API — sharp deterministic model anchors.

Returns one series per model (GFS, ECMWF, ICON, GEM, HRRR). HRRR is high-res
and especially valuable for the same-day picture. Also exposes a historical
fetch used by calibration/backtest to compare past forecasts against obs.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import requests

from config import (DETERMINISTIC_MODELS, LAT, LON, NIGHT_WINDOW_HOURS,
                    TIMEZONE)
from settlement import local_day_bounds
from sources.common import get_open_meteo, parse_local_times

# File the scheduled Action publishes to the data branch: the raw deterministic
# forecast response, fetched from an UN-throttled IP. The live app reads it as a
# fallback when api.open-meteo.com rate-limits the shared Streamlit Cloud IP.
PUBLISHED_FILE = "det_models.json"

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
        pairs = [(t, v) for t, v in zip(times, values) if v is not None]
        out[label] = ([t for t, _ in pairs], [v for _, v in pairs])
    return out


def _fetch_live_raw(forecast_days: int, models) -> dict:
    """The raw Open-Meteo forecast response for the deterministic models."""
    return get_open_meteo(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(models or DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })


def _load_published_raw() -> dict:
    """Read the raw deterministic response the Action published to the data
    branch (GitHub-hosted). Raises if unconfigured or unreachable — the caller
    then behaves exactly as before (drops the source)."""
    repo = os.environ.get("FORECAST_LOG_GH_REPO")
    if not repo:
        raise RuntimeError("no FORECAST_LOG_GH_REPO — published fallback unavailable")
    ref = os.environ.get("FORECAST_LOG_GH_REF", "data")
    headers = {"Accept": "application/vnd.github.raw+json"}
    token = os.environ.get("FORECAST_LOG_GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/contents/{PUBLISHED_FILE}"
    r = requests.get(url, params={"ref": ref}, headers=headers, timeout=10)
    r.raise_for_status()
    return json.loads(r.text)


def fetch(forecast_days: int = 2, models=None) -> dict[str, tuple[list[datetime], list[float]]]:
    """Live deterministic forecasts, {model_label: (times, temps_f)}.

    `models` overrides the production DETERMINISTIC_MODELS (used by the shadow
    consensus); None keeps production behavior. When the live API call fails for
    the PRODUCTION set (e.g. the shared IP is rate-limited), fall back to the
    Action-published copy so the models stay in the consensus instead of dropping;
    a custom `models` set has no published copy, so it just raises."""
    try:
        data = _fetch_live_raw(forecast_days, models)
    except requests.exceptions.RequestException as live_err:
        if models is not None:
            raise
        try:
            data = _load_published_raw()
        except Exception:
            # Published copy unavailable too — re-raise the ORIGINAL network
            # error (a RequestException) so gather_series drops the source as
            # before, rather than escaping as an uncaught error and crashing.
            raise live_err from None
        print("[open_meteo_models] live API failed — using published det_models.json fallback")
    return _parse(data)


def write_published(path: str, forecast_days: int = 3) -> None:
    """Fetch the live production deterministic response and write it to `path`
    (the Action calls this from an un-throttled IP; log.yml ships it to the data
    branch). Raw response so the app's fallback runs the identical _parse path."""
    data = _fetch_live_raw(forecast_days, None)
    with open(path, "w") as fh:
        json.dump(data, fh)


def fetch_historical(start: date, end: date,
                     ttl: int = 24 * 3600, models=None) -> dict[str, tuple[list[datetime], list[float]]]:
    """Archived past *forecasts* over [start, end] for bias calibration.

    The historical-forecast archive stores what each model predicted, letting us
    measure systematic error against what KDFW actually recorded. `models`
    overrides DETERMINISTIC_MODELS (for the shadow backtest); None keeps
    production behavior.
    """
    data = get_open_meteo(HISTORICAL_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(models or DETERMINISTIC_MODELS),
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
    data = get_open_meteo(FORECAST_URL, {
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
    data = get_open_meteo(HISTORICAL_URL, {
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
    data = get_open_meteo(FORECAST_URL, {
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
