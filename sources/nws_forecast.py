"""NWS / NDFD official forecast — the human-tuned anchor.

Resolves the KDFW point to its gridpoint once, then pulls the hourly forecast
and normalizes it to the same (times, temps_f) shape as every other source.
"""

from __future__ import annotations

from datetime import datetime

import config
from sources.common import get_json, parse_local_times


def points_url(station: str = config.DEFAULT_STATION) -> str:
    s = config.station(station)
    return f"https://api.weather.gov/points/{s.lat},{s.lon}"


def _hourly_forecast_url(station: str = config.DEFAULT_STATION) -> str:
    # Gridpoint rarely changes; cache the point lookup for a day.
    points = get_json(points_url(station), ttl=24 * 3600)
    return points["properties"]["forecastHourly"]


def fetch(station: str = config.DEFAULT_STATION) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {'nws_ndfd': (times, temps_f)} from the hourly NWS forecast."""
    data = get_json(_hourly_forecast_url(station))
    periods = data["properties"]["periods"]
    iso_times = [p["startTime"] for p in periods]
    # NWS hourly forecast reports temperature in whole degrees F by default.
    temps = [float(p["temperature"]) if p["temperatureUnit"] == "F"
             else float(p["temperature"]) * 9.0 / 5.0 + 32.0 for p in periods]
    times = parse_local_times(iso_times)
    return {"nws_ndfd": (times, temps)}
