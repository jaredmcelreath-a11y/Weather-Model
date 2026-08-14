"""The city registry behind the Hourly page.

The Hourly page is a pure reference view — no model, no bins, no prices — so it
covers every city Kalshi lists temperature contracts on, not just the two this
system forecasts. `config.STATIONS` deliberately stays at two entries because
`config.STATION_CODES` drives the scheduled loggers, the trader and the "Both"
city toggle; these 20 therefore live in their own light registry carrying only
what a display page needs.

Coordinates for the 18 reference cities come from `scan_cities`, the single
source of truth for them. Dallas and Austin are built from `config.station()`
instead, the same way `config` builds its KDFW entry from module constants, so
their page renders exactly as it did before this registry existed.

Station ids, timezones and CLI locations were verified live on 2026-08-07;
`scripts/verify_hourly_cities.py` re-runs that check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import config
import scan_cities


@dataclass(frozen=True)
class HourlyCity:
    key: str            # scan_cities city code, e.g. "ATL"
    name: str           # "Atlanta"
    station: str        # observing/climate station, e.g. "KATL"
    lat: float
    lon: float
    timezone: str       # IANA zone — every time this page displays
    climate_tz: str     # fixed standard-time zone the NWS climate day runs on
    cli_location: str   # NWS CLI product location, e.g. "ATL"
    modeled: str | None = None   # config station code, for Dallas/Austin only


# The two cities this system models. Their facts come from config, not from the
# table below, so there is one authority per city.
_MODELED = (("DAL", "KDFW"), ("AUS", "KAUS"))

# The reference cities: (key, station, timezone, climate_tz, cli_location).
# `climate_tz` is the station's STANDARD-time offset because an NWS climate day
# runs midnight-to-midnight LST year-round (the rule config.CLIMATE_TZ encodes
# for KDFW). Coordinates are deliberately absent — they come from scan_cities.
#
# Three entries are not guessable from the city name, and the busier airport is
# the WRONG answer for all three. Confirmed 2026-08-13 by scoring each candidate
# station's NWS CLI product against how Kalshi actually settled (see the
# scan_cities docstring): Chicago settles at Midway, not O'Hare (CLIMDW 67/67 vs
# CLIORD 33/67); Houston at Hobby, not Intercontinental (CLIHOU 33/33 vs CLIIAH
# 6/33); and New York at Central Park, not an airport (CLINYC 67/67 vs CLIJFK
# 28/67). Every other city below matched its obvious airport 32/33 or better.
_REFERENCE = (
    ("ATL",  "KATL", "America/New_York",    "Etc/GMT+5", "ATL"),
    ("BOS",  "KBOS", "America/New_York",    "Etc/GMT+5", "BOS"),
    ("CHI",  "KMDW", "America/Chicago",     "Etc/GMT+6", "MDW"),
    ("DC",   "KDCA", "America/New_York",    "Etc/GMT+5", "DCA"),
    ("DEN",  "KDEN", "America/Denver",      "Etc/GMT+7", "DEN"),
    ("HOU",  "KHOU", "America/Chicago",     "Etc/GMT+6", "HOU"),
    ("LAX",  "KLAX", "America/Los_Angeles", "Etc/GMT+8", "LAX"),
    ("LV",   "KLAS", "America/Los_Angeles", "Etc/GMT+8", "LAS"),
    ("MIA",  "KMIA", "America/New_York",    "Etc/GMT+5", "MIA"),
    ("MIN",  "KMSP", "America/Chicago",     "Etc/GMT+6", "MSP"),
    ("NOLA", "KMSY", "America/Chicago",     "Etc/GMT+6", "MSY"),
    ("NYC",  "KNYC", "America/New_York",    "Etc/GMT+5", "NYC"),
    ("OKC",  "KOKC", "America/Chicago",     "Etc/GMT+6", "OKC"),
    ("PHIL", "KPHL", "America/New_York",    "Etc/GMT+5", "PHL"),
    # Phoenix keeps standard time year-round, so its display zone and its
    # climate zone are the same clock.
    ("PHX",  "KPHX", "America/Phoenix",     "Etc/GMT+7", "PHX"),
    ("SATX", "KSAT", "America/Chicago",     "Etc/GMT+6", "SAT"),
    ("SEA",  "KSEA", "America/Los_Angeles", "Etc/GMT+8", "SEA"),
    ("SFO",  "KSFO", "America/Los_Angeles", "Etc/GMT+8", "SFO"),
)


def _from_config(key: str, code: str) -> HourlyCity:
    s = config.station(code)
    return HourlyCity(key=key, name=s.name, station=s.id, lat=s.lat, lon=s.lon,
                      timezone=s.timezone, climate_tz=s.climate_tz,
                      cli_location=s.cli_location, modeled=code)


def _from_table(key, station, tz, climate_tz, cli) -> HourlyCity:
    lat, lon = scan_cities.coords_of(key)
    return HourlyCity(key=key, name=scan_cities.name_of(key), station=station,
                      lat=lat, lon=lon, timezone=tz, climate_tz=climate_tz,
                      cli_location=cli)


# Modeled cities first — they are the ones with a full dashboard behind them —
# then the rest alphabetically.
CITIES: list[HourlyCity] = (
    [_from_config(k, c) for k, c in _MODELED]
    + sorted((_from_table(*row) for row in _REFERENCE), key=lambda c: c.name))

_BY_KEY = {c.key: c for c in CITIES}

DEFAULT_KEY = "DAL"


def keys() -> list[str]:
    """City keys in display order."""
    return [c.key for c in CITIES]


def city(key: str) -> HourlyCity:
    """The city for `key`, falling back to the default rather than raising: the
    key arrives from session state, which can outlive a change to this table."""
    return _BY_KEY.get((key or "").upper(), _BY_KEY[DEFAULT_KEY])


def label(key: str) -> str:
    """Dropdown label — the city and the station it settles on, e.g.
    'Atlanta (KATL)'."""
    c = city(key)
    return f"{c.name} ({c.station})"


def climate_day(c: HourlyCity, now: datetime) -> date:
    """The climate day `now` falls in for this city.

    The same rule as `settlement.climate_day_of`, applied without teaching
    `settlement` about cities this system does not model: converting into fixed
    LST makes the calendar date the climate day.
    """
    return now.astimezone(ZoneInfo(c.climate_tz)).date()
