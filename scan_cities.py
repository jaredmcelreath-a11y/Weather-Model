"""Kalshi weather series -> the coordinate NWS should be asked about.

The ONLY per-city data this system needs for the screen. Everything else --
forecast gridpoint, timezone, nearest observation station -- is derived from
NWS `points/{lat},{lon}` and cached for a day.

Coordinates are the city's NWS CLIMATE STATION, which is the primary airport
everywhere except New York, whose climate station is Central Park (KNYC). Using
a downtown point elsewhere would resolve to the wrong observation station and
silently break the realized-extreme rule in screen_rules.

Every pair below was verified 2026-08-03 by resolving it and confirming the
FIRST returned station is the expected climate station.
"""
from __future__ import annotations

from sources.common import get_json

# (lat, lon) of each city's NWS climate station.
_CITY_COORDS = {
    "ATL": (33.6404, -84.4269),     # KATL
    "AUS": (30.1975, -97.6664),     # KAUS
    "BOS": (42.3656, -71.0096),     # KBOS
    "CHI": (41.9803, -87.9090),     # KORD
    "DAL": (32.8934, -97.0265),     # KDFW
    "DC": (38.8512, -77.0402),      # KDCA
    "DEN": (39.8561, -104.6737),    # KDEN
    "HOU": (29.9902, -95.3368),     # KIAH
    "LAX": (33.9425, -118.4081),    # KLAX
    "LV": (36.0840, -115.1537),     # KLAS
    "MIA": (25.7932, -80.2906),     # KMIA
    "MIN": (44.8848, -93.2223),     # KMSP
    "NOLA": (29.9934, -90.2581),    # KMSY
    "NYC": (40.7789, -73.9692),     # KNYC -- Central Park, NOT an airport
    "OKC": (35.3931, -97.6007),     # KOKC
    "PHIL": (39.8719, -75.2411),    # KPHL
    "PHX": (33.4342, -112.0116),    # KPHX
    "SATX": (29.5337, -98.4698),    # KSAT
    "SEA": (47.4489, -122.3094),    # KSEA
    "SFO": (37.6188, -122.3750),    # KSFO
}

# Kalshi's series naming is inconsistent -- the high dropped the 'T' for some
# cities (KXHIGHAUS) and kept it for others (KXHIGHTDAL), and the low is its own
# mess. These are the 40 PRICED series observed live on 2026-08-03; anything not
# listed simply is not screened.
CITY_POINTS = {series: _CITY_COORDS[city] for series, city in {
    "KXHIGHAUS": "AUS", "KXHIGHCHI": "CHI", "KXHIGHDEN": "DEN",
    "KXHIGHLAX": "LAX", "KXHIGHMIA": "MIA", "KXHIGHNY": "NYC",
    "KXHIGHPHIL": "PHIL", "KXHIGHTATL": "ATL", "KXHIGHTBOS": "BOS",
    "KXHIGHTDAL": "DAL", "KXHIGHTDC": "DC", "KXHIGHTHOU": "HOU",
    "KXHIGHTLV": "LV", "KXHIGHTMIN": "MIN", "KXHIGHTNOLA": "NOLA",
    "KXHIGHTOKC": "OKC", "KXHIGHTPHX": "PHX", "KXHIGHTSATX": "SATX",
    "KXHIGHTSEA": "SEA", "KXHIGHTSFO": "SFO",
    "KXLOWTATL": "ATL", "KXLOWTAUS": "AUS", "KXLOWTBOS": "BOS",
    "KXLOWTCHI": "CHI", "KXLOWTDAL": "DAL", "KXLOWTDC": "DC",
    "KXLOWTDEN": "DEN", "KXLOWTHOU": "HOU", "KXLOWTLAX": "LAX",
    "KXLOWTLV": "LV", "KXLOWTMIA": "MIA", "KXLOWTMIN": "MIN",
    "KXLOWTNOLA": "NOLA", "KXLOWTNYC": "NYC", "KXLOWTOKC": "OKC",
    "KXLOWTPHIL": "PHIL", "KXLOWTPHX": "PHX", "KXLOWTSATX": "SATX",
    "KXLOWTSEA": "SEA", "KXLOWTSFO": "SFO",
}.items()}


def point_for(series: str):
    """(lat, lon) for a Kalshi series, or None when the city is not mapped."""
    return CITY_POINTS.get((series or "").upper())


def resolve(lat: float, lon: float, fetch=None) -> dict:
    """Timezone and downstream URLs for a point. Cached a day upstream: the
    gridpoint mapping essentially never changes."""
    fetch = fetch or (lambda url: get_json(url, ttl=24 * 3600))
    props = (fetch(f"https://api.weather.gov/points/{lat},{lon}") or {}).get(
        "properties") or {}
    return {
        "timezone": props.get("timeZone"),
        "forecast_hourly": props.get("forecastHourly"),
        "stations_url": props.get("observationStations"),
    }


def station_for(stations_url: str, fetch=None):
    """Nearest observation station id, or None. The FIRST entry is the climate
    station when the coordinate is the climate station's own."""
    fetch = fetch or (lambda url: get_json(url, ttl=24 * 3600))
    feats = (fetch(stations_url) or {}).get("features") or []
    if not feats:
        return None
    return (feats[0].get("properties") or {}).get("stationIdentifier")
