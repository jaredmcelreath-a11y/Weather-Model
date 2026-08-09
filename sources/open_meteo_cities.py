"""One batched Open-Meteo request covering every screened city.

Open-Meteo accepts comma-separated coordinates, so all 20 cities and all 5
models arrive in a single call -- measured 2026-08-09 at 0.7s and 62 KB for
20 coords x 5 models x 72 hours. That is the whole reason a per-city consensus
is affordable at the screen's 30-minute cadence.

Separate from open_meteo_models.py, which is bound to config.station and
config.TIMEZONE and is single-station by construction.
"""
from __future__ import annotations

from config import DETERMINISTIC_MODELS
from sources.common import get_open_meteo

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def fetch(coords: list, models: list = None, forecast_days: int = 3,
          ttl: int = 900, get=None) -> list:
    """Raw per-location responses for `coords`, in request order.

    `hourly` + `timeformat=unixtime` rather than Open-Meteo's own daily
    aggregates: those are cut on local time WITH daylight saving, while the
    climate day Kalshi settles on is a fixed-LST window. In summer that is an
    hour of the wrong day -- see screen_forecast.climate_day_of_ticker for the
    same trap on close_time.
    """
    if not coords:
        return []
    fetcher = get or get_open_meteo
    data = fetcher(FORECAST_URL, {
        "latitude": ",".join(str(lat) for lat, _ in coords),
        "longitude": ",".join(str(lon) for _, lon in coords),
        "hourly": "temperature_2m",
        "models": ",".join(models or DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timeformat": "unixtime",
        "forecast_days": forecast_days,
    }, ttl=ttl)
    # A single coordinate comes back as a bare object, many as an array.
    return data if isinstance(data, list) else [data]
