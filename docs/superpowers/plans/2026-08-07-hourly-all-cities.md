# All-Cities Hourly Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Hourly page show the hourly forecast, current temperature, climate report and radar for any of the 20 cities Kalshi lists temperature contracts on, chosen from a station-labelled dropdown.

**Architecture:** A new light registry (`hourly_cities.py`) carries the per-city facts a display page needs; `config.STATIONS` stays two entries so the loggers, trader and "Both" toggle keep modelling only Dallas and Austin. Three source-layer functions are extracted to take raw coordinates / station ids / CLI locations instead of `config` station codes, and `hourly_view` renders every time in the selected city's own timezone.

**Tech Stack:** Python 3.9, Streamlit, pandas, Altair, pytest. NWS API (`api.weather.gov`), The Weather Company API (`api.weather.com`).

**Spec:** `docs/superpowers/specs/2026-08-07-hourly-all-cities-design.md`

## Global Constraints

- Python 3.9 — no `match`, no `X | Y` at runtime in annotations evaluated eagerly. Every module in this repo starts with `from __future__ import annotations`; keep doing that.
- Run tests with `python3 -m pytest` (there is no bare `python` on this machine).
- The model, logging, alerting and trading paths must not change behaviour. Only the Hourly page, and the three source functions it calls, are in scope.
- **Do not add cities to `config.STATIONS`.** `config.STATION_CODES` is derived from it and is iterated by `city_view.codes_for("Both")`, the scheduled loggers and the trader.
- Existing public signatures stay working: `wunderground.hourly(station=...)`, `nws_cli.fetch_latest_cli(ttl=..., station=...)`, `nws_cli.list_url(station=...)`, `nws_observations.fetch(...)`.
- Comments explain *why*, matching the density of the surrounding code. No decorative comments.
- Commit after each task with a Conventional Commits subject.

---

### Task 1: The city registry

**Files:**
- Create: `hourly_cities.py`
- Modify: `scan_cities.py` (add two public accessors after `city_name`)
- Test: `tests/test_hourly_cities.py`

**Interfaces:**
- Consumes: `config.station(code)` → `StationConfig` with `.name/.id/.lat/.lon/.timezone/.climate_tz/.cli_location`; `scan_cities` private tables `_CITY_COORDS`, `_CITY_NAMES`.
- Produces:
  - `scan_cities.coords_of(key: str) -> tuple[float, float] | None`
  - `scan_cities.name_of(key: str) -> str | None`
  - `hourly_cities.HourlyCity` (frozen dataclass: `key, name, station, lat, lon, timezone, climate_tz, cli_location, modeled`)
  - `hourly_cities.CITIES: list[HourlyCity]`, `hourly_cities.DEFAULT_KEY: str`
  - `hourly_cities.keys() -> list[str]`, `hourly_cities.city(key) -> HourlyCity`, `hourly_cities.label(key) -> str`, `hourly_cities.climate_day(c: HourlyCity, now: datetime) -> date`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hourly_cities.py`:

```python
"""The Hourly page's 20-city registry — membership, ordering, and the
climate-day rule its CLI box is gated on."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import hourly_cities
import scan_cities


def test_covers_every_kalshi_screened_city():
    # The registry and the screen's coordinate table must not drift apart.
    screened = {scan_cities._SERIES_CITY[s] for s in scan_cities.CITY_POINTS}
    assert {c.key for c in hourly_cities.CITIES} == screened
    assert len(hourly_cities.CITIES) == 20


def test_modeled_cities_come_first_then_alphabetical():
    names = [c.name for c in hourly_cities.CITIES]
    assert names[:2] == ["Dallas", "Austin"]
    assert names[2:] == sorted(names[2:])


def test_dallas_and_austin_are_built_from_config():
    # Their rendered page must stay identical to today's, so every displayed
    # value comes from the StationConfig rather than a second hand-typed copy.
    for key, code in (("DAL", "KDFW"), ("AUS", "KAUS")):
        c = hourly_cities.city(key)
        s = config.station(code)
        assert (c.name, c.station, c.lat, c.lon) == (s.name, s.id, s.lat, s.lon)
        assert (c.timezone, c.climate_tz, c.cli_location) == (
            s.timezone, s.climate_tz, s.cli_location)
        assert c.modeled == code


def test_reference_cities_are_not_marked_modeled():
    assert hourly_cities.city("ATL").modeled is None


def test_reference_coordinates_come_from_scan_cities():
    assert (hourly_cities.city("ATL").lat,
            hourly_cities.city("ATL").lon) == scan_cities.coords_of("ATL")


def test_verified_station_ids_and_zones():
    # Verified live 2026-08-07 against api.weather.gov; the surprising ones.
    c = hourly_cities.city("CHI")
    assert (c.station, c.cli_location) == ("KORD", "ORD")
    assert hourly_cities.city("HOU").station == "KIAH"       # not Hobby
    assert hourly_cities.city("NYC").station == "KNYC"       # Central Park
    assert hourly_cities.city("PHX").timezone == "America/Phoenix"
    assert hourly_cities.city("MIA").timezone == "America/New_York"


def test_every_city_has_a_plausible_station_and_zone():
    for c in hourly_cities.CITIES:
        assert c.station.startswith("K") and len(c.station) == 4
        assert ZoneInfo(c.timezone)          # raises if the zone is bogus
        assert c.climate_tz.startswith("Etc/GMT+")
        assert c.cli_location.isupper() and len(c.cli_location) == 3


def test_label_names_the_station():
    assert hourly_cities.label("ATL") == "Atlanta (KATL)"


def test_keys_are_in_display_order_and_default_is_dallas():
    assert hourly_cities.keys()[:2] == ["DAL", "AUS"]
    assert hourly_cities.DEFAULT_KEY == "DAL"
    assert hourly_cities.city(hourly_cities.DEFAULT_KEY).name == "Dallas"


def test_unknown_key_falls_back_to_the_default():
    # A stale session-state key must not crash the page.
    assert hourly_cities.city("NOPE").key == hourly_cities.DEFAULT_KEY


def test_climate_day_uses_fixed_standard_time():
    # 00:30 local on a Pacific summer night is still the previous climate day:
    # PDT is UTC-7, so LST (UTC-8) reads 23:30 of the day before.
    c = hourly_cities.city("LAX")
    now = datetime(2026, 8, 7, 0, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert hourly_cities.climate_day(c, now).isoformat() == "2026-08-06"


def test_climate_day_is_the_clock_date_during_the_day():
    c = hourly_cities.city("LAX")
    now = datetime(2026, 8, 7, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert hourly_cities.climate_day(c, now).isoformat() == "2026-08-07"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_hourly_cities.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'hourly_cities'`.

- [ ] **Step 3: Add the two public accessors to `scan_cities.py`**

Insert directly after the existing `city_name` function:

```python
def coords_of(key: str):
    """(lat, lon) for a CITY key like 'ATL' — the same climate-station point
    `point_for` serves by series. Exposed so the Hourly page's registry can read
    this table rather than keeping a second copy of it."""
    return _CITY_COORDS.get((key or "").upper())


def name_of(key: str):
    """Display name for a CITY key like 'ATL', or None if unmapped."""
    return _CITY_NAMES.get((key or "").upper())
```

- [ ] **Step 4: Create `hourly_cities.py`**

```python
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
# Three entries are not guessable from the city name and were confirmed against
# api.weather.gov: Chicago observes at O'Hare (CLI `ORD`, there is no `CHI`
# product), Houston at Intercontinental (`IAH` — a Hobby product exists and is
# the wrong airport for the Kalshi series), and New York at Central Park (KNYC,
# not an airport), matching the exception scan_cities already documents.
_REFERENCE = (
    ("ATL",  "KATL", "America/New_York",    "Etc/GMT+5", "ATL"),
    ("BOS",  "KBOS", "America/New_York",    "Etc/GMT+5", "BOS"),
    ("CHI",  "KORD", "America/Chicago",     "Etc/GMT+6", "ORD"),
    ("DC",   "KDCA", "America/New_York",    "Etc/GMT+5", "DCA"),
    ("DEN",  "KDEN", "America/Denver",      "Etc/GMT+7", "DEN"),
    ("HOU",  "KIAH", "America/Chicago",     "Etc/GMT+6", "IAH"),
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_hourly_cities.py tests/test_scan_cities.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add hourly_cities.py scan_cities.py tests/test_hourly_cities.py
git commit -m "feat(hourly): add the 20-city registry behind the Hourly page"
```

---

### Task 2: Coordinate + timezone aware TWC hourly fetch

**Files:**
- Modify: `sources/wunderground.py:36-61` (`hourly`)
- Test: `tests/test_wunderground.py`

**Interfaces:**
- Consumes: `hourly_cities.HourlyCity.lat/.lon/.timezone` (Task 1).
- Produces: `wunderground.hourly_at(lat: float, lon: float, tz: str) -> list[dict]` — rows identical in shape to `hourly()`, with `time` stamped in `tz`. `hourly(station=...)` keeps its signature and delegates.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wunderground.py`:

```python
def test_hourly_at_stamps_rows_in_the_requested_zone(monkeypatch):
    # 1784350800 == 2026-07-18T05:00Z == 00:00 CDT == 22:00 PDT the day before.
    monkeypatch.setattr(wunderground, "get_json", lambda url, params, **kw: _HOURLY)
    rows = wunderground.hourly_at(33.9425, -118.4081, "America/Los_Angeles")
    first = rows[0]["time"]
    assert first.utcoffset().total_seconds() == -7 * 3600
    assert (first.hour, first.day) == (22, 17)
    # every field still parses the same way
    assert rows[0]["temp"] == 84 and rows[2]["wind_dir"] == "SW"


def test_hourly_at_requests_the_given_coordinate(monkeypatch):
    seen = {}

    def fake(url, params, **kw):
        seen.update(params)
        return _HOURLY

    monkeypatch.setattr(wunderground, "get_json", fake)
    wunderground.hourly_at(25.7932, -80.2906, "America/New_York")
    assert seen["geocode"] == "25.7932,-80.2906"
    assert seen["units"] == "e"


def test_hourly_still_stamps_the_station_zone(monkeypatch):
    monkeypatch.setattr(wunderground, "get_json", lambda url, params, **kw: _HOURLY)
    rows = wunderground.hourly()
    assert rows[0]["time"].astimezone(_TZ).hour == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_wunderground.py -q`
Expected: FAIL — `AttributeError: module 'sources.wunderground' has no attribute 'hourly_at'`.

- [ ] **Step 3: Rewrite `hourly` as a delegate of `hourly_at`**

Replace the body of `hourly` in `sources/wunderground.py` with:

```python
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
```

The module-level `_geocode` helper becomes unused — delete it, and delete the now-unused `KDFW_GEOCODE` constant only if `grep -rn "KDFW_GEOCODE\|_geocode" .` shows no other reader.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_wunderground.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sources/wunderground.py tests/test_wunderground.py
git commit -m "feat(hourly): fetch the TWC hourly feed by coordinate and timezone"
```

---

### Task 3: Latest observation for any station

**Files:**
- Modify: `sources/nws_observations.py` (add `latest` after `fetch`)
- Test: `tests/test_observations.py`

**Interfaces:**
- Consumes: `sources.common.get_json`, `sources.common.c_to_f`.
- Produces: `nws_observations.latest(station_id: str, ttl: int = 60) -> dict | None` returning `{"temp": float_F, "time": aware_datetime}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observations.py`:

```python
def test_latest_takes_a_raw_station_id(monkeypatch):
    # Reference cities on the Hourly page are not in config.STATIONS, so this
    # must not route through config.station().
    seen = {}

    def fake_get_json(url, params=None, **kw):
        seen["url"] = url
        seen["params"] = params or {}
        return _one_feature("2026-08-07T18:53:00+00:00", 30.0)

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    got = nws_observations.latest("KATL")
    assert seen["url"] == "https://api.weather.gov/stations/KATL/observations"
    assert got["temp"] == 86.0
    assert got["time"].utcoffset().total_seconds() == 0


def test_latest_skips_readings_with_no_temperature(monkeypatch):
    payload = {"features": [
        {"properties": {"timestamp": "2026-08-07T18:53:00+00:00",
                        "temperature": {"value": None}}},
        {"properties": {"timestamp": "2026-08-07T18:48:00+00:00",
                        "temperature": {"value": 30.0}}},
    ]}
    monkeypatch.setattr(nws_observations, "get_json", lambda *a, **k: payload)
    got = nws_observations.latest("KATL")
    # The feed is newest-first, so the first usable reading is the answer.
    assert got["temp"] == 86.0
    assert got["time"].minute == 48


def test_latest_returns_none_on_an_empty_feed(monkeypatch):
    monkeypatch.setattr(nws_observations, "get_json", lambda *a, **k: {"features": []})
    assert nws_observations.latest("KATL") is None


def test_latest_returns_none_when_the_feed_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nws down")

    monkeypatch.setattr(nws_observations, "get_json", boom)
    assert nws_observations.latest("KATL") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_observations.py -q`
Expected: FAIL — `AttributeError: module 'sources.nws_observations' has no attribute 'latest'`.

- [ ] **Step 3: Add `latest` to `sources/nws_observations.py`**

Add after `fetch`:

```python
def latest(station_id: str, ttl: int = 60) -> dict | None:
    """Newest usable reading from any NWS station: {'temp': °F, 'time': aware}.

    Takes a RAW station id rather than a config station code, so it serves the
    Hourly page's reference cities, which `config` has never heard of — and it
    returns the timestamp in whatever zone the feed states, leaving the display
    zone to the caller (a Miami reading must not be stamped Central).

    Display only. Unlike `fetch` it carries no IEM outage fallback (that path
    resolves its station through `config`) and no settlement logic, so a gap in
    the feed shows as a missing reading rather than a stale one.
    """
    try:
        data = get_json(
            f"https://api.weather.gov/stations/{station_id}/observations",
            {"limit": 10}, ttl=ttl)
    except Exception:
        return None
    for feature in (data.get("features") or []):    # newest-first
        props = feature.get("properties") or {}
        temp_c = (props.get("temperature") or {}).get("value")
        if temp_c is None:
            continue
        stamp = (props.get("timestamp") or "").replace("Z", "+00:00")
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        return {"temp": c_to_f(temp_c), "time": when}
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_observations.py tests/test_obs_fallback.py -q`
Expected: all pass (the second file guards that `fetch`'s outage path is untouched).

- [ ] **Step 5: Commit**

```bash
git add sources/nws_observations.py tests/test_observations.py
git commit -m "feat(hourly): read the latest observation for any station id"
```

---

### Task 4: CLI report by product location

**Files:**
- Modify: `sources/nws_cli.py:22-25` (`list_url`), `sources/nws_cli.py:57-73` (`fetch_latest_cli`)
- Test: `tests/test_nws_cli.py`, `tests/test_station_threading.py`

**Interfaces:**
- Consumes: `hourly_cities.HourlyCity.cli_location` (Task 1).
- Produces: `nws_cli.list_url_for(location: str) -> str`, `nws_cli.fetch_latest_for(location: str, ttl: int | None = None) -> dict | None`. `list_url(station=...)` and `fetch_latest_cli(ttl=..., station=...)` keep their signatures and delegate.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nws_cli.py`:

```python
def test_list_url_for_a_bare_location():
    assert nws_cli.list_url_for("ATL") == \
        "https://api.weather.gov/products/types/CLI/locations/ATL"


def test_fetch_latest_for_hits_the_given_location(monkeypatch):
    # Reference cities have no config station, so the fetch must be addressable
    # by CLI location alone.
    seen = []

    def fake_get_json(url, **kw):
        seen.append(url)
        if url.endswith("/ATL"):
            return {"@graph": [{"@id": "https://example.test/product/1"}]}
        return {"productText": FIXTURE,
                "issuanceTime": "2026-07-20T21:41:00+00:00"}

    monkeypatch.setattr(nws_cli, "get_json", fake_get_json)
    got = nws_cli.fetch_latest_for("ATL", ttl=300)
    assert seen[0].endswith("/locations/ATL")
    assert (got["high_f"], got["low_f"]) == (100, 80)


def test_fetch_latest_cli_still_routes_through_config(monkeypatch):
    seen = []

    def fake_get_json(url, **kw):
        seen.append(url)
        if "/locations/" in url:
            return {"@graph": [{"@id": "https://example.test/product/1"}]}
        return {"productText": FIXTURE,
                "issuanceTime": "2026-07-20T21:41:00+00:00"}

    monkeypatch.setattr(nws_cli, "get_json", fake_get_json)
    nws_cli.fetch_latest_cli(station="KAUS")
    assert seen[0].endswith("/locations/AUS")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_nws_cli.py -q`
Expected: FAIL — `AttributeError: module 'sources.nws_cli' has no attribute 'list_url_for'`.

- [ ] **Step 3: Split both functions in `sources/nws_cli.py`**

Replace `list_url` with:

```python
def list_url_for(location: str) -> str:
    """NWS CLI product-list endpoint for a bare product location, e.g. 'ATL'."""
    return "https://api.weather.gov/products/types/CLI/locations/" + location


def list_url(station: str = config.DEFAULT_STATION) -> str:
    """NWS CLI product-list endpoint for `station`'s climate report."""
    return list_url_for(config.station(station).cli_location)
```

Replace `fetch_latest_cli` with:

```python
def fetch_latest_cli(ttl: int | None = None,
                     station: str = config.DEFAULT_STATION) -> dict | None:
    """Fetch and parse the newest CLI product for `station`, or None on failure.

    `ttl` controls the cache freshness of the product list; pass 0 for an
    always-fresh read (the scheduled Action), or a short TTL for the dashboard.
    """
    return fetch_latest_for(config.station(station).cli_location, ttl)


def fetch_latest_for(location: str, ttl: int | None = None) -> dict | None:
    """Fetch and parse the newest CLI product for a bare product location.

    Addressable by location because the Hourly page shows the climate report for
    cities this system does not model; the parser already handles every issuing
    office's time format (verified against all 20 products, 2026-08-07).
    """
    t = CACHE_TTL_SECONDS if ttl is None else ttl
    try:
        listing = get_json(list_url_for(location), ttl=t)
        graph = listing.get("@graph") or []
        if not graph:
            return None
        product = get_json(graph[0]["@id"], ttl=t)
        text = product.get("productText") or ""
        issued = datetime.fromisoformat(product["issuanceTime"])
        return parse_cli(text, issued)
    except Exception:
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_nws_cli.py tests/test_station_threading.py tests/test_cli_alert.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sources/nws_cli.py tests/test_nws_cli.py
git commit -m "feat(hourly): fetch a CLI climate report by product location"
```

---

### Task 5: Render the page in the selected city's timezone

**Files:**
- Modify: `hourly_view.py` (module `TZ`/`KDFW_LAT`/`KDFW_LON` constants, `_current`, `cli_report_box`, `render`)
- Test: `tests/test_hourly_view.py`, `tests/test_cli_report_box.py`

**Interfaces:**
- Consumes: `hourly_cities.city/HourlyCity` (Task 1), `nws_observations.latest` (Task 3).
- Produces: `hourly_view.render(load_hourly, cli_report=None, city=None)` where `city` is an `HourlyCity` (`None` → the default city); `hourly_view._current(city) -> dict | None`; `hourly_view.cli_report_box(cli, tz=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hourly_view.py`:

```python
def test_current_reads_the_citys_station_and_converts_to_its_zone(monkeypatch):
    import hourly_cities
    import hourly_view
    from sources import nws_observations

    seen = {}

    def fake_latest(station_id, **kw):
        seen["station"] = station_id
        return {"temp": 86.0,
                "time": datetime(2026, 8, 7, 18, 53, tzinfo=ZoneInfo("UTC"))}

    monkeypatch.setattr(nws_observations, "latest", fake_latest)
    got = hourly_view._current(hourly_cities.city("LAX"))
    assert seen["station"] == "KLAX"
    # 18:53Z is 11:53 in Los Angeles, not 13:53 Central.
    assert (got["time"].hour, got["time"].minute) == (11, 53)


def test_current_is_none_when_the_station_has_nothing(monkeypatch):
    import hourly_cities
    import hourly_view
    from sources import nws_observations
    monkeypatch.setattr(nws_observations, "latest", lambda *a, **k: None)
    assert hourly_view._current(hourly_cities.city("ATL")) is None


def test_render_accepts_a_reference_city(monkeypatch):
    import hourly_cities
    import hourly_view
    # Miami: no PWS, no rows — render must not raise and must not touch config.
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    hourly_view.render(lambda: ([], None), cli_report=None,
                       city=hourly_cities.city("MIA"))


def test_render_centers_the_radar_on_the_city(monkeypatch):
    import hourly_cities
    import hourly_view
    seen = {}
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    monkeypatch.setattr(hourly_view, "_radar_html",
                        lambda lat, lon, palette=None: seen.update(lat=lat, lon=lon) or "")
    rows = [_row(datetime(2026, 8, 7, 13, tzinfo=ZoneInfo("America/Denver")))]
    hourly_view.render(lambda: (rows, None), city=hourly_cities.city("DEN"))
    assert (round(seen["lat"], 2), round(seen["lon"], 2)) == (39.86, -104.67)
```

Replace the existing `test_render_accepts_station` (it passes the retired
`station=` argument) with:

```python
def test_render_accepts_a_modeled_city(monkeypatch):
    import hourly_cities
    import hourly_view
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    hourly_view.render(lambda: ([], None), cli_report=None,
                       city=hourly_cities.city("AUS"))
```

and update `test_render_degrades_when_loader_raises` to stub
`lambda city: None` instead of `lambda station=None: None`.

Append to `tests/test_cli_report_box.py`:

```python
def test_cli_report_box_renders_the_issued_time_in_the_given_zone():
    import hourly_view
    from datetime import datetime
    from zoneinfo import ZoneInfo
    cli = {"high_f": 91, "low_f": 79,
           "issued": datetime(2026, 8, 7, 21, 41, tzinfo=ZoneInfo("UTC"))}
    value, issued = hourly_view.cli_report_box(cli, tz=ZoneInfo("America/New_York"))
    assert value == "91° / 79°"
    assert issued == "5:41 PM"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_hourly_view.py tests/test_cli_report_box.py -q`
Expected: FAIL — `_current()` takes a keyword `station`, and `cli_report_box()` takes no `tz`.

- [ ] **Step 3: Make `hourly_view` city-aware**

In `hourly_view.py`:

1. Delete the module-level `TZ = ZoneInfo(TIMEZONE)`, the `from config import TIMEZONE` import, and the `KDFW_LAT`/`KDFW_LON` constants. Add `import hourly_cities`. Keep `import config` only if something else still uses it; otherwise drop it.

2. `_radar_html`'s signature loses its KDFW defaults — it becomes
   `def _radar_html(lat: float, lon: float, zoom: int = 7, palette: dict | None = None) -> str:`.
   Update the four `_radar_html()` calls in `tests/test_hourly_view.py` that
   rely on the old defaults to pass `lat=32.90, lon=-97.04` explicitly.

3. Replace `_current`:

```python
def _current(city) -> dict | None:
    """Official current temp for the city = its station's newest reading
    (display only, no settlement logic), converted to the city's own zone."""
    from sources import nws_observations
    got = nws_observations.latest(city.station)
    if not got:
        return None
    return {"temp": got["temp"],
            "time": got["time"].astimezone(ZoneInfo(city.timezone))}
```

4. Replace `cli_report_box`:

```python
def cli_report_box(cli, tz=None):
    """(value, issued_caption) for the NWS climate-report box, or None.

    `cli` is the city's parsed CLI report (nws_cli.fetch_latest_for) or None;
    `tz` is the city's zone, since the parser stamps `issued` in the project
    default and a Pacific city must not read its report time as Central."""
    if not cli:
        return None
    value = f'{cli["high_f"]:g}° / {cli["low_f"]:g}°'
    issued = cli["issued"]
    if tz is not None:
        issued = issued.astimezone(tz)
    return value, issued.strftime("%-I:%M %p")
```

5. In `render`, replace the `station` parameter and the `s = config.station(station)`
   lookup:

```python
def render(load_hourly, cli_report=None, city=None):
    """Draw the Hourly page. `load_hourly` is the cached () -> (rows, pws)
    callable where `rows` is wunderground.hourly_at() and `pws` is
    wunderground.pws_current() (None for every city but Dallas). `cli_report` is
    the city's parsed CLI report (or None). `city` is an hourly_cities.HourlyCity;
    None means the default city."""
    c = city or hourly_cities.city(hourly_cities.DEFAULT_KEY)
    tz = ZoneInfo(c.timezone)
```

Then, in the body, substitute mechanically:
- `s.name` → `c.name`, `s.id` → `c.station`, `s.cli_location` → `c.cli_location`,
  `s.lat`/`s.lon` → `c.lat`/`c.lon`.
- `cur = _current(station)` → `cur = _current(c)`.
- `pws["obs_time"].astimezone(TZ)` → `pws["obs_time"].astimezone(tz)`.
- `today = datetime.now(TZ).date()` → `today = datetime.now(tz).date()`.
- `cli_box = cli_report_box(cli_report)` → `cli_box = cli_report_box(cli_report, tz)`.
- `st_autorefresh(..., key="refresh_hourly")` is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_hourly_view.py tests/test_cli_report_box.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add hourly_view.py tests/test_hourly_view.py tests/test_cli_report_box.py
git commit -m "feat(hourly): render the page in the selected city's timezone"
```

---

### Task 6: The city dropdown

**Files:**
- Modify: `app.py:378-391` (`load_hourly`, `hourly_page`), `app.py:315-329` (`load_cli_report`)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `app.load_hourly_city(key: str)`, `app.load_city_cli(key: str)`, and a `hourly_page()` that owns its own city state.

`app.py` is the Streamlit entry point and has no unit tests in this repo; this task is verified by running the app.

- [ ] **Step 1: Confirm nothing else reads the loaders being replaced**

Run: `grep -rn "load_cli_report\|load_hourly" --include=*.py .`
Expected: matches only inside `app.py`. If anything else reads them, keep those functions and add the new ones alongside.

- [ ] **Step 2: Replace the loaders and the page in `app.py`**

Delete `load_cli_report` and `load_hourly`, and replace `hourly_page` with:

```python
@st.cache_data(ttl=60, show_spinner="Fetching hourly forecast…")
def load_hourly_city(key: str):
    """TWC hourly forecast + nearby PWS current temp for a Hourly-page city.
    60s TTL matches the page autorefresh; the source layer's own TTLs (300s
    hourly, 60s PWS) keep this from refetching every cycle. Only Dallas has a
    configured PWS, so every other city gets None."""
    from sources import wunderground
    c = hourly_cities.city(key)
    pws = wunderground.pws_current(station=c.modeled) if c.modeled else None
    return wunderground.hourly_at(c.lat, c.lon, c.timezone), pws


@st.cache_data(ttl=300, show_spinner=False)
def load_city_cli(key: str):
    """Today's official CLI report for a Hourly-page city, else None.

    Gated to the city's own climate day: probing at midday Central, the newest
    product for the Pacific and Mountain cities is still YESTERDAY's, which
    ungated would label yesterday's high as today's all morning."""
    from datetime import datetime, timezone as _utc
    from sources import nws_cli
    c = hourly_cities.city(key)
    try:
        cli = nws_cli.fetch_latest_for(c.cli_location, ttl=300)
        if cli and cli["report_date"] == hourly_cities.climate_day(
                c, datetime.now(_utc.utc)):
            return cli
    except Exception:
        return None
    return None


def hourly_page():
    # Deliberately NOT city_view: that control is the sticky Dallas/Austin pick
    # shared by every modelled page, and selecting Miami here must not follow the
    # user to Forecast or Journal, which have no data for it.
    key = st.selectbox("City", hourly_cities.keys(), key="hourly_city",
                       format_func=hourly_cities.label,
                       help="Every city Kalshi lists temperature contracts on, "
                            "with the station its market settles on.")
    hourly_view.render(lambda: load_hourly_city(key),
                       cli_report=load_city_cli(key),
                       city=hourly_cities.city(key))
```

Add `import hourly_cities` to the imports at the top of `app.py`, beside `import hourly_view`.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest -q`
Expected: everything passes (the count should be the prior total plus the new tests; no failures).

- [ ] **Step 4: Verify in the running app**

Use the `verify` skill to launch the dashboard locally and screenshot the Hourly page for three cities: **Dallas** (must look exactly as before, including the Euless PWS tile), **Miami** (Eastern hours, single full-width current-temp tile, radar over Florida), and **Seattle** (Pacific hours). Confirm on each that the "Today"/"Tomorrow" section boundaries fall at the city's local midnight and that switching cities on Hourly leaves the Forecast page's city unchanged.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat(hourly): choose any Kalshi weather city from a dropdown"
```

---

### Task 7: Live verification script

**Files:**
- Create: `scripts/verify_hourly_cities.py`

**Interfaces:**
- Consumes: `hourly_cities.CITIES` (Task 1), `sources.nws_cli.parse_cli`.
- Produces: a hand-run script; no importable API.

This exists because unit tests passed against both of the screen's original
defects and only a live pass caught them — the same lesson applies to a
hand-maintained table of station ids.

- [ ] **Step 1: Write the script**

```python
"""Verify the Hourly page's city table against the live NWS API. Run by hand.

For each city: resolve its coordinate through api.weather.gov/points, assert the
first observation station and the timezone match the table, then fetch and parse
its CLI product. Unit tests cannot catch a wrong station id or a CLI location
that stops existing; only this can.

Usage: python3 scripts/verify_hourly_cities.py
"""
import json
import sys
import urllib.request

sys.path.insert(0, ".")

import hourly_cities          # noqa: E402
from sources import nws_cli   # noqa: E402
from datetime import datetime  # noqa: E402

HEADERS = {"User-Agent": "kdfw-weather-model (jaredmcelreath@gmail.com)"}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    bad = 0
    for c in hourly_cities.CITIES:
        problems = []
        try:
            props = get(f"https://api.weather.gov/points/{c.lat},{c.lon}")["properties"]
            station = get(props["observationStations"])["features"][0][
                "properties"]["stationIdentifier"]
            if station != c.station:
                problems.append(f"station {station} != {c.station}")
            if props["timeZone"] != c.timezone:
                problems.append(f"tz {props['timeZone']} != {c.timezone}")
        except Exception as e:
            problems.append(f"points/stations failed: {e}")
        try:
            listing = get(nws_cli.list_url_for(c.cli_location))
            graph = listing.get("@graph") or []
            if not graph:
                problems.append("no CLI products")
            else:
                product = get(graph[0]["@id"])
                parsed = nws_cli.parse_cli(
                    product.get("productText") or "",
                    datetime.fromisoformat(product["issuanceTime"]))
                if not parsed:
                    problems.append("CLI product did not parse")
                else:
                    print(f"  {c.key:5} CLI {c.cli_location}: "
                          f"{parsed['high_f']}/{parsed['low_f']} "
                          f"({parsed['report_date']})")
        except Exception as e:
            problems.append(f"CLI failed: {e}")
        status = "OK" if not problems else "FAIL " + "; ".join(problems)
        print(f"{c.key:5} {c.station:5} {c.timezone:20} {status}")
        bad += bool(problems)
    print(f"\n{len(hourly_cities.CITIES) - bad}/{len(hourly_cities.CITIES)} verified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the live API**

Run: `python3 scripts/verify_hourly_cities.py`
Expected: `20/20 verified`, every row `OK`. A FAIL row means the table is wrong — fix `hourly_cities.py` (and the spec's table) before committing.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_hourly_cities.py
git commit -m "chore(hourly): add a live check of the city table"
```

---

## Done when

- `python3 -m pytest -q` passes with no failures.
- `python3 scripts/verify_hourly_cities.py` reports 20/20.
- The Hourly page's dropdown lists all 20 cities as `Name (KXXX)`, Dallas and Austin first.
- Dallas renders exactly as before, PWS tile included.
- A Pacific or Eastern city renders its hours, its "as of" caption, its climate-report time and its Today/Tomorrow boundaries in its own local time.
- Selecting a reference city on Hourly leaves every other page on its Dallas/Austin pick.
