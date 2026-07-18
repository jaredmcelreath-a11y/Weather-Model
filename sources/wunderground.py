"""Wunderground hourly forecast + PWS current-temp adapter.

Mirrors the data behind wunderground.com/hourly/KDFW. Both endpoints belong to
The Weather Company (TWC) API at api.weather.com and are reached with the WU web
app's shared API key below. This is unofficial: fine for a personal dashboard,
but if TWC ever rotates the web key, refresh WEB_API_KEY here.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
from sources.common import get_json

TZ = ZoneInfo(TIMEZONE)

# The WU web app's shared TWC key. Unofficial; replace if it ever stops working.
WEB_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

KDFW_GEOCODE = "32.90,-97.04"      # KDFW airport (what WU's /hourly/KDFW resolves to)
PWS_STATION_ID = "KTXEULES41"      # Euless backyard PWS — a fast "live" reference

_HOURLY_URL = "https://api.weather.com/v3/wx/forecast/hourly/2day"
_PWS_URL = "https://api.weather.com/v2/pws/observations/current"


def hourly() -> list[dict]:
    """The next ~48h of TWC hourly forecast for KDFW as per-hour dicts.

    TWC returns parallel arrays (one entry per hour); zip them into rows with the
    six fields the Hourly page shows plus a tz-aware local `time`. Empty feed ->
    empty list. Short cache so it tracks WU without hammering the endpoint.
    """
    data = get_json(_HOURLY_URL, {
        "geocode": KDFW_GEOCODE, "format": "json", "units": "e",
        "language": "en-US", "apiKey": WEB_API_KEY,
    }, ttl=300)
    epochs = data.get("validTimeUtc") or []
    rows = []
    for i, epoch in enumerate(epochs):
        rows.append({
            "time": datetime.fromtimestamp(epoch, TZ),
            "temp": _at(data, "temperature", i),
            "feels": _at(data, "temperatureFeelsLike", i),
            "precip_pct": _at(data, "precipChance", i),
            "cloud_pct": _at(data, "cloudCover", i),
            "humidity": _at(data, "relativeHumidity", i),
            "wind_mph": _at(data, "windSpeed", i),
            "wind_dir": _at(data, "windDirectionCardinal", i),
        })
    return rows


def pws_current() -> dict | None:
    """Latest reading from the Euless PWS: {'temp', 'obs_time'} or None if the
    feed has no observation. Very short cache — this is the live number."""
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
