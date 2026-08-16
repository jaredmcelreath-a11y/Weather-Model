# Timeseries Page, Unsettled Table and Strategy Rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Timeseries page showing 36 hours of 5-minute observations for any of the twenty Kalshi cities, add an unsettled-markets table to the Screen page, and reorder the nav with Screen renamed to Strategy.

**Architecture:** The observation feed is `api.weather.gov/stations/{ICAO}/observations`, which is the same 5-minute MADIS data `weather.gov/wrh/timeseries` renders via Synoptic — verified live at 466 readings over 36h for KLAS. A new windowed, ICAO-addressable fetch goes in `sources/nws_observations.py`; a new `timeseries_view.py` holds the pure reductions and the render. The unsettled leader is computed inside `screen.py`, which already lists every series' open markets each firing, and is published in `screen_reference.json` so the page costs one document read rather than forty rate-limited ladder calls.

**Tech Stack:** Python 3.9, Streamlit, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-16-timeseries-page-and-unsettled-table-design.md`

## Global Constraints

- **Python 3.9.** `X | None` annotations require `from __future__ import annotations` at the top of every file that uses them. Every module in this repo already does this — keep it.
- **Run tests with `python3 -m pytest`.** There is no bare `python` on this machine and no pytest in CI.
- **No new dependency, no new secret, no new GitHub Action step, no new log file.**
- **Do not touch `screen_rules.forecast_candidate`, `dead_candidate`, `locked_candidate`, `guarded_candidate`, or `screen_score`.** The outcome-scoring record must stay comparable across this change.
- **Do not rename any module file.** `screen_view.py` and its siblings keep their names; only display strings change.
- **Tables are hand-rolled HTML** through `screen_view._table` / `market_view`'s `.wtbl` theme. Never `st.dataframe` — it is canvas-rendered and cannot centre cells.
- **Every displayed time is in the city's own zone.** This page spans four US timezones; nothing may fall back to the project default.
- **Commit after every task.** Message style is the repo's: `feat(scope): lowercase sentence`.

---

### Task 1: The windowed observation fetch

**Files:**
- Modify: `sources/nws_observations.py` (append after `latest`, at end of file)
- Test: `tests/test_obs_window.py` (create)

**Interfaces:**
- Consumes: `sources.common.get_json`
- Produces: `nws_observations.window_for_id(station_id, start, end, ttl=300, fetch=None) -> list[dict]` — a list of raw NWS `properties` dicts, newest first. Also `nws_observations._WINDOW_LIMIT = 500`.

**Context for the implementer:** the NWS observations endpoint accepts `start`, `end` and `limit`, returns newest-first, and caps at `limit` rows without telling you it truncated. A 36-hour window is ~432 five-minute readings; SPECIs push it past 500 on a busy day (measured 466 at KLAS). So a response that comes back *exactly* full may be truncated, and we page backwards using the oldest row's timestamp as the next `end`. The boundary row repeats between pages, hence the timestamp dedup.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_obs_window.py`:

```python
"""The windowed observation fetch: one page when it fits, paged when it does not."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sources import nws_observations


def _props(stamp: str, temp=20.0) -> dict:
    return {"timestamp": stamp, "temperature": {"value": temp}}


def _page(stamps: list) -> dict:
    return {"features": [{"properties": _props(s)} for s in stamps]}


class _Recorder:
    """A get_json stand-in that hands back canned pages and records the params."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, url, params=None, ttl=None):
        self.calls.append(dict(params or {}))
        return self.pages.pop(0) if self.pages else {"features": []}


START = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
END = datetime(2026, 8, 17, 0, tzinfo=timezone.utc)


def test_a_short_page_costs_exactly_one_request():
    # Under the limit means the feed gave us everything in the window; asking
    # again would be a wasted call against a host that trips a 60s cooldown
    # after three failures.
    fetch = _Recorder([_page(["2026-08-16T23:25:00+00:00",
                              "2026-08-16T23:20:00+00:00"])])
    got = nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 1
    assert [r["timestamp"] for r in got] == ["2026-08-16T23:25:00+00:00",
                                             "2026-08-16T23:20:00+00:00"]


def test_a_full_page_is_paged_and_the_boundary_row_is_not_duplicated():
    # THE TRAP: the endpoint truncates at `limit` silently. A full page means
    # "possibly more", so we re-ask ending at the oldest row we have -- and that
    # row comes back again, which would double-count without the dedup.
    #
    # One-minute spacing, deliberately: 500 rows five minutes apart span 41.7h,
    # which overruns this 36h window, and the "reached the far edge" branch
    # would end paging before the dedup branch was ever reached.
    first = [(END - timedelta(minutes=i)).isoformat()
             for i in range(nws_observations._WINDOW_LIMIT)]
    oldest = first[-1]
    second = [oldest, (END - timedelta(minutes=500)).isoformat()]
    fetch = _Recorder([_page(first), _page(second)])
    got = nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 2
    assert fetch.calls[1]["end"] == oldest
    stamps = [r["timestamp"] for r in got]
    assert len(stamps) == len(set(stamps))            # no duplicate boundary row
    assert len(stamps) == nws_observations._WINDOW_LIMIT + 1


def test_paging_stops_once_the_window_is_covered():
    # A full page whose oldest row is already at or before `start` has reached
    # the far edge of the window -- there is nothing older to ask for.
    stamps = [(START + timedelta(minutes=5 * i)).isoformat()
              for i in range(nws_observations._WINDOW_LIMIT)][::-1]
    fetch = _Recorder([_page(stamps)])
    nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 1


def test_a_dead_feed_returns_no_rows_rather_than_raising():
    # A page must not crash because one station is down; the caller shows an
    # empty table with a notice.
    def boom(url, params=None, ttl=None):
        raise RuntimeError("upstream down")

    assert nws_observations.window_for_id("KLAS", START, END, fetch=boom) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_obs_window.py -q`
Expected: FAIL with `AttributeError: module 'sources.nws_observations' has no attribute 'window_for_id'`

- [ ] **Step 3: Implement `window_for_id`**

Append to the end of `sources/nws_observations.py`:

```python
# The endpoint's row cap. A 36-hour window is ~432 five-minute readings and
# SPECIs push it higher -- 466 measured at KLAS on 2026-08-16 -- so a response
# that comes back exactly full may have been truncated.
_WINDOW_LIMIT = 500
# Four pages covers ~2000 readings, far past any real window this serves. A
# bound rather than a while-loop because the paging cursor comes from data: a
# feed repeating one timestamp would otherwise spin forever.
_MAX_WINDOW_PAGES = 4


def _stamp(text):
    """An NWS timestamp as an aware datetime, or None when unparseable."""
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def window_for_id(station_id: str, start: datetime, end: datetime,
                  ttl: int = 300, fetch=None) -> list:
    """Every reading `station_id` published in [start, end], newest first.

    Raw NWS `properties` dicts rather than a reduction: the Timeseries page
    shows columns this module has no opinion about (dew point, wind, the raw
    METAR), and normalising them here would put display concerns in a source.

    Takes a RAW station id, like `latest` and for the same reason -- `config`
    knows only KDFW and KAUS, while this serves twenty reference cities.

    Display only, and best-effort: any failure returns the rows gathered so far
    rather than raising, because one dead station must not take down a page.
    """
    get = fetch or get_json
    url = f"https://api.weather.gov/stations/{station_id}/observations"
    out, seen, cursor = [], set(), end
    for _ in range(_MAX_WINDOW_PAGES):
        params = {"start": start.isoformat().replace("+00:00", "Z"),
                  "end": cursor.isoformat().replace("+00:00", "Z"),
                  "limit": _WINDOW_LIMIT}
        try:
            features = (get(url, params, ttl=ttl) or {}).get("features") or []
        except Exception:             # noqa: BLE001 - a page must not crash
            break
        fresh = []
        for feature in features:
            props = (feature or {}).get("properties") or {}
            key = props.get("timestamp")
            if not key or key in seen:
                continue              # the boundary row repeats between pages
            seen.add(key)
            fresh.append(props)
        out.extend(fresh)
        if len(features) < _WINDOW_LIMIT:
            break                     # the feed gave us the whole window
        oldest = _stamp(fresh[-1]["timestamp"]) if fresh else None
        if oldest is None or oldest <= start:
            break                     # reached the far edge, or made no progress
        cursor = oldest
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_obs_window.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS — no existing test touches `nws_observations` module scope.

- [ ] **Step 6: Commit**

```bash
git add sources/nws_observations.py tests/test_obs_window.py
git commit -m "feat(obs): fetch a station's readings over an arbitrary window"
```

---

### Task 2: The Timeseries reductions

**Files:**
- Create: `timeseries_view.py`
- Test: `tests/test_timeseries_rows.py` (create)

**Interfaces:**
- Consumes: `nws_observations.window_for_id` (Task 1), `hourly_cities.HourlyCity`
- Produces:
  - `timeseries_view.is_hourly(props: dict) -> bool`
  - `timeseries_view.compass(degrees) -> str`
  - `timeseries_view.reading(props: dict, tz) -> dict | None` returning `{"time", "temp_f", "dewpoint_f", "wind_mph", "wind_dir", "raw", "hourly"}`
  - `timeseries_view.readings(rows: list, tz) -> list`
  - `timeseries_view.day_extremes(readings: list, day, climate_tz: str) -> dict` returning `{"high": (temp, time) | None, "low": (temp, time) | None}`
  - `timeseries_view.table_rows(readings: list) -> list`
  - `timeseries_view._COLUMNS: list`
  - `timeseries_view._table(columns: list, rows: list) -> str`

**On the table renderer:** `timeseries_view` gets its own twelve-line `_table` rather than importing `screen_view._table`. Two reasons. It is a private name on another *display* page, so importing it points one page at another's internals and pulls the whole Screen import graph — `bet_history`, `city_consensus`, `sources.kalshi`, altair, pandas — into a page that needs none of it. And `screen_view._header_cell` defaults its tooltip map to that module's `_TIPS`, the fade table's, so the shared version would need a signature change and a sweep of its existing call sites — unrelated refactoring this feature does not justify. The Timeseries table carries no column tooltips at all (its six columns are self-describing and the precision caveat is already a caption), so it needs neither `_header_cell` nor `_TIP_CSS`. Both renderers emit the same `.wtbl` markup `market_view` themes.

**Context for the implementer:** two kinds of row arrive in one feed and they are NOT equally precise. Rows with a non-empty `rawMessage` are the routine ~:53 METAR and carry tenths of a degree (`temperature.value` of `37.8`). Rows with an empty `rawMessage` are the 5-minute MADIS readings and are whole °C (`39`, `38`) — which display at the **bottom** of their bucket, so they read up to 1.8 °F low. That is the 100-vs-101 wall: a whole-°C feed cannot represent 101 °F at all. The Feed column exists to keep a reader from treating the two as the same measurement.

NWS units, confirmed live: `temperature` and `dewpoint` in `wmoUnit:degC`; `windSpeed` in `wmoUnit:km_h-1` (NOT mph); `windDirection` in degrees.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeseries_rows.py`:

```python
"""Timeseries reductions: which feed a row came from, and the climate-day extremes."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import timeseries_view

VEGAS = ZoneInfo("America/Los_Angeles")


def _props(stamp, temp_c=None, dew_c=None, wind_kmh=None, wind_deg=None, raw=""):
    return {"timestamp": stamp,
            "temperature": {"value": temp_c},
            "dewpoint": {"value": dew_c},
            "windSpeed": {"value": wind_kmh},
            "windDirection": {"value": wind_deg},
            "rawMessage": raw}


def test_a_raw_metar_marks_the_row_hourly():
    # The :53 METAR carries the T group in TENTHS. Verified live at KLAS:
    # T03780111 alongside temperature 37.8.
    assert timeseries_view.is_hourly(
        _props("2026-08-16T21:56:00+00:00", 37.8,
               raw="KLAS 162156Z 22008KT 38/11 A3002 RMK AO2 T03780111"))


def test_an_empty_raw_message_marks_the_row_five_minute():
    # The 5-minute MADIS rows carry no raw text and are WHOLE degC -- 39, 38 --
    # which is why they must not be read as equally precise.
    assert not timeseries_view.is_hourly(_props("2026-08-16T23:25:00+00:00", 39))
    assert not timeseries_view.is_hourly({"timestamp": "x"})


def test_a_reading_converts_units_and_localises():
    # degC -> degF, km/h -> mph, degrees -> compass, UTC -> the city's zone.
    got = timeseries_view.reading(
        _props("2026-08-16T23:25:00+00:00", 39.0, 12.0, 16.1, 220), VEGAS)
    assert got["temp_f"] == 102.2
    assert got["dewpoint_f"] == 53.6
    assert got["wind_mph"] == 10.0
    assert got["wind_dir"] == "SW"
    assert got["time"].hour == 16                 # 23:25Z is 16:25 PDT
    assert got["hourly"] is False


def test_a_reading_without_a_temperature_is_dropped():
    # A row with no temperature has nothing this page exists to show.
    assert timeseries_view.reading(_props("2026-08-16T23:25:00+00:00"), VEGAS) is None


def test_compass_wraps_at_north():
    assert timeseries_view.compass(0) == "N"
    assert timeseries_view.compass(354) == "N"
    assert timeseries_view.compass(90) == "E"
    assert timeseries_view.compass(None) == ""


def _at(stamp_utc, temp_f):
    return {"time": datetime.fromisoformat(stamp_utc).astimezone(VEGAS),
            "temp_f": temp_f, "dewpoint_f": None, "wind_mph": None,
            "wind_dir": "", "raw": "", "hourly": False}


def test_extremes_cover_the_fixed_lst_day_not_the_local_day():
    # THE TRAP, the same one city_consensus documents. Las Vegas LST is UTC-8
    # all year, so the Aug 16 climate day runs 08:00Z Aug 16 to 08:00Z Aug 17.
    # The 08:30Z Aug 17 reading is Aug 17's, though it is 01:30 LOCAL on Aug 17
    # and would land on Aug 16 under any DST-aware local rule.
    rows = [_at("2026-08-17T08:30:00+00:00", 120.0),   # next climate day
            _at("2026-08-16T23:25:00+00:00", 102.2),
            _at("2026-08-16T13:00:00+00:00", 80.0)]
    got = timeseries_view.day_extremes(rows, date(2026, 8, 16), "Etc/GMT+8")
    assert got["high"][0] == 102.2                     # NOT 120.0
    assert got["low"][0] == 80.0


def test_extremes_report_when_each_happened():
    rows = [_at("2026-08-16T23:25:00+00:00", 102.2),
            _at("2026-08-16T13:00:00+00:00", 80.0)]
    got = timeseries_view.day_extremes(rows, date(2026, 8, 16), "Etc/GMT+8")
    assert got["high"][1].hour == 16                   # 23:25Z = 16:25 PDT
    assert got["low"][1].hour == 6


def test_extremes_are_none_when_the_day_has_no_readings():
    got = timeseries_view.day_extremes([], date(2026, 8, 16), "Etc/GMT+8")
    assert got == {"high": None, "low": None}


def test_table_rows_name_the_feed_each_row_came_from():
    rows = [{"time": datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS),
             "temp_f": 102.2, "dewpoint_f": 53.6, "wind_mph": 10.0,
             "wind_dir": "SW", "raw": "KLAS 162156Z", "hourly": True},
            {"time": datetime(2026, 8, 16, 16, 20, tzinfo=VEGAS),
             "temp_f": 100.4, "dewpoint_f": None, "wind_mph": None,
             "wind_dir": "", "raw": "", "hourly": False}]
    got = timeseries_view.table_rows(rows)
    assert got[0]["Feed"] == "METAR"
    assert got[0]["Temp"] == "102.2°"
    assert got[0]["Wind"] == "SW 10"
    assert got[1]["Feed"] == "5-min"
    assert got[1]["Dew pt"] == "—"
    assert got[1]["Wind"] == "—"
    assert set(timeseries_view._COLUMNS) >= {"Time", "Temp", "Feed"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_timeseries_rows.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'timeseries_view'`

- [ ] **Step 3: Implement the reductions**

Create `timeseries_view.py` with everything below (the `render` function comes in Task 3):

```python
"""The Timeseries page — 36 hours of a city's own observations.

Mirrors what weather.gov/wrh/timeseries shows, on our theme and in one place
for all twenty cities. That NWS page is a front end over the Synoptic API using
a token embedded in NWS's own site; we read the same 5-minute MADIS data
straight from api.weather.gov, which needs no key.

Two kinds of row arrive in that one feed and they are NOT equally precise. The
routine ~:53 METAR carries tenths of a degree and its raw text; the 5-minute
readings are whole degC and carry neither. A whole-degC value displays at the
BOTTOM of its bucket -- 38C renders 100.4F when the true reading is anywhere
below 102.2F -- so it can read up to 1.8F low, and it cannot represent 101F at
all. That is the settlement wall this page exists to watch, so the Feed column
names which feed every row came from rather than letting them blur together.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_autorefresh import st_autorefresh

import hourly_cities
import market_view

_EM = "—"

# 16-point compass, in the order the bearing divides into.
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_MPH_PER_KMH = 0.621371


def is_hourly(props: dict) -> bool:
    """Whether this row is the routine METAR rather than a 5-minute reading.

    The raw text is the discriminator, not the minute of the timestamp: a SPECI
    also carries raw text and is also a full-precision report, while the :55
    five-minute row is not."""
    return bool(((props or {}).get("rawMessage") or "").strip())


def compass(degrees) -> str:
    """A bearing as a 16-point compass name, or '' when there is no wind."""
    if degrees is None:
        return ""
    return _COMPASS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]


def _c_to_f(celsius):
    return None if celsius is None else round(float(celsius) * 9.0 / 5.0 + 32.0, 1)


def _value(props: dict, key: str):
    return ((props or {}).get(key) or {}).get("value")


def reading(props: dict, tz) -> dict:
    """One display row in the city's own zone, or None with no temperature.

    A row without a temperature has nothing this page exists to show, so it is
    dropped rather than rendered as a line of em dashes."""
    temp = _c_to_f(_value(props, "temperature"))
    if temp is None:
        return None
    try:
        when = datetime.fromisoformat(str((props or {}).get("timestamp")))
    except (TypeError, ValueError):
        return None
    speed = _value(props, "windSpeed")
    return {
        "time": when.astimezone(tz),
        "temp_f": temp,
        "dewpoint_f": _c_to_f(_value(props, "dewpoint")),
        # NWS publishes wind in km/h, not mph. Reading it raw understates every
        # gust by ~38% and looks entirely plausible.
        "wind_mph": None if speed is None else round(float(speed) * _MPH_PER_KMH, 1),
        "wind_dir": compass(_value(props, "windDirection")),
        "raw": ((props or {}).get("rawMessage") or "").strip(),
        "hourly": is_hourly(props),
    }


def readings(rows: list, tz) -> list:
    """Every usable row from a window_for_id payload, newest first."""
    out = [reading(p, tz) for p in rows or []]
    return [r for r in out if r is not None]


def day_extremes(rows: list, day: date, climate_tz: str) -> dict:
    """{'high': (temp, time), 'low': (temp, time)} over one LST climate day.

    The window is fixed standard time, never the city's local time: a climate
    day ends at 01:00 local during daylight saving, so the local date is a day
    ahead for that hour and an hour of the wrong day lands in the extreme. The
    same trap city_consensus.series_extreme documents.

    Computed from the values as PUBLISHED, so with a whole-degC row in the mix
    the high is a floor on the true high and the low a ceiling on the true low.
    The caller says so on the page.
    """
    zone = ZoneInfo(climate_tz)
    inside = [r for r in rows or []
              if r.get("temp_f") is not None
              and r["time"].astimezone(zone).date() == day]
    if not inside:
        return {"high": None, "low": None}
    hi = max(inside, key=lambda r: r["temp_f"])
    lo = min(inside, key=lambda r: r["temp_f"])
    return {"high": (hi["temp_f"], hi["time"]), "low": (lo["temp_f"], lo["time"])}


_COLUMNS = ["Time", "Temp", "Dew pt", "Wind", "Feed", "Raw METAR"]


def _table(columns: list, rows: list) -> str:
    """A themed .wtbl table from display-string row dicts.

    Deliberately its own renderer rather than screen_view's. That one is a
    private name on another display page -- importing it would point this page
    at another's internals and drag the whole Screen import graph in -- and its
    header cell binds its tooltip default to the fade table's tip map. This
    table carries no column tooltips: six self-describing columns, with the one
    caveat worth stating (precision) already a caption under the extremes.
    Same .wtbl markup, themed by market_view exactly as every other table is."""
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = []
    for r in rows:
        body.append("<tr>")
        body.append("".join(f"<td>{html.escape(str(r.get(c, '')))}</td>"
                            for c in columns))
        body.append("</tr>")
    return ('<div class="wtbl-wrap"><table class="wtbl"><thead><tr>'
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>")


def _temp(value) -> str:
    return _EM if value is None else f"{float(value):.1f}°"


def _wind(mph, direction) -> str:
    if mph is None:
        return _EM
    return f"{direction} {mph:.0f}".strip()


def table_rows(rows: list) -> list:
    """Display-string dicts for `_table`, in the order given (newest first)."""
    return [{"Time": r["time"].strftime("%-I:%M %p"),
             "Temp": _temp(r.get("temp_f")),
             "Dew pt": _temp(r.get("dewpoint_f")),
             "Wind": _wind(r.get("wind_mph"), r.get("wind_dir") or ""),
             "Feed": "METAR" if r.get("hourly") else "5-min",
             "Raw METAR": r.get("raw") or _EM}
            for r in rows or []]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_timeseries_rows.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add timeseries_view.py tests/test_timeseries_rows.py
git commit -m "feat(timeseries): reduce a station's window to display rows"
```

---

### Task 3: The Timeseries page

**Files:**
- Modify: `timeseries_view.py` (append `render`)
- Modify: `app.py` — add `load_timeseries` and `timeseries_page` near `hourly_page` (currently ends line 404), and add one `st.Page` entry to the `st.navigation` list
- Test: `tests/test_timeseries_caption.py` (create)

**Interfaces:**
- Consumes: `timeseries_view.readings`, `day_extremes`, `table_rows`, `_table`, `_COLUMNS` (Task 2); `nws_observations.window_for_id` (Task 1)
- Produces: `timeseries_view.render(load_window, city, now=None) -> None`, `timeseries_view.extreme_caption(extremes) -> str`, `app.load_timeseries(key) -> list`, `app.timeseries_page() -> None`

**Context for the implementer:** follow `hourly_page` in `app.py` exactly — including its comment about why the city dropdown is deliberately NOT `city_view.city_control` (that control is the sticky Dallas/Austin pick shared by the modelled pages, and choosing Las Vegas here must not follow the user to Forecast, which has no data for it). The page window is 36 hours ending now; `hourly_cities.climate_day(c, now)` gives the climate day for the extremes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_timeseries_caption.py`:

```python
"""The extremes caption: the settling number, and the honesty about precision."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import timeseries_view

VEGAS = ZoneInfo("America/Los_Angeles")


def test_the_caption_reports_both_extremes_with_their_times():
    got = timeseries_view.extreme_caption(
        {"high": (102.2, datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS)),
         "low": (80.0, datetime(2026, 8, 16, 6, 0, tzinfo=VEGAS))})
    assert "102.2" in got and "4:25 PM" in got
    assert "80.0" in got and "6:00 AM" in got


def test_a_missing_extreme_reads_as_nothing_yet_not_as_a_number():
    got = timeseries_view.extreme_caption({"high": None, "low": None})
    assert "—" in got


def test_one_extreme_present_does_not_invent_the_other():
    got = timeseries_view.extreme_caption(
        {"high": (102.2, datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS)), "low": None})
    assert "102.2" in got
    assert "None" not in got
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_timeseries_caption.py -q`
Expected: FAIL with `AttributeError: module 'timeseries_view' has no attribute 'extreme_caption'`

- [ ] **Step 3: Implement `extreme_caption` and `render`**

Append to `timeseries_view.py`:

```python
def _extreme(pair) -> str:
    """'102.2° at 4:25 PM', or an em dash when the day has none yet."""
    if not pair:
        return _EM
    value, when = pair
    return f"{float(value):.1f}° at {when.strftime('%-I:%M %p')}"


def extreme_caption(extremes: dict) -> str:
    """The climate day's running extremes, as one line."""
    return (f"High so far {_extreme((extremes or {}).get('high'))}  ·  "
            f"Low so far {_extreme((extremes or {}).get('low'))}")


def render(load_window, city=None, now=None) -> None:
    """Draw the Timeseries page.

    `load_window` is a cached () -> list of NWS observation `properties` dicts,
    newest first (nws_observations.window_for_id). `city` is an
    hourly_cities.HourlyCity; None means the default city.
    """
    from datetime import timezone as _utc

    c = city or hourly_cities.city(hourly_cities.DEFAULT_KEY)
    tz = ZoneInfo(c.timezone)
    market_view._theme_controls()      # theme CSS + .wtbl/.wtbl-wrap + Settings
    # Five minutes, matching the feed: a new MADIS reading exists only that
    # often, so a faster refresh would re-render the same table.
    st_autorefresh(interval=300_000, key="refresh_timeseries")
    st.title(f"{c.name} Timeseries")
    st.caption(f"The last 36 hours of {c.station}'s own observations, five "
               "minutes apart — the same feed weather.gov/wrh/timeseries draws.")

    try:
        rows = readings(load_window(), tz)
    except Exception as e:             # noqa: BLE001 - one dead station must
        st.info(f"No observations for {c.station} right now ({e}).")
        return
    if not rows:
        st.info(f"No observations for {c.station} in the last 36 hours.")
        return

    day = hourly_cities.climate_day(c, now or datetime.now(_utc.utc))
    st.markdown(f"#### Climate day {day:%b %-d}")
    st.caption(extreme_caption(day_extremes(rows, day, c.climate_tz)))
    st.caption("Read as published: with a whole-°C row in the mix the high is a "
               "FLOOR on the true high and the low a CEILING on the true low.")
    st.markdown(_table(_COLUMNS, table_rows(rows)), unsafe_allow_html=True)
```

- [ ] **Step 4: Wire the page into `app.py`**

Insert immediately after `hourly_page()` (which ends around line 404, just before `def edge_page():`):

```python
@st.cache_data(ttl=300, show_spinner="Fetching observations…")
def load_timeseries(key: str):
    """36 hours of 5-minute observations for a Timeseries-page city.

    300s TTL matches both the page's autorefresh and the feed's own cadence:
    NWS publishes a new 5-minute reading only that often, so a shorter window
    would re-fetch the same rows."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _utc
    from sources import nws_observations
    c = hourly_cities.city(key)
    now = _dt.now(_utc.utc)
    return nws_observations.window_for_id(c.station, now - _td(hours=36), now,
                                          ttl=300)


def timeseries_page():
    # Deliberately NOT city_view, for the same reason hourly_page is not: that
    # control is the sticky Dallas/Austin pick shared by every modelled page,
    # and selecting Las Vegas here must not follow the user to Forecast.
    import timeseries_view
    key = st.selectbox("City", hourly_cities.keys(), key="timeseries_city",
                       format_func=hourly_cities.label,
                       help="Every city Kalshi lists temperature contracts on, "
                            "with the station its market settles on.")
    timeseries_view.render(lambda: load_timeseries(key),
                           city=hourly_cities.city(key))
```

Then add the page to the `st.navigation` list, immediately after the Hourly entry:

```python
    st.Page(hourly_page, title="Hourly"),
    st.Page(timeseries_page, title="Timeseries"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_timeseries_caption.py -q`
Expected: PASS, 3 tests

Run: `python3 -m pytest -q`
Expected: PASS, whole suite

- [ ] **Step 6: Verify the page renders against real data**

Use the `verify` skill to run the dashboard locally and screenshot the Timeseries page. Confirm: the dropdown lists twenty cities; the table fills; times are in the selected city's zone (check a Pacific city against an Eastern one); the Feed column shows both `METAR` and `5-min` values; the climate-day extremes line is populated.

- [ ] **Step 7: Commit**

```bash
git add timeseries_view.py app.py tests/test_timeseries_caption.py
git commit -m "feat(timeseries): add the 36-hour observation page for all 20 cities"
```

---

### Task 4: Publish each ladder's leading bracket

**Files:**
- Modify: `screen_rules.py` (append a new section after `guarded_candidate`)
- Modify: `screen.py` — inside `screen_pass`, in the `if not in_progress: continue` block, beside the `realized` and `remaining` publishes (around lines 216-238)
- Modify: `screen.py::merge_reference` docstring (around line 78-82) to name `leader` among the dropped measurements
- Test: `tests/test_screen_leader.py` (create)

**Interfaces:**
- Consumes: `scan_log.build_snapshot_row` output rows (fields `ticker`, `label`, `yes_ask`, `yes_bid`)
- Produces:
  - `screen_rules.UNSETTLED_BELOW = 0.90`
  - `screen_rules.leading_bracket(rows: list) -> dict | None` returning `{"ticker", "label", "price", "next_label", "next_price"}`
  - `screen_rules.is_unsettled(leader: dict) -> bool`
  - `screen_reference.json` gains `cities[series]["leader"][day] = {...}`

**Context for the implementer:** `screen_pass` already holds `day_rows` — every open bracket on that series for that climate day, as `build_snapshot_row` dicts. The leader is a pure reduction over rows already in hand, so it costs no request. Price is the YES **ask** (`screen_rules.price_of`), which is what taking that side would cost — the same basis as the Price column everywhere else on the page.

The leader is published for every series on the day in progress, **not** only the unsettled ones. The page applies the threshold, so it can honestly say "12 of 40 still live" rather than only ever seeing the survivors.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screen_leader.py`:

```python
"""The leading bracket on a ladder, and when a ladder counts as unsettled."""
from __future__ import annotations

import screen
import screen_rules


def _row(ticker, label, ask, bid=None):
    return {"ticker": ticker, "label": label, "yes_ask": ask, "yes_bid": bid}


def test_the_leader_is_the_highest_asked_bracket_with_its_runner_up():
    rows = [_row("A", "88° to 89°", 0.12),
            _row("B", "90° to 91°", 0.71),
            _row("C", "92° to 93°", 0.24)]
    got = screen_rules.leading_bracket(rows)
    assert got["ticker"] == "B"
    assert got["label"] == "90° to 91°"
    assert got["price"] == 0.71
    assert got["next_label"] == "92° to 93°"
    assert got["next_price"] == 0.24


def test_a_lone_bracket_has_no_runner_up_rather_than_a_zero():
    # A zero would read as "the market prices the alternative at nothing",
    # which is a claim the single-row ladder never made.
    got = screen_rules.leading_bracket([_row("A", "90° to 91°", 0.71)])
    assert got["next_label"] is None
    assert got["next_price"] is None


def test_an_unquoted_ladder_has_no_leader():
    assert screen_rules.leading_bracket([]) is None
    assert screen_rules.leading_bracket([_row("A", "90° to 91°", None)]) is None


def test_a_bracket_quoted_only_on_the_bid_still_leads():
    # price_of falls back to the bid when there is no offer: you cannot trade
    # the midpoint, but an absent ASK is thin liquidity, not an absent market.
    got = screen_rules.leading_bracket([_row("A", "90° to 91°", None, 0.64)])
    assert got["price"] == 0.64


def test_the_threshold_is_ninety_cents_exclusive():
    # At 90c the market has picked its answer. The boundary matters: 0.90 is
    # settled, 0.89 is not.
    assert screen_rules.is_unsettled({"price": 0.89})
    assert not screen_rules.is_unsettled({"price": 0.90})
    assert not screen_rules.is_unsettled({"price": 0.91})
    assert not screen_rules.is_unsettled({"price": None})
    assert not screen_rules.is_unsettled(None)


def test_merge_reference_drops_a_carried_city_leader():
    # A stale leader price is exactly the failure mode merge_reference exists
    # to prevent, so it is dropped like `realized` and `remaining`.
    previous = {"cities": {"KXHIGHCHI": {
        "station": "KMDW", "timezone": "America/Chicago",
        "days": {"2026-08-16": 90.0},
        "leader": {"2026-08-16": {"ticker": "T", "price": 0.55}}}}}
    got = screen.merge_reference(previous, {"cities": {}})
    assert got["KXHIGHCHI"]["station"] == "KMDW"
    assert "leader" not in got["KXHIGHCHI"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_leader.py -q`
Expected: FAIL with `AttributeError: module 'screen_rules' has no attribute 'leading_bracket'`

- [ ] **Step 3: Implement the rules**

Append to `screen_rules.py`, after `guarded_candidate`:

```python
# ---- Which ladders are still live -----------------------------------------
#
# Not a screening rule and deliberately not shaped like one: it flags no
# mispricing and produces no candidate. It answers the prior question the page
# never answered -- which of the forty ladders has a market at all today, and
# which have already collapsed onto one bracket.

# At or above this the ladder has picked its answer and there is nothing left to
# decide. The mirror of SETTLED_PRICE's logic applied to a whole ladder rather
# than one bracket, and a round number by choice: this is an orientation
# threshold, not a calibrated one.
UNSETTLED_BELOW = 0.90


def leading_bracket(rows: list):
    """The dearest bracket on this ladder and its runner-up, or None.

    Priced off `price_of` -- the YES ask, falling back to the bid -- so the
    number means what Price means everywhere else on the page: what taking that
    side would cost.

    The runner-up is carried as its own label/price pair rather than a nested
    dict, so a consumer reading one field cannot accidentally read the
    leader's. It is None on a one-bracket ladder rather than zero: a zero would
    read as "the market prices the alternative at nothing", which the ladder
    never said."""
    priced = [(price_of(r), r) for r in rows or []]
    priced = [(p, r) for p, r in priced if p is not None]
    if not priced:
        return None
    priced.sort(key=lambda pair: (-pair[0], str(pair[1].get("ticker") or "")))
    price, top = priced[0]
    runner = priced[1] if len(priced) > 1 else None
    return {"ticker": top.get("ticker"), "label": top.get("label"),
            "price": price,
            "next_label": None if runner is None else runner[1].get("label"),
            "next_price": None if runner is None else runner[0]}


def is_unsettled(leader) -> bool:
    """Whether this ladder still has something to decide."""
    price = (leader or {}).get("price")
    return price is not None and float(price) < UNSETTLED_BELOW
```

Note the sort key breaks ties on ticker so the result is deterministic — two brackets at the same price must not reorder between firings.

- [ ] **Step 4: Publish it from `screen.py`**

In `screen_pass`, immediately after the `remaining` publish (the block ending `reference[series].setdefault("remaining", {})[day.isoformat()] = remaining`), add:

```python
            # Which ladder still has something to decide. Published rather than
            # screened: it is a reduction over rows already in hand, so it
            # costs no request here, while pricing forty ladders at page load
            # would cost 20-30s under REQUEST_SPACING_S on every rerun.
            #
            # Published for EVERY series, not only the unsettled ones, so the
            # page can say "12 of 40 still live" rather than only ever seeing
            # the survivors. screen_rules.is_unsettled applies the threshold.
            leader = screen_rules.leading_bracket(day_rows)
            if leader is not None:
                reference[series].setdefault("leader", {})[day.isoformat()] = leader
```

Then extend `merge_reference`'s docstring — change the sentence beginning "The measurements (`days`, `realized`, `remaining`) are deliberately dropped" to read:

```
    The measurements (`days`, `realized`, `remaining`, `leader`) are
    deliberately dropped, because a stale realized extreme is exactly what
    screen_alert fires dead-row pushes from -- and a stale leader price would
    put a market on the unsettled table hours after it settled. Both alert
    rules already degrade safely when they are absent, and the unsettled table
    simply omits a city it cannot price.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_leader.py -q`
Expected: PASS, 6 tests

Run: `python3 -m pytest -q`
Expected: PASS, whole suite — `tests/test_screen_reference.py` covers `merge_reference` and must still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add screen_rules.py screen.py tests/test_screen_leader.py
git commit -m "feat(screen): publish each ladder's leading bracket per firing"
```

---

### Task 5: The unsettled-markets table

**Files:**
- Modify: `screen_view.py` — add a section after `city_timezones` (ends line 679) and call it from `render` (line 1372)
- Test: `tests/test_unsettled_table.py` (create)

**Interfaces:**
- Consumes: `screen_rules.leading_bracket`, `is_unsettled`, `UNSETTLED_BELOW` (Task 4); `scan_log.read_doc`, `scan_log.REFERENCE_PATH`; `screen_view._table`, `_pct`, `city_of`; `scan_cities.city_name`; `screen_forecast.in_progress_day`
- Produces: `screen_view.reference_doc() -> dict`, `screen_view.unsettled_rows(doc, now=None) -> list`, `screen_view.unsettled_caption(doc, shown, total) -> str`, `screen_view._render_unsettled(doc) -> None`, `screen_view._UNSETTLED_COLUMNS`

**Context for the implementer:** `city_timezones()` already reads `screen_reference.json` but projects it down to `{series: timezone}`. Add a `reference_doc()` that returns the whole document, cached the same way, and leave `city_timezones` alone — it has three callers and its narrow return type is deliberate.

Each series carries `leader[day]` for the day that was in progress at the firing. Select the entry whose day equals the day in progress **now** in that series' own city, so a document that has crossed a climate-day boundary drops the stale day rather than showing yesterday's ladder.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_unsettled_table.py`:

```python
"""The unsettled-markets table: which ladders still have something to decide."""
from __future__ import annotations

from datetime import datetime, timezone

import screen_view


def _doc(**series) -> dict:
    return {"generated": "2026-08-16T18:00:00Z", "cities": series}


def _city(tzname, day, price, label="90° to 91°", nxt=("92° to 93°", 0.24)):
    return {"station": "K", "timezone": tzname, "days": {},
            "leader": {day: {"ticker": "T", "label": label, "price": price,
                             "next_label": nxt[0], "next_price": nxt[1]}}}


NOW = datetime(2026, 8, 16, 18, tzinfo=timezone.utc)   # 13:00 LST in Chicago


def test_a_ladder_under_ninety_cents_is_listed():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.55))
    rows = screen_view.unsettled_rows(doc, NOW)
    assert len(rows) == 1
    assert rows[0]["Var"] == "High"
    assert rows[0]["Leader"] == "90° to 91°"
    assert rows[0]["Price"] == "55%"
    assert rows[0]["Runner-up"] == "92° to 93°"
    assert rows[0]["Next"] == "24%"


def test_a_ladder_at_or_over_ninety_cents_is_not():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.94))
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_yesterdays_leader_is_dropped_not_shown_as_today():
    # THE TRAP: the reference is published per firing and survives a climate-day
    # boundary. A leader keyed to a day that is no longer running must vanish,
    # not headline a table titled "still live today".
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-15", 0.55))
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_a_city_with_no_timezone_is_skipped():
    # merge_reference carries identity forward without measurements; a city with
    # no zone is one screen.py could not resolve, and screen_alert skips it too.
    doc = _doc(KXHIGHCHI={"leader": {"2026-08-16": {"price": 0.5}}})
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_rows_are_sorted_by_how_undecided_the_ladder_is():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.80),
               KXLOWTCHI=_city("America/Chicago", "2026-08-16", 0.35),
               KXHIGHTATL=_city("America/New_York", "2026-08-16", 0.60))
    rows = screen_view.unsettled_rows(doc, NOW)
    assert [r["Price"] for r in rows] == ["35%", "60%", "80%"]


def test_the_caption_names_the_denominator_and_the_firing_time():
    # "12 rows" alone cannot be read: 12 of 14 is a quiet day, 12 of 40 is not.
    got = screen_view.unsettled_caption(_doc(), shown=12, total=40)
    assert "12" in got and "40" in got


def test_the_caption_survives_a_document_with_no_stamp():
    got = screen_view.unsettled_caption({"cities": {}}, shown=0, total=0)
    assert isinstance(got, str) and got
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_unsettled_table.py -q`
Expected: FAIL with `AttributeError: module 'screen_view' has no attribute 'unsettled_rows'`

- [ ] **Step 3: Implement the table**

Insert into `screen_view.py` immediately after `city_timezones()` (before the `def _days(` at line 682):

```python
@st.cache_data(ttl=300, show_spinner=False)
def reference_doc() -> dict:
    """The whole reference document screen.py publishes each firing.

    Separate from city_timezones, which projects the same document down to
    {series: zone}. That narrow return type is deliberate and has three
    callers; widening it to serve one more would make every caller carry a
    shape it does not use."""
    return scan_log.read_doc(scan_log.REFERENCE_PATH)


_UNSETTLED_COLUMNS = ["City", "Var", "Leader", "Price", "Runner-up", "Next"]

_UNSETTLED_TIPS = {
    "Leader": "The dearest bracket on this city's ladder — the market's own "
              "answer, as of the last firing.",
    "Price": f"What buying that answer costs. Under "
             f"{round(screen_rules.UNSETTLED_BELOW * 100)}% the ladder has not "
             "collapsed onto one bracket, so the day is still open.",
    "Next": "What the runner-up costs. A close second means the market is "
            "genuinely split rather than merely uncommitted.",
}


def unsettled_rows(doc: dict, now=None) -> list:
    """Display rows for every ladder still under UNSETTLED_BELOW today.

    Keyed to the climate day running RIGHT NOW in each city, not to whatever
    day the firing published: the reference survives a day boundary, and
    yesterday's leader under a heading that says "still live today" is worse
    than no table at all.

    Sorted by price ascending — the least decided ladder is the most
    interesting, and it is the one a glance should land on first."""
    now = now or datetime.now(timezone.utc)
    out = []
    for series, info in ((doc or {}).get("cities") or {}).items():
        tzname = (info or {}).get("timezone")
        if not tzname:
            continue                  # unresolved city; screen_alert skips it too
        today = screen_forecast.in_progress_day(now, tzname).isoformat()
        leader = ((info or {}).get("leader") or {}).get(today)
        if not screen_rules.is_unsettled(leader):
            continue
        variable = scan_log.variable_of_series(series) or ""
        out.append({
            "_price": float(leader["price"]),
            "City": scan_cities.city_name(series),
            "Var": variable.title() or "—",
            "Leader": leader.get("label") or "—",
            "Price": _pct(leader.get("price")),
            "Runner-up": leader.get("next_label") or "—",
            "Next": _pct(leader.get("next_price")),
        })
    out.sort(key=lambda r: (r["_price"], r["City"], r["Var"]))
    for r in out:
        del r["_price"]
    return out


def unsettled_caption(doc: dict, shown: int, total: int) -> str:
    """How many ladders are still live, out of how many, and as of when.

    The denominator is not decoration: 12 of 14 is a quiet afternoon and 12 of
    40 is a busy one, and the count alone cannot tell them apart."""
    stamp = (doc or {}).get("generated")
    when = "the last firing"
    if stamp:
        try:
            when = datetime.fromisoformat(
                str(stamp).replace("Z", "+00:00")).astimezone().strftime("%-I:%M %p")
        except ValueError:
            pass
    return (f"{shown} of {total} ladders still under "
            f"{round(screen_rules.UNSETTLED_BELOW * 100)}%, as of {when}. "
            "Prices are from that firing, not live.")


def _render_unsettled(doc: dict) -> None:
    """Today's still-open ladders, at the top of the page.

    Above the candidates because it answers the prior question: 'where is there
    a market at all' comes before 'which bracket is mispriced'."""
    st.markdown("#### Still Live Today")
    rows = unsettled_rows(doc)
    total = len({s for s, i in ((doc or {}).get("cities") or {}).items()
                 if (i or {}).get("timezone")})
    if not rows:
        st.caption("No reference published yet, or every ladder today has "
                   "already collapsed onto one bracket.")
        return
    st.markdown(_table(_UNSETTLED_COLUMNS, rows, _UNSETTLED_TIPS),
                unsafe_allow_html=True)
    st.caption(unsettled_caption(doc, len(rows), total))
```

- [ ] **Step 4: Call it from `render`**

In `screen_view.render`, immediately after the `st.markdown(_TIP_CSS, ...)` line and before the `try:` that loads candidates, add:

```python
    _render_unsettled(reference_doc())
```

It goes before the candidate load deliberately: the unsettled table reads a different document, and a missing candidate log must not take it down with it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_unsettled_table.py -q`
Expected: PASS, 7 tests

Run: `python3 -m pytest -q`
Expected: PASS, whole suite

- [ ] **Step 6: Verify against real data**

Use the `verify` skill to screenshot the Screen page. Note the live `screen_reference.json` will have no `leader` key until the next Action firing, so the correct first observation is the "No reference published yet…" caption, not a populated table. Confirm the page still renders every existing section.

- [ ] **Step 7: Commit**

```bash
git add screen_view.py tests/test_unsettled_table.py
git commit -m "feat(screen): list today's ladders that are still live"
```

---

### Task 6: Nav order and the Strategy rename

**Files:**
- Modify: `app.py` — the `st.navigation` list at the end of the file
- Modify: `screen_view.py:1374` — the `st.subheader` in `render`
- Modify: `screen_view.py:1` — the module docstring's first line

**Interfaces:**
- Consumes: `app.timeseries_page` (Task 3)
- Produces: nothing importable; display strings only.

**Context for the implementer:** this is display-only. No module is renamed, no import changes, no test asserts on these strings. The `screen_*.py` modules are named for the mechanism, which has not changed.

- [ ] **Step 1: Reorder the nav**

Replace the `st.navigation([...])` list in `app.py` with:

```python
st.navigation([
    st.Page(kalshi_page, title="Forecast", default=True),
    st.Page(screen_page, title="Strategy"),
    st.Page(hourly_page, title="Hourly"),
    st.Page(timeseries_page, title="Timeseries"),
    st.Page(journal_page, title="Journal"),
    st.Page(bet_view.render, title="History"),
    st.Page(trader_page, title="Trader"),
    st.Page(edge_page, title="Edge"),
    st.Page(lab_page, title="Lab"),
    st.Page(accuracy_page, title="Accuracy"),
    st.Page(status_page, title="Status"),
]).run()
```

Eleven entries. Leave the comment above it about the retired Robinhood page exactly as it is.

- [ ] **Step 2: Rename the page heading**

In `screen_view.render`, change:

```python
    st.subheader("Screen — Mispriced Brackets")
```

to:

```python
    st.subheader("Strategy — Mispriced Brackets")
```

- [ ] **Step 3: Update the module docstring's first line**

In `screen_view.py`, change the first docstring line from `"""The Screen page: brackets worth two minutes of attention.` to `"""The Strategy page: brackets worth two minutes of attention.` and leave the rest of the docstring unchanged.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS — no test asserts on a nav title or a subheader string.

- [ ] **Step 5: Verify the nav**

Use the `verify` skill to screenshot the app. Confirm the sidebar reads Forecast, Strategy, Hourly, Timeseries, Journal, History, Trader, Edge, Lab, Accuracy, Status, and that Strategy and Timeseries both open.

- [ ] **Step 6: Commit**

```bash
git add app.py screen_view.py
git commit -m "feat(nav): promote Strategy to second and add Timeseries under Hourly"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| A: source is `api.weather.gov`, not Synoptic | 1 |
| A: `window_for_id` with pagination | 1 |
| A: city dropdown, own session key | 3 |
| A: climate-day extremes above the table | 2 (`day_extremes`), 3 (caption) |
| A: table columns in the city's own zone | 2 (`table_rows`), 3 (`render`) |
| A: 36-hour window, 5-minute refresh | 3 |
| A: precision marker on hourly rows | 2 (`is_hourly`, Feed column), 3 (caption) |
| B: leader rule, today only, 0.90 threshold | 4 |
| B: computed in `screen.py`, published to reference | 4 |
| B: `merge_reference` drops it | 4 |
| B: table on Strategy with firing-time caption | 5 |
| C: nav order, rename | 6 |
| Testing section | tests in 1, 2, 3, 4, 5 |
| Risks: dead station shows a notice | 1 (`window_for_id` returns `[]`), 3 (`st.info`) |

One deviation from the spec, made deliberately and noted in Task 4: the spec implied publishing only unsettled leaders; the plan publishes every leader and applies `UNSETTLED_BELOW` on the page, so the caption can report a denominator. Same cost, strictly more information.

**Placeholder scan:** none. Every step carries the code it needs.

**Type consistency:** `leading_bracket` returns `ticker/label/price/next_label/next_price` in Task 4 and `unsettled_rows` reads exactly those keys in Task 5. `reading()` returns `time/temp_f/dewpoint_f/wind_mph/wind_dir/raw/hourly` in Task 2 and `day_extremes`/`table_rows` read exactly those. `window_for_id` returns raw `properties` dicts in Task 1 and `readings()` consumes exactly that in Task 2.
