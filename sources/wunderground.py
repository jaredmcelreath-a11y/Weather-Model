"""Wunderground hourly forecast + PWS current-temp adapter.

Mirrors the data behind wunderground.com/hourly/KDFW. Both endpoints belong to
The Weather Company (TWC) API at api.weather.com and are reached with the WU web
app's shared API key below. This is unofficial: fine for a personal dashboard,
but if TWC ever rotates the web key, refresh WEB_API_KEY here.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import config
from sources.common import get_json

# The WU web app's shared TWC key. Unofficial; replace if it ever stops working.
WEB_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

PWS_STATION_ID = "KTXEULES41"      # Euless backyard PWS — a fast "live" reference

_HOURLY_URL = "https://api.weather.com/v3/wx/forecast/hourly/2day"
_PWS_URL = "https://api.weather.com/v2/pws/observations/current"


def hourly(station: str = config.DEFAULT_STATION) -> list[dict]:
    """The next ~48h of TWC hourly forecast for a configured station."""
    s = config.station(station)
    return hourly_at(s.lat, s.lon, s.timezone)


def hourly_at(lat: float, lon: float, tz: str) -> list[dict]:
    """The next ~48h of TWC hourly forecast for a coordinate, as per-hour dicts
    stamped in `tz`.

    TWC returns parallel arrays (one entry per hour); zip them into rows with the
    fields the Hourly page shows plus a tz-aware local `time`. Empty feed ->
    empty list. Short cache so it tracks WU without hammering the endpoint.

    The timezone is a parameter, not the module default, because the Hourly page
    spans four US zones: stamping a Miami or Los Angeles feed in Central would
    silently shift every hour of it.
    """
    zone = ZoneInfo(tz)
    data = get_json(_HOURLY_URL, {
        "geocode": f"{lat},{lon}", "format": "json", "units": "e",
        "language": "en-US", "apiKey": WEB_API_KEY,
    }, ttl=300)
    epochs = data.get("validTimeUtc") or []
    rows = []
    for i, epoch in enumerate(epochs):
        rows.append({
            "time": datetime.fromtimestamp(epoch, zone),
            "temp": _at(data, "temperature", i),
            "feels": _at(data, "temperatureFeelsLike", i),
            "dew": _at(data, "temperatureDewPoint", i),
            "precip_pct": _at(data, "precipChance", i),
            "cloud_pct": _at(data, "cloudCover", i),
            "humidity": _at(data, "relativeHumidity", i),
            "wind_mph": _at(data, "windSpeed", i),
            "wind_dir": _at(data, "windDirectionCardinal", i),
        })
    return rows


def pws_current(station: str = config.DEFAULT_STATION) -> dict | None:
    """Latest reading from the station's nearby PWS: {'temp', 'obs_time'} or None
    if the feed has no observation. Very short cache — this is the live number.

    Only KDFW has a configured PWS (Euless) today; other stations return None
    until their own PWS is wired (the Hourly page's Austin display is a later
    plan)."""
    if station != config.DEFAULT_STATION:
        return None
    data = get_json(_PWS_URL, {
        "stationId": PWS_STATION_ID, "format": "json", "units": "e",
        "apiKey": WEB_API_KEY,
    }, ttl=60)
    obs = (data.get("observations") or [])
    if not obs:
        return None
    o = obs[0]
    return {
        "temp": (o.get("imperial") or {}).get("temp"),
        "obs_time": datetime.fromisoformat(o["obsTimeUtc"].replace("Z", "+00:00")),
    }


def _at(data: dict, key: str, i: int):
    """i-th element of a TWC parallel array, or None if absent/short."""
    arr = data.get(key)
    return arr[i] if isinstance(arr, list) and i < len(arr) else None
