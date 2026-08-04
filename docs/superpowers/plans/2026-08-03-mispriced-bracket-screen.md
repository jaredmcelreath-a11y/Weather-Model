# Mispriced-Bracket Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each scan firing, list the Kalshi brackets across ~20 cities that are priced richly but sit far from the NWS forecast, or that realized temperature has already made impossible — for human review, never traded automatically.

**Architecture:** Four new modules (`scan_cities.py`, `screen_forecast.py`, `screen_rules.py`, `screen.py`) plus a `screen_view.py` page. Reuses the existing `scan-data` branch, `scan_log` IO, and `sources/kalshi.py` ladder fetch. Read-only.

**Tech Stack:** Python 3.9-compatible, `pytest`, `zoneinfo`, existing `sources.common.get_json`.

## Global Constraints

- **Read-only.** No module may import `trader`, `trade_logic`, `trade_state`, `kalshi_orders`, or `trade_params`. No order placement. No ntfy or alerting of any kind.
- **Python 3.9 compatible.** Every new module starts with `from __future__ import annotations`.
- **No `StationConfig` entries and no calibration.** City identity is the Kalshi series ticker; the only per-city datum is a coordinate pair.
- **Fixtures are copied from live payloads, never invented.** The verified shapes are given inline in each task. This is not stylistic: the scanner's tests passed against a hand-written market payload whose fields do not exist, and a live pass returned 0 rows from 40 active series.
- **Thresholds:** `MIN_CANDIDATE_PRICE = 0.10`, `MIN_CANDIDATE_GAP_F = 4.0`, `MIN_OBS_SUPPORT = 2`.
- Tests live in `tests/`, run with `python3 -m pytest`. Baseline is **992 passing** at merge `101b02b`.

### Verified live payload shapes (2026-08-03)

NWS `points/{lat},{lon}` → `properties`:
```json
{"gridId": "BOU", "gridX": 74, "gridY": 66,
 "timeZone": "America/Denver",
 "forecastHourly": "https://api.weather.gov/gridpoints/BOU/74,66/forecast/hourly",
 "observationStations": "https://api.weather.gov/gridpoints/BOU/74,66/stations"}
```

NWS hourly forecast → `properties.periods[]` (temperature already in **°F**):
```json
{"startTime": "2026-08-03T17:00:00-06:00", "temperature": 94, "temperatureUnit": "F"}
```

NWS station observations → `features[].properties` (temperature in **°C**):
```json
{"timestamp": "2026-08-03T23:53:00+00:00",
 "temperature": {"unitCode": "wmoUnit:degC", "value": 32.8}}
```

Kalshi market row (from `scan_log.build_snapshot_row`):
```json
{"series": "KXLOWTDEN", "variable": "low", "ticker": "KXLOWTDEN-26AUG03-B72.5",
 "strike_type": "between", "floor": 72, "cap": 73,
 "yes_bid": 0.33, "yes_ask": 0.37, "hours_to_close": 11.0}
```

---

### Task 1: City table and NWS point resolution

**Files:**
- Create: `scan_cities.py`
- Test: `tests/test_scan_cities.py` (create)

**Interfaces:**
- Produces:
  - `CITY_POINTS: dict[str, tuple[float, float]]` — series ticker → (lat, lon)
  - `point_for(series: str) -> tuple | None`
  - `resolve(lat: float, lon: float, fetch=None) -> dict` with keys
    `timezone` (str), `forecast_hourly` (url str), `stations_url` (url str)
  - `station_for(stations_url: str, fetch=None) -> str | None` — first station id

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scan_cities.py`:

```python
import scan_cities

_POINTS = {"properties": {
    "gridId": "BOU", "gridX": 74, "gridY": 66,
    "timeZone": "America/Denver",
    "forecastHourly": "https://api.weather.gov/gridpoints/BOU/74,66/forecast/hourly",
    "observationStations": "https://api.weather.gov/gridpoints/BOU/74,66/stations",
}}

_STATIONS = {"features": [
    {"properties": {"stationIdentifier": "KDEN"}},
    {"properties": {"stationIdentifier": "KCFO"}},
]}


def test_every_mapped_series_is_a_high_or_low_ticker():
    assert scan_cities.CITY_POINTS
    for s in scan_cities.CITY_POINTS:
        assert s.startswith("KXHIGH") or s.startswith("KXLOW")


def test_the_table_covers_both_variables_for_denver():
    assert scan_cities.point_for("KXHIGHDEN") is not None
    assert scan_cities.point_for("KXLOWTDEN") is not None


def test_high_and_low_of_one_city_share_a_coordinate():
    assert scan_cities.point_for("KXHIGHDEN") == scan_cities.point_for("KXLOWTDEN")


def test_an_unmapped_series_is_none():
    assert scan_cities.point_for("KXHIGHNOWHERE") is None


def test_resolve_pulls_timezone_and_urls_from_the_points_payload():
    got = scan_cities.resolve(39.8561, -104.6737, fetch=lambda url: _POINTS)
    assert got["timezone"] == "America/Denver"
    assert got["forecast_hourly"].endswith("/forecast/hourly")
    assert got["stations_url"].endswith("/stations")


def test_station_for_takes_the_nearest_station():
    got = scan_cities.station_for("u", fetch=lambda url: _STATIONS)
    assert got == "KDEN"


def test_station_for_returns_none_when_there_are_no_stations():
    assert scan_cities.station_for("u", fetch=lambda url: {"features": []}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scan_cities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_cities'`

- [ ] **Step 3: Write minimal implementation**

Create `scan_cities.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_cities.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scan_cities.py tests/test_scan_cities.py
git commit -m "feat(screen): city coordinate table and NWS point resolution"
```

---

### Task 2: Climate day and forecast daily extremes

**Files:**
- Create: `screen_forecast.py`
- Test: `tests/test_screen_forecast.py` (create)

**Interfaces:**
- Produces:
  - `climate_day_of_ticker(ticker: str) -> date | None`
  - `lst_offset_hours(tzname: str) -> int`
  - `daily_extremes(periods: list, day: date, tzname: str) -> dict` →
    `{"high": float | None, "low": float | None}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screen_forecast.py`:

```python
from datetime import date

import screen_forecast as sf


def test_climate_day_comes_from_the_ticker():
    # Kalshi embeds the event date; close_time does NOT give it directly --
    # KXHIGHAUS-26AUG04-T97 closes 2026-08-05T05:59Z, which is Aug 5 in local
    # time and only Aug 4 in fixed standard time.
    assert sf.climate_day_of_ticker("KXHIGHAUS-26AUG04-T97") == date(2026, 8, 4)
    assert sf.climate_day_of_ticker("KXLOWTDEN-26AUG03-B72.5") == date(2026, 8, 3)


def test_an_unparseable_ticker_is_none():
    assert sf.climate_day_of_ticker("garbage") is None
    assert sf.climate_day_of_ticker("") is None
    assert sf.climate_day_of_ticker("KXHIGHAUS-26XXX99-T97") is None


def test_lst_offset_ignores_daylight_saving():
    assert sf.lst_offset_hours("America/Chicago") == -6      # CST, not CDT
    assert sf.lst_offset_hours("America/Denver") == -7
    assert sf.lst_offset_hours("America/New_York") == -5
    assert sf.lst_offset_hours("America/Phoenix") == -7      # never shifts


def test_daily_extremes_uses_only_the_targeted_climate_day():
    periods = [
        {"startTime": "2026-08-03T23:00:00-06:00", "temperature": 70},
        {"startTime": "2026-08-04T02:00:00-06:00", "temperature": 61},
        {"startTime": "2026-08-04T15:00:00-06:00", "temperature": 96},
        {"startTime": "2026-08-05T02:00:00-06:00", "temperature": 55},
    ]
    got = sf.daily_extremes(periods, date(2026, 8, 4), "America/Denver")
    assert got["high"] == 96
    assert got["low"] == 61          # 55 belongs to Aug 5, 70 to Aug 3


def test_daily_extremes_is_none_when_the_day_is_absent():
    periods = [{"startTime": "2026-08-03T23:00:00-06:00", "temperature": 70}]
    got = sf.daily_extremes(periods, date(2026, 8, 9), "America/Denver")
    assert got == {"high": None, "low": None}


def test_daily_extremes_skips_periods_with_no_temperature():
    periods = [
        {"startTime": "2026-08-04T15:00:00-06:00", "temperature": None},
        {"startTime": "2026-08-04T16:00:00-06:00", "temperature": 91},
    ]
    got = sf.daily_extremes(periods, date(2026, 8, 4), "America/Denver")
    assert got["high"] == 91 and got["low"] == 91
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen_forecast'`

- [ ] **Step 3: Write minimal implementation**

Create `screen_forecast.py`:

```python
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
        # Shift into fixed standard time, then take the calendar date.
        lst = start.utcoffset() and (start - start.utcoffset() + offset)
        if lst is None:
            continue
        if lst.date() == day:
            temps.append(float(raw))
    if not temps:
        return {"high": None, "low": None}
    return {"high": max(temps), "low": min(temps)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_forecast.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add screen_forecast.py tests/test_screen_forecast.py
git commit -m "feat(screen): climate-day parsing and forecast daily extremes"
```

---

### Task 3: Bracket gap and the forecast-distance rule

**Files:**
- Create: `screen_rules.py`
- Test: `tests/test_screen_rules_forecast.py` (create)

**Interfaces:**
- Produces:
  - `MIN_CANDIDATE_PRICE = 0.10`, `MIN_CANDIDATE_GAP_F = 4.0`
  - `price_of(row: dict) -> float | None` — the YES ask, falling back to the bid
  - `bracket_gap(floor, cap, value: float) -> float | None`
  - `forecast_candidate(row: dict, forecast: float, now_iso: str) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screen_rules_forecast.py`:

```python
import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, ask=0.35, bid=0.33, strike="between"):
    return {"series": "KXLOWTDEN", "variable": "low",
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": strike,
            "floor": floor, "cap": cap, "yes_bid": bid, "yes_ask": ask,
            "hours_to_close": 11.0}


def test_gap_is_zero_when_the_bracket_contains_the_forecast():
    assert sr.bracket_gap(65, 66, 65.4) == 0.0


def test_gap_measures_to_the_nearest_edge():
    assert sr.bracket_gap(72, 73, 66.0) == 6.0
    assert sr.bracket_gap(60, 61, 66.0) == 5.0


def test_gap_handles_open_ended_tails():
    # 'greater than 107' with a forecast of 96 is 11 degrees away.
    assert sr.bracket_gap(107, None, 96.0) == 11.0
    # 'less than 97' with a forecast of 96 contains it.
    assert sr.bracket_gap(None, 97, 96.0) == 0.0


def test_gap_is_none_without_any_strike():
    assert sr.bracket_gap(None, None, 96.0) is None


def test_price_prefers_the_ask_because_that_is_what_you_pay():
    assert sr.price_of(_row(72, 73, ask=0.37, bid=0.33)) == 0.37
    assert sr.price_of(_row(72, 73, ask=None, bid=0.33)) == 0.33
    assert sr.price_of(_row(72, 73, ask=None, bid=None)) is None


def test_a_far_and_richly_priced_bracket_is_a_candidate():
    got = sr.forecast_candidate(_row(72, 73, ask=0.35), 66.0, _TS)
    assert got["kind"] == "forecast"
    assert got["gap"] == 6.0
    assert got["price"] == 0.35
    assert got["forecast"] == 66.0
    assert got["ticker"] == "KXLOWTDEN-26AUG03-B72.5"


def test_a_cheap_bracket_is_not_a_candidate():
    assert sr.forecast_candidate(_row(72, 73, ask=0.05), 66.0, _TS) is None


def test_a_near_bracket_is_not_a_candidate():
    assert sr.forecast_candidate(_row(68, 69, ask=0.35), 66.0, _TS) is None


def test_the_thresholds_are_inclusive_at_the_boundary():
    at_price = sr.forecast_candidate(_row(72, 73, ask=sr.MIN_CANDIDATE_PRICE),
                                     66.0, _TS)
    assert at_price is not None
    at_gap = sr.forecast_candidate(_row(70, 71, ask=0.35), 66.0, _TS)
    assert at_gap is not None and at_gap["gap"] == sr.MIN_CANDIDATE_GAP_F


def test_no_candidate_without_a_forecast():
    assert sr.forecast_candidate(_row(72, 73), None, _TS) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_rules_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen_rules'`

- [ ] **Step 3: Write minimal implementation**

Create `screen_rules.py`:

```python
"""Screening rules: which live brackets are worth a human's two minutes.

Two independent screens, both returning candidate dicts of the same shape:

  forecast_candidate -- SOFT. The market pays real money for an outcome far from
      the NWS forecast. Reported as a DISTANCE in degrees, never as a
      probability: there is no per-city calibrated sigma here, and converting a
      gap into a probability with an invented one manufactures a confident
      number that is a guess (see the 2026-07-17 season-readiness bug, where a
      bin outside the model's range printed 0% and produced a live
      "0% -> BUY NO +85").

  dead_candidate (Task 4) -- HARD. Realized temperature has already made the
      bracket impossible. No calibration, no judgment.
"""
from __future__ import annotations

MIN_CANDIDATE_PRICE = 0.10
MIN_CANDIDATE_GAP_F = 4.0


def price_of(row: dict):
    """What acting on this bracket would cost: the YES ask, or the bid when
    there is no offer. You cannot trade the midpoint."""
    ask, bid = row.get("yes_ask"), row.get("yes_bid")
    return ask if ask is not None else bid


def bracket_gap(floor, cap, value: float):
    """Degrees from `value` to the nearest edge of [floor, cap]; 0 inside it.

    Open-ended tails carry one strike: 'greater' has no cap, 'less' no floor,
    and each is unbounded on its missing side."""
    if value is None or (floor is None and cap is None):
        return None
    if floor is not None and value < floor:
        return round(float(floor) - float(value), 2)
    if cap is not None and value > cap:
        return round(float(value) - float(cap), 2)
    return 0.0


def _candidate(row: dict, kind: str, reference: float, gap: float,
               price: float, now_iso: str) -> dict:
    return {
        "ts": now_iso,
        "series": row.get("series"),
        "variable": row.get("variable"),
        "ticker": row.get("ticker"),
        "floor": row.get("floor"),
        "cap": row.get("cap"),
        "price": price,
        "forecast": reference,
        "gap": gap,
        "kind": kind,
        "hours_to_close": row.get("hours_to_close"),
    }


def forecast_candidate(row: dict, forecast, now_iso: str):
    """A richly-priced bracket far from the forecast, or None."""
    if forecast is None:
        return None
    price = price_of(row)
    if price is None or price < MIN_CANDIDATE_PRICE:
        return None
    gap = bracket_gap(row.get("floor"), row.get("cap"), forecast)
    if gap is None or gap < MIN_CANDIDATE_GAP_F:
        return None
    return _candidate(row, "forecast", float(forecast), gap, price, now_iso)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_forecast.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add screen_rules.py tests/test_screen_rules_forecast.py
git commit -m "feat(screen): forecast-distance screening rule"
```

---

### Task 4: Realized extreme and the dead-bracket rule

**Files:**
- Modify: `screen_rules.py` (append)
- Test: `tests/test_screen_rules_dead.py` (create)

**Interfaces:**
- Consumes: `_candidate`, `price_of` (Task 3)
- Produces:
  - `MIN_OBS_SUPPORT = 2`
  - `c_to_f(celsius) -> float | None`
  - `realized_extreme(temps_f: list, variable: str, min_support: int = MIN_OBS_SUPPORT) -> float | None`
  - `dead_candidate(row: dict, bound: float, now_iso: str) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screen_rules_dead.py`:

```python
import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, variable="low", ask=0.35):
    return {"series": "KXLOWTDEN", "variable": variable,
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": "between",
            "floor": floor, "cap": cap, "yes_bid": 0.33, "yes_ask": ask,
            "hours_to_close": 11.0}


def test_celsius_converts_to_fahrenheit():
    assert sr.c_to_f(0) == 32.0
    assert sr.c_to_f(32.8) == 91.0
    assert sr.c_to_f(None) is None


def test_realized_low_is_the_minimum_with_support():
    # Two readings at or below 66 corroborate 66 as the realized minimum.
    assert sr.realized_extreme([70.0, 66.0, 66.0, 72.0], "low") == 66.0


def test_realized_high_is_the_maximum_with_support():
    assert sr.realized_extreme([70.0, 96.0, 96.0, 72.0], "high") == 96.0


def test_a_lone_outlier_does_not_establish_an_extreme():
    # One spurious 40 must NOT make 40 the realized low -- it would wrongly
    # declare every bracket above it dead.
    assert sr.realized_extreme([70.0, 40.0, 71.0, 72.0], "low") == 70.0


def test_too_few_observations_establish_nothing():
    assert sr.realized_extreme([66.0], "low") is None
    assert sr.realized_extreme([], "low") is None


def test_a_low_bracket_above_the_realized_minimum_is_dead():
    # The low already touched 66, so 72-73 cannot settle YES at any price.
    got = sr.dead_candidate(_row(72, 73, "low"), 66.0, _TS)
    assert got["kind"] == "dead"
    assert got["gap"] == 6.0
    assert got["forecast"] == 66.0


def test_a_low_bracket_below_the_realized_minimum_is_still_live():
    assert sr.dead_candidate(_row(60, 61, "low"), 66.0, _TS) is None


def test_a_high_bracket_below_the_realized_maximum_is_dead():
    got = sr.dead_candidate(_row(90, 91, "high"), 96.0, _TS)
    assert got["kind"] == "dead" and got["gap"] == 5.0


def test_a_high_bracket_above_the_realized_maximum_is_still_live():
    assert sr.dead_candidate(_row(99, 100, "high"), 96.0, _TS) is None


def test_a_bracket_containing_the_bound_is_not_dead():
    assert sr.dead_candidate(_row(66, 67, "low"), 66.0, _TS) is None


def test_a_worthless_dead_bracket_is_not_reported():
    # Nothing to harvest below the price floor.
    assert sr.dead_candidate(_row(72, 73, "low", ask=0.01), 66.0, _TS) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_rules_dead.py -v`
Expected: FAIL — `AttributeError: module 'screen_rules' has no attribute 'c_to_f'`

- [ ] **Step 3: Write minimal implementation**

Append to `screen_rules.py`:

```python
# ---- Realized-extreme (hard) screen ---------------------------------------

MIN_OBS_SUPPORT = 2


def c_to_f(celsius):
    """NWS observations report Celsius; brackets are Fahrenheit."""
    if celsius is None:
        return None
    return round(float(celsius) * 9.0 / 5.0 + 32.0, 1)


def realized_extreme(temps_f: list, variable: str,
                     min_support: int = MIN_OBS_SUPPORT):
    """The day's realized extreme so far, or None when unestablished.

    Physics, not forecasting: the minimum realized so far is a CEILING on the
    settled low, and the maximum a FLOOR on the settled high. Neither can move
    back the other way.

    `min_support` guards against a single spurious reading: the returned extreme
    is the most extreme value that at least `min_support` observations reach.
    One bad 40F print must not declare every bracket above it dead."""
    values = sorted(float(t) for t in temps_f if t is not None)
    if len(values) < min_support:
        return None
    if variable == "low":
        return values[min_support - 1]      # min corroborated by min_support
    if variable == "high":
        return values[-min_support]         # max corroborated by min_support
    return None


def dead_candidate(row: dict, bound, now_iso: str):
    """A bracket the realized extreme has already made impossible, or None.

    For a LOW, `bound` is the realized minimum and any bracket entirely ABOVE it
    is dead. For a HIGH, `bound` is the realized maximum and any bracket
    entirely BELOW it is dead."""
    if bound is None:
        return None
    price = price_of(row)
    if price is None or price < MIN_CANDIDATE_PRICE:
        return None
    variable = row.get("variable")
    floor, cap = row.get("floor"), row.get("cap")
    if variable == "low":
        if floor is None or floor <= bound:
            return None
        gap = round(float(floor) - float(bound), 2)
    elif variable == "high":
        if cap is None or cap >= bound:
            return None
        gap = round(float(bound) - float(cap), 2)
    else:
        return None
    return _candidate(row, "dead", float(bound), gap, price, now_iso)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_dead.py tests/test_screen_rules_forecast.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add screen_rules.py tests/test_screen_rules_dead.py
git commit -m "feat(screen): realized-extreme dead-bracket rule"
```

---

### Task 5: Screen pass, CLI, and workflow wiring

**Files:**
- Modify: `scan_log.py` (add `CANDIDATES_PATH`)
- Create: `screen.py`
- Modify: `.github/workflows/scan.yml` (add the screen step)
- Test: `tests/test_screen_pass.py` (create)

**Interfaces:**
- Consumes: `scan_cities.point_for/resolve/station_for`, `screen_forecast.climate_day_of_ticker/daily_extremes`, `screen_rules.forecast_candidate/dead_candidate/realized_extreme/c_to_f`, `scan_log.build_snapshot_row/append_many`, `sources.kalshi.list_weather_series/list_series_markets`
- Produces:
  - `scan_log.CANDIDATES_PATH = "scan_candidates.jsonl"`
  - `screen.Deps` — callables `list_series`, `list_markets`, `resolve_point`,
    `fetch_forecast`, `fetch_obs`, `append_rows`, `sleep`
  - `screen.screen_pass(now, deps) -> dict` → `{"candidates": int, "cities": int, "errors": int}`
  - `screen.main(argv, deps=None, now=None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_pass.py`:

```python
from datetime import datetime, timezone

import screen

_NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)

_PERIODS = [
    {"startTime": "2026-08-03T02:00:00-06:00", "temperature": 66},
    {"startTime": "2026-08-03T15:00:00-06:00", "temperature": 94},
]


def _market(ticker, floor, cap, ask="0.3500"):
    return {"ticker": ticker, "status": "active", "strike_type": "between",
            "floor_strike": floor, "cap_strike": cap,
            "yes_bid_dollars": "0.3300", "yes_ask_dollars": ask,
            "volume_fp": "50.00", "close_time": "2026-08-04T05:59:00Z"}


def _deps(sink, markets, obs=None):
    return screen.Deps(
        list_series=lambda: [{"ticker": "KXLOWTDEN", "title": "Denver low"}],
        list_markets=lambda s, status=None: markets,
        resolve_point=lambda lat, lon: {"timezone": "America/Denver",
                                        "forecast_hourly": "f",
                                        "stations_url": "s"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: obs or [],
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        sleep=lambda s: None,
        station_for=lambda url: "KDEN",
    )


def test_a_far_richly_priced_bracket_is_flagged_from_the_forecast():
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    out = screen.screen_pass(_NOW, d)
    assert out["candidates"] == 1
    c = sink[0]
    assert c["kind"] == "forecast"
    assert c["gap"] == 6.0            # forecast low 66, bracket floor 72
    assert c["price"] == 0.35


def test_a_bracket_near_the_forecast_is_not_flagged():
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B66.5", 66, 67)])
    assert screen.screen_pass(_NOW, d)["candidates"] == 0
    assert sink == []


def test_realized_observations_flag_a_dead_bracket():
    sink = []
    # Two readings of 18.9C = 66.0F establish the realized low.
    obs = [{"properties": {"timestamp": "2026-08-03T09:00:00+00:00",
                           "temperature": {"value": 18.9}}},
           {"properties": {"timestamp": "2026-08-03T10:00:00+00:00",
                           "temperature": {"value": 18.9}}}]
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)], obs=obs)
    screen.screen_pass(_NOW, d)
    kinds = {c["kind"] for c in sink}
    assert "dead" in kinds


def test_an_unmapped_city_is_skipped():
    sink = []
    d = screen.Deps(
        list_series=lambda: [{"ticker": "KXHIGHNOWHERE", "title": "?"}],
        list_markets=lambda s, status=None: [_market("X-26AUG03-B1.5", 1, 2)],
        resolve_point=lambda lat, lon: {},
        fetch_forecast=lambda url: [],
        fetch_obs=lambda station, start, end: [],
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        sleep=lambda s: None,
        station_for=lambda url: None,
    )
    out = screen.screen_pass(_NOW, d)
    assert out["candidates"] == 0
    assert out["cities"] == 0


def test_one_failing_city_does_not_kill_the_pass():
    sink = []

    def boom(url):
        raise RuntimeError("nws down")

    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    d.fetch_forecast = boom
    out = screen.screen_pass(_NOW, d)
    assert out["errors"] == 1
    assert out["candidates"] == 0


def test_main_returns_nonzero_for_an_unknown_command():
    assert screen.main(["nope"], deps=_deps([], []), now=_NOW) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_screen_pass.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen'`

- [ ] **Step 3: Write minimal implementation**

First add to `scan_log.py`, beside the existing path constants:

```python
CANDIDATES_PATH = "scan_candidates.jsonl"
```

Then create `screen.py`:

```python
"""Multi-city mispriced-bracket screen.

Each firing, list the brackets worth a human's attention: priced richly but far
from the NWS forecast (soft), or already made impossible by realized temperature
(hard). Writes candidates to scan_candidates.jsonl; never trades them.

Read-only. Imports nothing from the trading modules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import scan_cities
import scan_log
import screen_forecast
import screen_rules
from sources import kalshi
from sources.common import get_json

REQUEST_SPACING_S = 0.5     # same Kalshi pacing the scanner needs


@dataclass
class Deps:
    list_series: Callable
    list_markets: Callable
    resolve_point: Callable
    fetch_forecast: Callable
    fetch_obs: Callable
    append_rows: Callable
    station_for: Callable
    sleep: Callable = time.sleep


def _real_fetch_forecast(url):
    return ((get_json(url, ttl=900) or {}).get("properties") or {}).get("periods") or []


def _real_fetch_obs(station, start, end):
    url = f"https://api.weather.gov/stations/{station}/observations"
    params = {"start": start.isoformat().replace("+00:00", "Z"),
              "end": end.isoformat().replace("+00:00", "Z"), "limit": 500}
    return (get_json(url, params, ttl=300) or {}).get("features") or []


def _real_deps() -> Deps:
    return Deps(
        list_series=kalshi.list_weather_series,
        list_markets=kalshi.list_series_markets,
        resolve_point=lambda lat, lon: scan_cities.resolve(lat, lon),
        fetch_forecast=_real_fetch_forecast,
        fetch_obs=_real_fetch_obs,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
        station_for=lambda url: scan_cities.station_for(url),
    )


def _observed_temps_f(features, tzname, day):
    """Fahrenheit readings inside the LST climate day."""
    offset = timedelta(hours=screen_forecast.lst_offset_hours(tzname))
    out = []
    for f in features or []:
        props = f.get("properties") or {}
        temp = screen_rules.c_to_f((props.get("temperature") or {}).get("value"))
        if temp is None:
            continue
        try:
            stamp = datetime.fromisoformat(str(props.get("timestamp")))
        except (TypeError, ValueError):
            continue
        lst = stamp - stamp.utcoffset() + offset
        if lst.date() == day:
            out.append(temp)
    return out


def screen_pass(now: datetime, deps: Deps) -> dict:
    """Screen every mapped city's ladder once."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    candidates, cities, errors = [], 0, 0
    for s in deps.list_series():
        series = s["ticker"]
        point = scan_cities.point_for(series)
        if point is None:
            continue                      # city not mapped; simply not screened
        try:
            markets = deps.list_markets(series, status="open")
            deps.sleep(REQUEST_SPACING_S)
            resolved = deps.resolve_point(*point)
            tzname = resolved.get("timezone")
            periods = deps.fetch_forecast(resolved.get("forecast_hourly"))
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[screen] {series}: skipped ({e})")   # cost the others
            errors += 1
            continue
        if not tzname:
            errors += 1
            continue
        cities += 1

        rows = [r for r in (scan_log.build_snapshot_row(m, series, now)
                            for m in markets) if r is not None]
        # Group by the climate day each bracket settles on, so today's and
        # tomorrow's markets are each screened against their own day.
        by_day: dict = {}
        for r in rows:
            day = screen_forecast.climate_day_of_ticker(r["ticker"])
            if day is not None:
                by_day.setdefault(day, []).append(r)

        variable = scan_log.variable_of_series(series)
        for day, day_rows in by_day.items():
            extremes = screen_forecast.daily_extremes(periods, day, tzname)
            forecast = extremes.get(variable)
            for r in day_rows:
                hit = screen_rules.forecast_candidate(r, forecast, now_iso)
                if hit:
                    candidates.append(hit)

            # Hard screen: only for a climate day already in progress.
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
                - timedelta(hours=screen_forecast.lst_offset_hours(tzname))
            if not (start <= now < start + timedelta(days=1)):
                continue
            try:
                station = deps.station_for(resolved.get("stations_url"))
                features = deps.fetch_obs(station, start, now) if station else []
            except Exception as e:        # noqa: BLE001
                print(f"[screen] {series}: observations skipped ({e})")
                continue
            bound = screen_rules.realized_extreme(
                _observed_temps_f(features, tzname, day), variable)
            for r in day_rows:
                hit = screen_rules.dead_candidate(r, bound, now_iso)
                if hit:
                    candidates.append(hit)

    written = deps.append_rows(scan_log.CANDIDATES_PATH, candidates)
    return {"candidates": written or 0, "cities": cities, "errors": errors}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    deps = deps or _real_deps()
    now = now or datetime.now(timezone.utc)
    if (argv[0] if argv else "") == "run":
        print(f"[screen] {screen_pass(now, deps)}")
        return 0
    print("usage: screen.py run")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_pass.py -v`
Expected: 6 passed

Then the full suite:
Run: `python3 -m pytest`
Expected: 1032 passed (992 baseline + 7 + 6 + 10 + 11 + 6)

- [ ] **Step 5: Add the workflow step**

In `.github/workflows/scan.yml`, insert after the "Snapshot prices" step:

```yaml
      # Screening runs on every firing, right after the snapshot: the ladder
      # fetches are still in the on-disk cache, so this costs NWS calls only.
      - name: Screen for mispriced brackets
        if: github.event_name == 'schedule'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: python screen.py run
```

- [ ] **Step 6: Commit**

```bash
git add scan_log.py screen.py tests/test_screen_pass.py .github/workflows/scan.yml
git commit -m "feat(screen): screening pass, CLI, and workflow wiring"
```

---

### Task 6: Screen page

**Files:**
- Create: `screen_view.py`
- Modify: `app.py` (register the page in the nav)
- Test: `tests/test_screen_view.py` (create)

**Interfaces:**
- Consumes: `scan_log.load`, `scan_log.CANDIDATES_PATH`
- Produces:
  - `latest_firing(rows: list) -> list` — rows sharing the newest `ts`
  - `display_rows(rows: list) -> list` — sorted by `price * gap` descending
  - `render()` — the Streamlit entry point

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_view.py`:

```python
import screen_view


def _c(ts, ticker, price, gap, kind="forecast"):
    return {"ts": ts, "series": "KXLOWTDEN", "variable": "low",
            "ticker": ticker, "floor": 72, "cap": 73, "price": price,
            "forecast": 66.0, "gap": gap, "kind": kind, "hours_to_close": 11.0}


def test_latest_firing_keeps_only_the_newest_timestamp():
    rows = [_c("2026-08-03T12:00:00Z", "a", 0.2, 5.0),
            _c("2026-08-03T18:00:00Z", "b", 0.3, 6.0),
            _c("2026-08-03T18:00:00Z", "c", 0.4, 7.0)]
    got = screen_view.latest_firing(rows)
    assert {r["ticker"] for r in got} == {"b", "c"}


def test_latest_firing_of_nothing_is_empty():
    assert screen_view.latest_firing([]) == []


def test_display_rows_rank_by_price_times_gap():
    rows = [_c("t", "small", 0.15, 4.0),      # 0.60
            _c("t", "big", 0.40, 8.0),        # 3.20
            _c("t", "mid", 0.30, 5.0)]        # 1.50
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["big", "mid", "small"]


def test_display_rows_tolerate_a_missing_gap():
    rows = [_c("t", "ok", 0.40, 8.0),
            dict(_c("t", "bad", 0.30, 5.0), gap=None)]
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["ok", "bad"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_screen_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen_view'`

- [ ] **Step 3: Write minimal implementation**

Create `screen_view.py`:

```python
"""The Screen page: brackets worth two minutes of attention.

Reads scan_candidates.jsonl and shows the newest firing. Display tables are
hand-rolled HTML because canvas-rendered st.dataframe cannot center cells --
the same reason every other table in this app is.
"""
from __future__ import annotations

import html

import streamlit as st

import scan_log


def latest_firing(rows: list) -> list:
    """Only the candidates from the most recent firing."""
    if not rows:
        return []
    newest = max(r.get("ts") or "" for r in rows)
    return [r for r in rows if (r.get("ts") or "") == newest]


def display_rows(rows: list) -> list:
    """Ranked by price x gap: how much the market pays, times how wrong it looks.
    A missing gap sorts last rather than raising."""
    def rank(r):
        price, gap = r.get("price"), r.get("gap")
        if price is None or gap is None:
            return -1.0
        return float(price) * float(gap)
    return sorted(rows, key=rank, reverse=True)


def _bracket_label(row: dict) -> str:
    floor, cap = row.get("floor"), row.get("cap")
    if floor is not None and cap is not None:
        return f"{floor}-{cap}"
    if cap is not None:
        return f"<{cap}"
    if floor is not None:
        return f">{floor}"
    return "?"


def render() -> None:
    st.subheader("Screen — mispriced brackets")
    st.caption(
        "Candidates for review, not signals. The NWS forecast is public, so a "
        "gap usually means the market knows something — 'dead' rows are the "
        "hard ones: realized temperature already ruled them out."
    )
    try:
        rows = latest_firing(scan_log.load(scan_log.CANDIDATES_PATH))
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.info(f"No candidate log yet ({e}).")
        return
    if not rows:
        st.info("No candidates in the latest firing.")
        return

    head = ("<tr><th>City</th><th>Var</th><th>Bracket</th><th>Price</th>"
            "<th>Ref</th><th>Gap</th><th>Kind</th><th>Hrs</th></tr>")
    body = []
    for r in display_rows(rows):
        body.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('series') or ''))}</td>"
            f"<td>{html.escape(str(r.get('variable') or ''))}</td>"
            f"<td>{html.escape(_bracket_label(r))}</td>"
            f"<td>{r.get('price')}</td>"
            f"<td>{r.get('forecast')}</td>"
            f"<td>{r.get('gap')}</td>"
            f"<td>{html.escape(str(r.get('kind') or ''))}</td>"
            f"<td>{r.get('hours_to_close')}</td>"
            "</tr>")
    st.markdown(
        "<table class='screen-table'>" + head + "".join(body) + "</table>",
        unsafe_allow_html=True)
```

Then wire it into `app.py`. Define a page function beside the other `*_page`
functions, importing lazily so a broken screen module cannot stop the whole app
from loading:

```python
def screen_page():
    import screen_view
    screen_view.render()
```

and add it as the LAST entry of the existing `st.navigation([...])` list at
`app.py:450`, so no existing page changes position:

```python
    st.Page(status_page, title="Status"),
    st.Page(screen_page, title="Screen"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_view.py -v`
Expected: 4 passed

Run the full suite:
Run: `python3 -m pytest`
Expected: 1036 passed

- [ ] **Step 5: Verify the page renders**

Use the project's `verify` skill to launch the dashboard and screenshot the
Screen page. Confirm the table renders and the empty-state message appears when
`scan_candidates.jsonl` does not exist yet.

- [ ] **Step 6: Commit**

```bash
git add screen_view.py app.py tests/test_screen_view.py
git commit -m "feat(screen): Screen page listing the latest firing's candidates"
```

---

## Post-implementation manual steps

1. **The scanner's own setup must already be done** — `scan-data` branch and
   `SCAN_GH_TOKEN`. The screen writes to the same branch and will fail without
   it.
2. **Trigger one manual scan run** and confirm `scan_candidates.jsonl` appears.
3. **Eyeball the first candidate list.** Any city whose candidates look
   systematically wrong is a coordinate to re-check in `scan_cities.py`.

## Known limits carried from the spec

- The NWS forecast is public information; most forecast gaps mean the market
  knows something. The `dead` rows are the trustworthy ones.
- A gap is a distance, not a probability. No per-city sigma exists.
- The NWS point forecast is not the CLI daily basis Kalshi settles on (~+0.9°F
  at KDFW, unquantified elsewhere). Immaterial at a 4°F threshold, material if
  the threshold is tightened.
- The dead rule assumes the resolved station is the settlement station. True for
  all 20 coordinates as verified 2026-08-03; recheck if a coordinate changes.
