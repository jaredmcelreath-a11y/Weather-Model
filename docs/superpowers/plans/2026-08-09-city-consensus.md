# City Consensus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a free 5-model consensus temperature and model spread for all 20 Screen cities, beside the NWS `Ref` the screen already measures gaps from, and log it for later scoring.

**Architecture:** A new standalone entry point (`city_consensus.py`) runs on the existing 30-minute `screen-run` dispatch, reads `screen_reference.json` for each city's LST timezone and realized extreme, fetches all 20 coordinates in ONE Open-Meteo request, folds each model onto the LST climate day itself, and writes `city_consensus.json` to the `scan-data` branch. The Streamlit page only ever reads that document. Nothing in the screening rules, the alert, or `screen_score` changes.

**Tech Stack:** Python 3.11 (CI) / 3.9 (local), `requests`, Streamlit, pytest. No new dependencies.

## Global Constraints

- **Display-only.** The consensus must never gate a flag, never blend into `Ref`, and never be imported by `screen_alert.py`. `screen_rules.py` is not modified by any task in this plan.
- **Equal weight.** Plain mean of the 5 deterministic models. No bias correction, no skill weighting, no per-city sigma, no invented constants.
- **Fold the climate day ourselves.** Never use Open-Meteo's `temperature_2m_max`/`_min`: they aggregate on local time WITH daylight saving, and the climate day is a fixed-LST window. Always `hourly=temperature_2m` with `timeformat=unixtime`, folded with `screen_forecast.lst_offset_hours`.
- **Never crash a page or a pass.** Every network call and document read degrades to `—` / a skipped city with a printed reason.
- **Models list:** `config.DETERMINISTIC_MODELS` = `["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless", "gfs_hrrr"]`.
- **Named constants:** `MIN_MODELS = 3`, `STALE_AFTER_HOURS = 6`.
- **Local test command:** `python3 -m pytest` from the repo root. There is no bare `python` on this Mac.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `sources/open_meteo_cities.py` (create) | One function: N coordinates → raw multi-location response. Knows the API, nothing else. |
| `city_consensus.py` (create) | Pure reduction (fold, extreme, mean, spread) + document build + log rows + `run` entry point. No Streamlit, no trading imports. |
| `scan_cities.py` (modify) | Add `city_key(series)`, a public accessor for the existing private series→city-code map. |
| `screen.py` (modify) | Publish the realized extreme it already computes into `screen_reference.json`. |
| `screen_view.py` (modify) | The `Models` column and the 20-city board. Reads the published document only. |
| `.github/workflows/scan.yml` (modify) | One new step beside the existing screen step. |
| `scripts/check_city_consensus.py` (create) | Live dry-run; prints the built document, writes nothing. |

---

### Task 1: The batch Open-Meteo fetch

**Files:**
- Create: `sources/open_meteo_cities.py`
- Test: `tests/test_open_meteo_cities.py`

**Interfaces:**
- Consumes: `sources.common.get_open_meteo` — signature `get_open_meteo(url, params=None, **kw) -> dict`.
- Produces: `fetch(coords, models=None, forecast_days=3, ttl=900, get=None) -> list[dict]`, where `coords` is `[(lat, lon), ...]` and the result is one dict per coordinate **in request order**.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_open_meteo_cities.py
"""The one batched Open-Meteo request that feeds every city's consensus."""
import pytest

from sources import open_meteo_cities


def _capture():
    """A stub get() that records its params and returns a 2-location payload."""
    seen = {}

    def get(url, params=None, **kw):
        seen["url"] = url
        seen["params"] = params
        seen["kw"] = kw
        return [{"latitude": 1.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [70.0]}},
                {"latitude": 2.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [80.0]}}]

    return get, seen


def test_every_coordinate_goes_in_one_request():
    get, seen = _capture()
    out = open_meteo_cities.fetch([(1.0, -1.5), (2.0, -2.5)], models=["gfs_seamless"], get=get)
    assert len(out) == 2
    assert seen["params"]["latitude"] == "1.0,2.0"
    assert seen["params"]["longitude"] == "-1.5,-2.5"


def test_the_day_fold_is_ours_not_open_meteos():
    # Asking for daily aggregates would give us local-time-WITH-DST days, an
    # hour off the fixed-LST climate day in summer.
    get, seen = _capture()
    open_meteo_cities.fetch([(1.0, -1.5)], models=["gfs_seamless"], get=get)
    assert seen["params"]["hourly"] == "temperature_2m"
    assert "daily" not in seen["params"]
    assert seen["params"]["timeformat"] == "unixtime"


def test_a_single_coordinate_still_yields_a_list():
    # Open-Meteo answers ONE coordinate with a bare object and many with an
    # array. Callers must not have to care.
    def get(url, params=None, **kw):
        return {"latitude": 1.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [70.0]}}

    out = open_meteo_cities.fetch([(1.0, -1.5)], models=["gfs_seamless"], get=get)
    assert isinstance(out, list) and len(out) == 1


def test_no_coordinates_makes_no_request():
    def get(url, params=None, **kw):
        raise AssertionError("must not be called")

    assert open_meteo_cities.fetch([], get=get) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_open_meteo_cities.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.open_meteo_cities'`

- [ ] **Step 3: Write the implementation**

```python
# sources/open_meteo_cities.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_open_meteo_cities.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add sources/open_meteo_cities.py tests/test_open_meteo_cities.py
git commit -m "$(cat <<'EOF'
feat(consensus): batch every city into one Open-Meteo request

20 coordinates x 5 models x 72 hours measured at 0.7s and 62 KB, which is what
makes a per-city consensus affordable at the 30-minute screen cadence.

Hourly + unixtime rather than Open-Meteo's daily aggregates: those cut the day
on local time WITH daylight saving, an hour off the fixed-LST climate day.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The pure reduction — fold, extreme, consensus

**Files:**
- Create: `city_consensus.py`
- Test: `tests/test_city_consensus.py`

**Interfaces:**
- Consumes: `screen_forecast.lst_offset_hours(tzname) -> int`.
- Produces:
  - `MIN_MODELS = 3`, `STALE_AFTER_HOURS = 6`
  - `series_extreme(times, temps, day, offset_hours) -> dict` → `{"high": float|None, "low": float|None}`
  - `model_extremes(hourly, day, offset_hours) -> dict` → `{"gfs_seamless": {"high": .., "low": ..}, ...}`
  - `consensus(values) -> dict|None` → `{"value": float, "spread": float, "n": int}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_city_consensus.py
"""The consensus reduction: fold onto an LST climate day, then average models."""
from datetime import date, datetime, timedelta, timezone

import city_consensus


def _ts(y, m, d, hour):
    """Unix seconds for a UTC wall time."""
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def test_the_extreme_covers_the_lst_day_not_the_utc_day():
    # Denver LST is UTC-7 all year. The Aug 7 climate day therefore runs
    # 07:00Z Aug 7 to 07:00Z Aug 8. The 90 at 06:00Z Aug 7 belongs to Aug 6 and
    # must NOT become Aug 7's high.
    times = [_ts(2026, 8, 7, 6), _ts(2026, 8, 7, 12), _ts(2026, 8, 8, 2)]
    temps = [90.0, 80.0, 70.0]
    got = city_consensus.series_extreme(times, temps, date(2026, 8, 7), -7)
    assert got == {"high": 80.0, "low": 70.0}


def test_a_daylight_saving_local_day_would_have_been_an_hour_wrong():
    # 06:30Z Aug 8 is 01:30 local MDT on Aug 8 -- inside the LOCAL day, outside
    # the LST climate day, which ended at 07:00Z. Folding on local time would
    # wrongly pull this reading in.
    times = [_ts(2026, 8, 8, 2), _ts(2026, 8, 8, 6)]
    temps = [70.0, 55.0]
    got = city_consensus.series_extreme(times, temps, date(2026, 8, 7), -7)
    assert got["low"] == 70.0          # NOT 55.0


def test_nulls_are_skipped_not_counted_as_zero():
    times = [_ts(2026, 8, 7, 12), _ts(2026, 8, 7, 13)]
    got = city_consensus.series_extreme(times, [None, 80.0], date(2026, 8, 7), -7)
    assert got == {"high": 80.0, "low": 80.0}


def test_a_day_with_no_readings_has_no_extreme():
    got = city_consensus.series_extreme([_ts(2026, 8, 9, 12)], [80.0],
                                        date(2026, 8, 7), -7)
    assert got == {"high": None, "low": None}


def test_model_extremes_are_keyed_by_model_not_by_api_column():
    hourly = {"time": [_ts(2026, 8, 7, 12), _ts(2026, 8, 7, 18)],
              "temperature_2m_gfs_seamless": [80.0, 92.0],
              "temperature_2m_gfs_hrrr": [81.0, 93.0]}
    got = city_consensus.model_extremes(hourly, date(2026, 8, 7), -7)
    assert got["gfs_seamless"]["high"] == 92.0
    assert got["gfs_hrrr"]["high"] == 93.0


def test_a_model_that_returned_only_nulls_is_absent():
    # HRRR past its 48-hour range, or a model outage.
    hourly = {"time": [_ts(2026, 8, 7, 12)],
              "temperature_2m_gfs_seamless": [80.0],
              "temperature_2m_gfs_hrrr": [None]}
    got = city_consensus.model_extremes(hourly, date(2026, 8, 7), -7)
    assert "gfs_hrrr" not in got


def test_consensus_is_the_plain_mean_and_the_full_spread():
    got = city_consensus.consensus([92.0, 91.5, 92.8, 92.9, 91.3])
    assert got["value"] == 92.1
    assert got["spread"] == 1.6
    assert got["n"] == 5


def test_consensus_needs_at_least_three_models():
    # Two models agreeing is not agreement, and the spread of two is a range,
    # not a distribution.
    assert city_consensus.consensus([92.0, 91.0]) is None
    assert city_consensus.consensus([]) is None


def test_consensus_survives_a_missing_model():
    # Routine for tomorrow, where HRRR does not reach.
    got = city_consensus.consensus([92.0, 91.5, 92.8, 92.9])
    assert got["n"] == 4


def test_consensus_ignores_nones_in_the_list():
    got = city_consensus.consensus([92.0, None, 91.5, 92.8, 92.9])
    assert got["n"] == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_city_consensus.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'city_consensus'`

- [ ] **Step 3: Write the implementation**

```python
# city_consensus.py
"""A free 5-model consensus temperature for every screened city.

The Screen page measures every gap from the NWS point forecast alone. This is
the independent second opinion: the same five deterministic models the KDFW
model already trusts, equal-weighted, with their spread as the honest statement
of how much they agree.

DISPLAY ONLY. Nothing here gates a flag, blends into Ref, or is imported by
screen_alert -- so screen_score's settled track record stays comparable across
this change.

Equal weight, deliberately. Skill weights at KDFW came from months of
self-scoring AT THAT STATION; nothing equivalent exists for Denver or Miami, and
inventing one would repeat the 2026-07-17 season-readiness bug where a number
derived from no data printed 0% and produced a live "BUY NO +85". The log this
module writes is how that can change on evidence instead of taste.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import screen_forecast

# Below this many contributing models there is no consensus worth printing: two
# models agreeing is not agreement, and the spread of two is a range.
MIN_MODELS = 3

# Past this the published document is too old to show. The globals refresh every
# 6 hours and HRRR hourly, so a document this stale means the Action has been
# failing, not that the models have not moved.
STALE_AFTER_HOURS = 6

_PREFIX = "temperature_2m_"


def _lst_date(unix_ts, offset_hours: int) -> date:
    """The fixed-LST calendar date this instant falls on."""
    return (datetime.fromtimestamp(int(unix_ts), timezone.utc)
            + timedelta(hours=offset_hours)).date()


def series_extreme(times: list, temps: list, day: date,
                   offset_hours: int) -> dict:
    """{'high': f, 'low': f} for one model over one LST climate day.

    Mirrors screen_forecast.daily_extremes, which does the same reduction for
    NWS periods -- same day boundary, same Nones-when-absent contract."""
    values = [float(t) for stamp, t in zip(times or [], temps or [])
              if t is not None and _lst_date(stamp, offset_hours) == day]
    if not values:
        return {"high": None, "low": None}
    return {"high": max(values), "low": min(values)}


def model_extremes(hourly: dict, day: date, offset_hours: int) -> dict:
    """{model: {'high': f, 'low': f}} for every model with data on `day`.

    A model whose series is entirely null on this day is ABSENT rather than
    present with Nones -- routine for HRRR, which does not reach tomorrow."""
    times = (hourly or {}).get("time") or []
    out = {}
    for key, temps in (hourly or {}).items():
        if not key.startswith(_PREFIX):
            continue
        got = series_extreme(times, temps, day, offset_hours)
        if got["high"] is not None:
            out[key[len(_PREFIX):]] = got
    return out


def consensus(values: list):
    """{'value','spread','n'} over these model extremes, or None.

    Plain mean and full range. Rounded to a tenth because the inputs are
    tenths and a consensus printed to more places than its members would claim
    precision the models do not have."""
    numbers = [float(v) for v in (values or []) if v is not None]
    if len(numbers) < MIN_MODELS:
        return None
    return {"value": round(sum(numbers) / len(numbers), 1),
            "spread": round(max(numbers) - min(numbers), 1),
            "n": len(numbers)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_city_consensus.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add city_consensus.py tests/test_city_consensus.py
git commit -m "$(cat <<'EOF'
feat(consensus): fold models onto the LST climate day and average them

Equal-weight mean of the five deterministic models with their full spread. No
bias correction or skill weighting: those came from months of self-scoring at
KDFW, and inventing them for 19 other cities is the season-readiness
phantom-edge bug again.

The fold is ours because Open-Meteo's daily aggregates cut on local time WITH
daylight saving, an hour off the fixed-LST climate day in summer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Publish the realized extreme into the reference

**Files:**
- Modify: `screen.py` (the `if not in_progress: continue` block, currently ~line 185)
- Modify: `scan_cities.py` (add `city_key`)
- Test: `tests/test_screen_reference.py`, `tests/test_screen_view.py`

**Why:** the consensus must be folded with realized temperature exactly as `Ref` is, or a 3pm reading of "high 91" would sit one cell from a folded number after 93 already happened. `screen.py` computes that extreme already and throws it away.

**Interfaces:**
- Produces: `screen_reference.json` gains `cities[series]["realized"] = {"YYYY-MM-DD": float|None}`, present only for a climate day already in progress. Additive — `screen_alert` ignores unknown keys.
- Produces: `scan_cities.city_key(series) -> str|None` (e.g. `"KXLOWTDEN"` → `"DEN"`).

- [ ] **Step 1: Write the failing tests**

`tests/test_screen_reference.py` already has a `_deps(published)` harness and a
module-level `_NOW` of `2026-08-07T18:30Z`. Its `fetch_obs` returns `[]`, which
yields no realized extreme, so these tests need observations. Append:

```python
# append to tests/test_screen_reference.py
def _obs(temp_c):
    """Two readings inside the Aug 7 Denver climate day, in Celsius as NWS
    sends them. Two, because realized_extreme needs MIN_OBS_SUPPORT of them."""
    return [{"properties": {"timestamp": "2026-08-07T12:00:00+00:00",
                            "temperature": {"value": temp_c}}},
            {"properties": {"timestamp": "2026-08-07T13:00:00+00:00",
                            "temperature": {"value": temp_c}}}]


def test_the_reference_publishes_the_realized_extreme_for_today():
    # city_consensus folds its models against this rather than refetching 20
    # cities' observations, so the board and the Models column sit on the same
    # basis as Ref.
    published = []
    deps = _deps(published)
    deps.fetch_obs = lambda station, start, end: _obs(16.1)      # 61.0 F
    screen.screen_pass(_NOW, deps)
    city = published[0]["cities"]["KXLOWTDEN"]
    assert city["realized"]["2026-08-07"] == 61.0


def test_a_day_not_yet_in_progress_has_no_realized_entry():
    # The market listed for Aug 8 is screened, but nothing has been realized on
    # it — an entry of None would read as "measured, and it is nothing".
    published = []
    deps = _deps(published)
    deps.list_markets = lambda series, status=None: [
        _market("KXLOWTDEN-26AUG07-T71"), _market("KXLOWTDEN-26AUG08-T71")]
    deps.fetch_obs = lambda station, start, end: _obs(16.1)
    screen.screen_pass(_NOW, deps)
    realized = published[0]["cities"]["KXLOWTDEN"].get("realized") or {}
    assert "2026-08-08" not in realized
```

```python
# append to tests/test_screen_view.py — add `import scan_cities` at the top if
# it is not already imported.
def test_city_key_maps_a_series_to_its_city_code():
    assert scan_cities.city_key("KXLOWTDEN") == "DEN"
    assert scan_cities.city_key("kxhighny") == "NYC"
    assert scan_cities.city_key("KXNOTACITY") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_reference.py tests/test_screen_view.py -q`
Expected: FAIL — `KeyError: 'realized'` and `AttributeError: module 'scan_cities' has no attribute 'city_key'`

- [ ] **Step 3: Write the implementation**

In `scan_cities.py`, directly after `name_of`:

```python
def city_key(series: str):
    """The CITY key ('DEN') behind a Kalshi series, or None if unmapped.

    The code, not the display name: it is what the consensus log files rows
    under, and a display name can be reworded without breaking a log."""
    return _SERIES_CITY.get((series or "").upper())
```

In `screen.py`, inside the day loop, replace:

```python
            if not in_progress:
                continue
            bound = screen_rules.realized_extreme(realized, variable)
```

with:

```python
            if not in_progress:
                continue
            bound = screen_rules.realized_extreme(realized, variable)
            # Published so city_consensus can fold its models against the same
            # realized extreme Ref is folded with, without refetching 20
            # cities' observations. Only for a day in progress -- tomorrow has
            # nothing realized, and an entry of None would read as "measured,
            # and it is nothing".
            reference[series].setdefault("realized", {})[day.isoformat()] = bound
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass, including the existing `screen_alert` tests — the new key is additive.

- [ ] **Step 5: Commit**

```bash
git add screen.py scan_cities.py tests/test_screen_reference.py tests/test_screen_view.py
git commit -m "$(cat <<'EOF'
feat(screen): publish the realized extreme the pass already computes

city_consensus folds its models against this so the Models column and the
20-city board sit on the same basis as Ref. Without it a 3pm consensus could
print a high of 91 next to a folded Ref after 93 had already happened.

Additive key, only for a climate day in progress; screen_alert ignores it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Build and publish the consensus document

**Files:**
- Modify: `city_consensus.py`
- Test: `tests/test_city_consensus_build.py`

**Interfaces:**
- Consumes: `open_meteo_cities.fetch`, `scan_cities.city_key/coords_of/name_of`, `screen_forecast.lst_offset_hours/fold_realized`, `scan_log.read_doc/write_doc`, `scan_log.REFERENCE_PATH`.
- Produces:
  - `CONSENSUS_PATH = "city_consensus.json"`
  - `cities_from_reference(reference) -> list[dict]` — each `{"code","name","lat","lon","timezone","series":{"high":s,"low":s},"realized":{day:{variable:float}}}`, sorted by `code`
  - `target_days(now, tzname) -> list[date]` — today and tomorrow in that city's LST
  - `build(reference, raw, cities, now) -> dict`
  - `Deps` dataclass + `run(now, deps) -> dict`

**Document shape** (each variable entry):

```json
{"generated": "2026-08-09T22:00:00Z",
 "cities": {"DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
   "2026-08-09": {"high": {"nws": 95.0, "nws_folded": 95.0,
                           "cons": 92.1, "cons_folded": 93.0,
                           "spread": 1.4, "n": 5,
                           "models": {"gfs_seamless": 92.0}}}}}}}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_city_consensus_build.py
"""Building the published consensus document from a reference plus one fetch."""
from datetime import date, datetime, timezone

import city_consensus

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)   # 13:00 LST in Denver

_REFERENCE = {
    "generated": "2026-08-07T19:55:00Z",
    "cities": {
        "KXHIGHDEN": {"station": "KDEN", "timezone": "America/Denver",
                      "days": {"2026-08-07": 95.0, "2026-08-08": 97.0},
                      "realized": {"2026-08-07": 96.0}},
        "KXLOWTDEN": {"station": "KDEN", "timezone": "America/Denver",
                      "days": {"2026-08-07": 63.0, "2026-08-08": 64.0},
                      "realized": {"2026-08-07": 61.0}},
    },
}


def _ts(y, m, d, hour):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def _payload(highs):
    """One location's hourly block: a noon reading per model on Aug 7."""
    hourly = {"time": [_ts(2026, 8, 7, 19)]}
    for model, value in highs.items():
        hourly[f"temperature_2m_{model}"] = [value]
    return {"hourly": hourly}


_RAW = [_payload({"gfs_seamless": 92.0, "ecmwf_ifs025": 91.5,
                  "icon_seamless": 92.8, "gem_seamless": 92.9,
                  "gfs_hrrr": 91.3})]


def test_cities_come_from_the_reference_one_row_per_city():
    got = city_consensus.cities_from_reference(_REFERENCE)
    assert [c["code"] for c in got] == ["DEN"]          # two series, one city
    city = got[0]
    assert city["name"] == "Denver"
    assert city["series"] == {"high": "KXHIGHDEN", "low": "KXLOWTDEN"}
    assert city["realized"]["2026-08-07"] == {"high": 96.0, "low": 61.0}


def test_a_city_with_no_timezone_is_skipped():
    reference = {"cities": {"KXLOWTDEN": {"station": "KDEN", "days": {}}}}
    assert city_consensus.cities_from_reference(reference) == []


def test_target_days_are_today_and_tomorrow_in_local_standard_time():
    assert city_consensus.target_days(_NOW, "America/Denver") == [
        date(2026, 8, 7), date(2026, 8, 8)]


def test_the_document_carries_both_folded_and_unfolded():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    high = doc["cities"]["DEN"]["days"]["2026-08-07"]["high"]
    assert high["cons"] == 92.1                 # the models' own number
    assert high["cons_folded"] == 96.0          # 96 already happened today
    assert high["nws"] == 95.0
    assert high["nws_folded"] == 96.0
    assert high["spread"] == 1.6 and high["n"] == 5
    assert high["models"]["ecmwf_ifs025"] == 91.5


def test_a_low_folds_downward_not_upward():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    low = doc["cities"]["DEN"]["days"]["2026-08-07"]["low"]
    assert low["nws_folded"] == 61.0            # realized 61 beats forecast 63


def test_tomorrow_has_no_realized_so_folding_is_a_no_op():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    tomorrow = doc["cities"]["DEN"]["days"]["2026-08-08"]
    # No model reading falls on Aug 8 in this payload, so there is no consensus
    # -- but the NWS side is still published, unfolded and folded alike.
    assert tomorrow["high"]["nws"] == 97.0
    assert tomorrow["high"]["nws_folded"] == 97.0
    assert tomorrow["high"]["cons"] is None


def test_the_document_is_stamped_so_the_page_can_age_it():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    assert doc["generated"] == "2026-08-07T20:00:00Z"


def test_run_writes_the_document():
    written = {}
    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=lambda coords: _RAW,
        write_doc=lambda path, obj: written.update({path: obj}),
        append_rows=lambda path, rows: len(rows),
    )
    got = city_consensus.run(_NOW, deps)
    assert got["cities"] == 1
    assert city_consensus.CONSENSUS_PATH in written


def test_run_without_a_reference_does_nothing_and_never_raises():
    deps = city_consensus.Deps(
        read_reference=lambda: {},
        fetch=lambda coords: (_ for _ in ()).throw(AssertionError("no fetch")),
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: 0,
    )
    assert city_consensus.run(_NOW, deps)["cities"] == 0


def test_a_failed_fetch_costs_the_pass_nothing_but_the_document():
    def boom(coords):
        raise RuntimeError("429")

    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=boom,
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: 0,
    )
    assert city_consensus.run(_NOW, deps)["cities"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_city_consensus_build.py -q`
Expected: FAIL — `AttributeError: module 'city_consensus' has no attribute 'cities_from_reference'`

- [ ] **Step 3: Write the implementation**

Append to `city_consensus.py`:

```python
import time
from dataclasses import dataclass
from typing import Callable

import scan_cities
import scan_log
from sources import open_meteo_cities

CONSENSUS_PATH = "city_consensus.json"


def cities_from_reference(reference: dict) -> list:
    """One entry per CITY from the reference's 40 series, sorted by code.

    The reference is keyed by series (two per city, one per variable); the
    consensus is per city, because a city has one coordinate and one fetch.
    A series with no timezone is one screen.py could not resolve and
    screen_alert already skips -- so it is skipped identically here."""
    cities = {}
    for series, info in (reference.get("cities") or {}).items():
        tzname = (info or {}).get("timezone")
        code = scan_cities.city_key(series)
        point = scan_cities.point_for(series)
        if not tzname or code is None or point is None:
            continue
        variable = scan_log.variable_of_series(series)
        entry = cities.setdefault(code, {
            "code": code, "name": scan_cities.name_of(code) or code,
            "lat": point[0], "lon": point[1], "timezone": tzname,
            "series": {}, "nws": {}, "realized": {},
        })
        entry["series"][variable] = series
        for day, value in ((info.get("days") or {})).items():
            entry["nws"].setdefault(day, {})[variable] = value
        for day, value in ((info.get("realized") or {})).items():
            entry["realized"].setdefault(day, {})[variable] = value
    return [cities[k] for k in sorted(cities)]


def target_days(now: datetime, tzname: str) -> list:
    """The climate day running now in this city, and the one after it.

    Exactly the two days Kalshi lists temperature markets for."""
    today = screen_forecast.in_progress_day(now, tzname)
    return [today, today + timedelta(days=1)]


def _entry(nws, cons, realized, variable: str) -> dict:
    """One variable's published block: both forms, plus the model detail.

    Unfolded is what a forecast SAID and is what the log scores. Folded is what
    can still be true given what has already happened, and is the only form
    comparable with the page's Ref."""
    value = None if cons is None else cons["value"]
    realized_list = [] if realized is None else [realized]
    return {
        "nws": nws,
        "nws_folded": screen_forecast.fold_realized(nws, realized_list, variable),
        "cons": value,
        "cons_folded": screen_forecast.fold_realized(value, realized_list,
                                                     variable),
        "spread": None if cons is None else cons["spread"],
        "n": 0 if cons is None else cons["n"],
    }


def build(reference: dict, raw: list, cities: list, now: datetime) -> dict:
    """The published document: every city, both days, both variables."""
    out = {}
    for city, payload in zip(cities, raw or []):
        offset = screen_forecast.lst_offset_hours(city["timezone"])
        days = {}
        for day in target_days(now, city["timezone"]):
            key = day.isoformat()
            extremes = model_extremes((payload or {}).get("hourly") or {},
                                      day, offset)
            block = {}
            for variable in ("high", "low"):
                values = [e[variable] for e in extremes.values()]
                cons = consensus(values)
                entry = _entry((city["nws"].get(key) or {}).get(variable),
                               cons,
                               (city["realized"].get(key) or {}).get(variable),
                               variable)
                entry["models"] = {m: e[variable] for m, e in extremes.items()}
                block[variable] = entry
            days[key] = block
        out[city["code"]] = {"name": city["name"], "timezone": city["timezone"],
                             "days": days}
    return {"generated": now.isoformat().replace("+00:00", "Z"), "cities": out}


@dataclass
class Deps:
    read_reference: Callable
    fetch: Callable
    write_doc: Callable
    append_rows: Callable


def _real_deps() -> Deps:
    return Deps(
        read_reference=lambda: scan_log.read_doc(scan_log.REFERENCE_PATH),
        fetch=lambda coords: open_meteo_cities.fetch(coords),
        write_doc=lambda path, obj: scan_log.write_doc(path, obj),
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
    )


def run(now: datetime, deps: Deps) -> dict:
    """One pass: fetch every city at once, publish the document."""
    reference = deps.read_reference() or {}
    cities = cities_from_reference(reference)
    if not cities:
        print("[city_consensus] no reference on the data branch — skipped")
        return {"cities": 0, "logged": 0}
    try:
        raw = deps.fetch([(c["lat"], c["lon"]) for c in cities])
    except Exception as e:                # noqa: BLE001 - a dead upstream must
        print(f"[city_consensus] fetch skipped ({e})")   # not fail the job
        return {"cities": 0, "logged": 0}
    doc = build(reference, raw, cities, now)
    deps.write_doc(CONSENSUS_PATH, doc)
    return {"cities": len(doc["cities"]), "logged": 0}
```

> **Note:** the `import` lines above belong at the top of the module with the existing ones, not mid-file. `scan_log.variable_of_series(series)` already exists and returns `"high"`/`"low"` from the series prefix.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_city_consensus_build.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add city_consensus.py tests/test_city_consensus_build.py
git commit -m "$(cat <<'EOF'
feat(consensus): build and publish the per-city document

One entry per city per day per variable, carrying both forms: unfolded for the
log and future scoring, folded for display beside Ref. Cities, timezones and
realized extremes all come from screen_reference.json rather than being
resolved a second time.

A missing reference or a dead upstream costs the document, never the job.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The hourly forecast log

**Files:**
- Modify: `city_consensus.py`
- Test: `tests/test_city_consensus_log.py`

**Interfaces:**
- Produces:
  - `LOG_PATH = "city_consensus.jsonl"`
  - `should_log(now) -> bool`
  - `log_rows(doc, now) -> list[dict]`
  - `run` now returns `{"cities": int, "logged": int}` with a real `logged` count.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_city_consensus_log.py
"""The hourly forecast log that later answers 'is the consensus better?'."""
from datetime import datetime, timezone

import city_consensus

_DOC = {
    "generated": "2026-08-07T20:00:00Z",
    "cities": {"DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
        "2026-08-07": {
            "high": {"nws": 95.0, "nws_folded": 96.0, "cons": 92.1,
                     "cons_folded": 96.0, "spread": 1.6, "n": 5,
                     "models": {"gfs_seamless": 92.0}},
            "low": {"nws": 63.0, "nws_folded": 61.0, "cons": None,
                    "cons_folded": None, "spread": None, "n": 0, "models": {}},
        }}}},
}


def test_the_log_fires_once_an_hour_not_once_a_pass():
    # Dispatches land at :00 and :30; 80 rows every pass would be 3,840/day into
    # a file append_many rewrites whole each time.
    assert city_consensus.should_log(datetime(2026, 8, 7, 20, 1, tzinfo=timezone.utc))
    assert not city_consensus.should_log(datetime(2026, 8, 7, 20, 31, tzinfo=timezone.utc))


def test_a_row_carries_the_unfolded_forecast_not_the_folded_one():
    # The scorer grades what the forecast SAID, not what had already happened.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["nws"] == 95.0 and high["cons"] == 92.1
    assert "nws_folded" not in high and "cons_folded" not in high


def test_a_row_identifies_its_city_day_and_variable():
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["city"] == "DEN"
    assert high["day"] == "2026-08-07"
    assert high["ts"] == "2026-08-07T20:00:00Z"


def test_per_model_values_are_kept():
    # 60 bytes, and the only way to later discover ECMWF alone wins in Denver.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["models"] == {"gfs_seamless": 92.0}


def test_a_variable_with_no_consensus_is_not_logged():
    # Nothing to score, and a row of Nones would dilute any later hit rate.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    assert [r["variable"] for r in rows] == ["high"]


def test_run_logs_on_the_hour_and_stays_quiet_off_it():
    _REFERENCE = {"cities": {"KXHIGHDEN": {"timezone": "America/Denver",
                                           "days": {}, "realized": {}},
                             "KXLOWTDEN": {"timezone": "America/Denver",
                                           "days": {}, "realized": {}}}}
    appended = []
    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=lambda coords: [{"hourly": {"time": [], }}],
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: appended.append(rows) or len(rows),
    )
    off_hour = datetime(2026, 8, 7, 20, 45, tzinfo=timezone.utc)
    assert city_consensus.run(off_hour, deps)["logged"] == 0
    assert appended == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_city_consensus_log.py -q`
Expected: FAIL — `AttributeError: module 'city_consensus' has no attribute 'should_log'`

- [ ] **Step 3: Write the implementation**

Add to `city_consensus.py` (constant beside `CONSENSUS_PATH`, functions before `run`):

```python
LOG_PATH = "city_consensus.jsonl"


def should_log(now: datetime) -> bool:
    """Whether this pass writes a log row.

    Once an hour, not once a pass: 20 cities x 2 variables x 2 days is 80 rows,
    and append_many rewrites the whole daily partition on every append. Hourly
    is already finer than the models update -- 6 hours for the globals, 1 for
    HRRR. Dispatches land at :00 and :30, so this picks exactly one of them.

    In Python rather than a bash minute-test so it can be tested; scan.yml has
    the shell version of this pattern for settlement and it is untestable."""
    return now.minute < 30


def log_rows(doc: dict, now: datetime) -> list:
    """One row per city / day / variable that has a consensus.

    UNFOLDED values only: a scorer grades what the forecast said, and folding
    mixes in what had already happened. A variable with no consensus is omitted
    rather than logged as Nones -- there is nothing to score, and the empty
    rows would dilute any later hit rate."""
    stamp = now.isoformat().replace("+00:00", "Z")
    rows = []
    for code, city in (doc.get("cities") or {}).items():
        for day, block in (city.get("days") or {}).items():
            for variable, entry in (block or {}).items():
                if (entry or {}).get("cons") is None:
                    continue
                rows.append({"ts": stamp, "city": code, "day": day,
                             "variable": variable, "nws": entry.get("nws"),
                             "cons": entry["cons"], "spread": entry.get("spread"),
                             "n": entry.get("n"), "models": entry.get("models")})
    return rows
```

Then replace the last two lines of `run` with:

```python
    doc = build(reference, raw, cities, now)
    deps.write_doc(CONSENSUS_PATH, doc)
    logged = 0
    if should_log(now):
        rows = log_rows(doc, now)
        if rows:
            logged = deps.append_rows(LOG_PATH, rows) or 0
    return {"cities": len(doc["cities"]), "logged": logged}
```

And add the CLI entry point at the end of the module:

```python
def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    if (argv[0] if argv else "") == "run":
        deps = deps or _real_deps()
        print(f"[city_consensus] {run(now or datetime.now(timezone.utc), deps)}")
        return 0
    print("usage: city_consensus.py run")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_city_consensus_log.py tests/test_city_consensus_build.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add city_consensus.py tests/test_city_consensus_log.py
git commit -m "$(cat <<'EOF'
feat(consensus): log the forecast hourly so it can be scored later

Unfolded values plus per-model detail, one row per city/day/variable. Hourly
rather than per-pass: 80 rows a pass is 3,840/day into a file append_many
rewrites whole, and hourly is already finer than the models update.

Truth needs no new pipeline -- scan_settled.jsonl already records Kalshi's
finalized bracket per city per day, which pins the settled temperature.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire it to the schedule, with a live dry-run

**Files:**
- Modify: `.github/workflows/scan.yml` (after the "Screen for mispriced brackets" step, ~line 103)
- Create: `scripts/check_city_consensus.py`

**Interfaces:** consumes `city_consensus.run` / `_real_deps` / `CONSENSUS_PATH`.

- [ ] **Step 1: Add the workflow step**

Immediately after the `Screen for mispriced brackets` step:

```yaml
      # The consensus rides the same dispatch, right after the screen, because
      # it reads the reference that step just published. ONE Open-Meteo request
      # covers all 20 cities (~0.7s), and it runs here rather than in the app so
      # the Streamlit Cloud shared-IP rate limit can never reach it.
      #
      # Deliberately AFTER the screen and not part of it: a broken consensus
      # must never cost a screen pass.
      - name: City model consensus
        if: >-
          github.event_name == 'schedule'
          || github.event_name == 'repository_dispatch'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: python city_consensus.py run
```

- [ ] **Step 2: Add `consensus` to the manual dispatch branch**

In the `Manual run` step's shell, change:

```bash
          if [ "$COMMAND" = "screen" ]; then
```

to:

```bash
          if [ "$COMMAND" = "consensus" ]; then
            python city_consensus.py run
          elif [ "$COMMAND" = "screen" ]; then
```

and add `consensus` to the `command` input description: `"snapshot | settle | report | screen | score | consensus"`.

- [ ] **Step 3: Write the dry-run script**

```python
# scripts/check_city_consensus.py
"""Build ONE consensus document against live data, writing nothing. By hand.

Prints the reference's age, how many cities were built, and a sample city, so a
change can be checked against the real 20-city fetch before it reaches the
schedule. Neither the document nor the log is written.

Needs SCAN_GH_REPO/SCAN_GH_BRANCH in the environment to read the scan-data
branch (public: no token required for reads).

Usage: SCAN_GH_REPO=owner/repo python3 scripts/check_city_consensus.py
"""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import city_consensus          # noqa: E402


def main():
    now = datetime.now(timezone.utc)
    real = city_consensus._real_deps()
    deps = city_consensus.Deps(
        read_reference=real.read_reference,
        fetch=real.fetch,
        write_doc=lambda path, obj: print(f"[stubbed] would write {path}"),
        append_rows=lambda path, rows: print(
            f"[stubbed] would append {len(rows)} rows to {path}") or len(rows),
    )
    result = city_consensus.run(now, deps)
    print(f"result: {result}")
    if not result["cities"]:
        return 1

    reference = real.read_reference()
    cities = city_consensus.cities_from_reference(reference)
    raw = real.fetch([(c["lat"], c["lon"]) for c in cities])
    doc = city_consensus.build(reference, raw, cities, now)
    sample = sorted(doc["cities"])[0]
    print(f"\n--- {sample} ---")
    print(json.dumps(doc["cities"][sample], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it against live data**

Run: `SCAN_GH_REPO=jaredmcelreath-a11y/Weather-Model SCAN_GH_BRANCH=scan-data python3 scripts/check_city_consensus.py`

Expected: `result: {'cities': 20, 'logged': ...}` and a sample city showing both days, both variables, `n` of 5 today and commonly 4 tomorrow (HRRR does not reach). Confirm no `429`.

- [ ] **Step 5: Run the full suite and commit**

```bash
python3 -m pytest -q
git add .github/workflows/scan.yml scripts/check_city_consensus.py
git commit -m "$(cat <<'EOF'
feat(consensus): run on the screen dispatch, with a live dry-run

Rides the existing 30-minute cadence right after the screen step, because it
reads the reference that step publishes. Runs in Actions rather than the app so
the Streamlit shared-IP rate limit cannot reach it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The `Models` column

**Files:**
- Modify: `screen_view.py` (`_TIPS`, `_COLUMNS`, `_candidate_row`, `render`)
- Test: `tests/test_screen_view_consensus.py`

**Interfaces:**
- Consumes: `city_consensus.CONSENSUS_PATH`, `STALE_AFTER_HOURS`; `scan_cities.city_key`; `screen_forecast.climate_day_of_ticker`.
- Produces:
  - `consensus_doc() -> dict` (Streamlit-cached, ttl 300)
  - `doc_is_fresh(doc, now=None) -> bool`
  - `consensus_entry(row, doc) -> dict|None`
  - `consensus_cell(row, doc, now=None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_view_consensus.py
"""The Models column: the consensus shown beside Ref on a candidate row."""
from datetime import datetime, timedelta, timezone

import screen_view

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)

_DOC = {
    "generated": "2026-08-07T19:45:00Z",
    "cities": {"DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
        "2026-08-07": {
            "high": {"nws": 95.0, "nws_folded": 96.0, "cons": 92.1,
                     "cons_folded": 96.0, "spread": 1.6, "n": 5, "models": {}},
            "low": {"nws": 63.0, "nws_folded": 61.0, "cons": 63.4,
                    "cons_folded": 61.0, "spread": 0.8, "n": 4, "models": {}},
        }}}},
}


def _row(ticker="KXLOWTDEN-26AUG07-B62.5", series="KXLOWTDEN", variable="low"):
    return {"series": series, "ticker": ticker, "variable": variable}


def test_the_cell_shows_the_folded_consensus_and_its_spread():
    # Folded, because Ref one cell away is folded -- an unfolded number beside
    # it would invite a false comparison.
    assert screen_view.consensus_cell(_row(), _DOC, _NOW) == "61.0 ±0.8"


def test_a_high_reads_its_own_variable():
    row = _row("KXHIGHDEN-26AUG07-T94", "KXHIGHDEN", "high")
    assert screen_view.consensus_cell(row, _DOC, _NOW) == "96.0 ±1.6"


def test_a_city_absent_from_the_document_reads_as_a_dash():
    row = _row("KXLOWTMIA-26AUG07-B76.5", "KXLOWTMIA", "low")
    assert screen_view.consensus_cell(row, _DOC, _NOW) == "—"


def test_a_day_the_document_does_not_cover_reads_as_a_dash():
    assert screen_view.consensus_cell(
        _row("KXLOWTDEN-26AUG12-B62.5"), _DOC, _NOW) == "—"


def test_a_stale_document_shows_nothing_rather_than_something_wrong():
    old = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)     # 10+ hours later
    assert screen_view.consensus_cell(_row(), _DOC, old) == "—"
    assert screen_view.doc_is_fresh(_DOC, old) is False


def test_a_document_within_the_window_is_fresh():
    assert screen_view.doc_is_fresh(_DOC, _NOW) is True


def test_an_unreadable_document_never_raises():
    assert screen_view.consensus_cell(_row(), {}, _NOW) == "—"
    assert screen_view.consensus_cell(_row(), None, _NOW) == "—"


def test_the_column_sits_next_to_the_reference_it_qualifies():
    cols = screen_view._COLUMNS
    assert cols[cols.index("Ref") + 1] == "Models"


def test_the_column_is_explained():
    tip = screen_view._TIPS["Models"]
    assert "spread" in tip.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_view_consensus.py -q`
Expected: FAIL — `AttributeError: module 'screen_view' has no attribute 'consensus_cell'`

- [ ] **Step 3: Write the implementation**

In `screen_view.py`, add `import city_consensus` beside the existing imports, then add after the `pushed_tickers` block:

```python
# ---- The model consensus, published by city_consensus.py -------------------

def consensus_doc_read() -> dict:
    """The published consensus document, or {} when it is not there yet."""
    return scan_log.read_doc(city_consensus.CONSENSUS_PATH)


@st.cache_data(ttl=300, show_spinner=False)
def consensus_doc() -> dict:
    return consensus_doc_read()


def doc_is_fresh(doc: dict, now=None) -> bool:
    """Whether the document is recent enough to show.

    The globals refresh every 6 hours and HRRR hourly, so a document older than
    STALE_AFTER_HOURS means the Action has been failing -- not that the models
    have stood still. Showing it anyway would be a number with no date on it."""
    stamp = (doc or {}).get("generated")
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - when) <= timedelta(hours=city_consensus.STALE_AFTER_HOURS)


def consensus_entry(row: dict, doc: dict):
    """This candidate's published block, or None when the document lacks it."""
    code = scan_cities.city_key(row.get("series") or "")
    day = screen_forecast.climate_day_of_ticker(row.get("ticker") or "")
    variable = row.get("variable")
    if code is None or day is None or variable not in ("high", "low"):
        return None
    city = ((doc or {}).get("cities") or {}).get(code) or {}
    return ((city.get("days") or {}).get(day.isoformat()) or {}).get(variable)


def consensus_cell(row: dict, doc: dict, now=None) -> str:
    """'61.0 ±0.8' — the folded consensus and how far the models spread.

    Folded, because Ref sits one cell away and IS folded; an unfolded number
    beside it would invite exactly the wrong comparison by mid-afternoon."""
    if not doc_is_fresh(doc, now):
        return "—"
    entry = consensus_entry(row, doc) or {}
    value, spread = entry.get("cons_folded"), entry.get("spread")
    if value is None:
        return "—"
    tail = "" if spread is None else f" ±{float(spread):.1f}"
    return f"{float(value):.1f}{tail}"
```

Add the tooltip to `_TIPS`:

```python
    "Models": "Consensus of five weather models (GFS, ECMWF, ICON, GEM, HRRR) "
              "for this bracket's climate day, and how far apart they are. "
              "Folded with temperature already realized, exactly like Ref, so "
              "the two are comparable. A tight spread beside a distant Ref "
              "means NWS is the outlier; a wide one means nobody knows. "
              "Equal-weighted and NOT calibrated per city — a second opinion, "
              "not a better forecast. '—' means too few models, or the feed is "
              "over six hours stale.",
```

Insert `"Models"` into `_COLUMNS` immediately after `"Ref"`, add the parameter to `_candidate_row`:

```python
def _candidate_row(r: dict, live: dict, fresh: set, zones: dict = None,
                   now=None, consensus: dict = None) -> dict:
```

with the cell `"Models": consensus_cell(r, consensus or {}, now),` placed directly after the `"Ref"` entry, and in `render()` fetch the document once and pass it:

```python
        zones = city_timezones()
        doc = consensus_doc()
```

then `_candidate_row(r, live, fresh, zones, consensus=doc)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_view_consensus.py tests/test_screen_view.py -q`
Expected: all pass. If `test_every_candidate_column_has_a_cell` fails, `_candidate_row` is missing the `Models` key.

- [ ] **Step 5: Commit**

```bash
git add screen_view.py tests/test_screen_view_consensus.py
git commit -m "$(cat <<'EOF'
feat(screen): show the model consensus beside Ref

'Ref 96.0 · Models 93.4 ±1.2' says NWS is three degrees above a tight cluster,
so the gap is measured from the outlier. A wide spread says nobody knows.

Folded like Ref, because it sits one cell away from it. Blank past six hours:
a consensus with no date on it is worse than none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: The 20-city board

**Files:**
- Modify: `screen_view.py`
- Test: `tests/test_screen_view_board.py`

**Interfaces:**
- Consumes: everything from Task 7.
- Produces:
  - `_BOARD_COLUMNS`, `_BOARD_TIPS`
  - `board_rows(doc, which, now=None) -> list[dict]`
  - `_render_board(doc) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_view_board.py
"""The 20-city board: a number for every city, flagged or not."""
from datetime import datetime, timezone

import screen_view

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)

_DOC = {
    "generated": "2026-08-07T19:45:00Z",
    "cities": {
        "DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
            "2026-08-07": {
                "high": {"nws_folded": 95.0, "cons_folded": 92.1,
                         "spread": 1.4, "n": 5},
                "low": {"nws_folded": 63.0, "cons_folded": 63.4,
                        "spread": 0.8, "n": 5}},
            "2026-08-08": {
                "high": {"nws_folded": 97.0, "cons_folded": 98.2,
                         "spread": 2.0, "n": 4},
                "low": {"nws_folded": 64.0, "cons_folded": None,
                        "spread": None, "n": 0}}}},
        "MIA": {"name": "Miami", "timezone": "America/New_York", "days": {
            "2026-08-07": {
                "high": {"nws_folded": 91.0, "cons_folded": 91.2,
                         "spread": 0.6, "n": 5},
                "low": {"nws_folded": 79.0, "cons_folded": 78.6,
                        "spread": 1.1, "n": 5}}}},
    },
}


def test_the_board_lists_every_city_alphabetically():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    assert [r["City"] for r in rows] == ["Denver", "Miami"]


def test_a_row_pairs_each_forecast_with_the_models_and_their_gap():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    denver = rows[0]
    assert denver["Hi NWS"] == "95.0"
    assert denver["Hi Models"] == "92.1 ±1.4"
    assert denver["Hi Δ"] == "−2.9"
    assert denver["Lo Δ"] == "+0.4"


def test_the_delta_uses_the_apps_true_minus_sign():
    # Every other negative on this page uses U+2212, not a hyphen.
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    assert "−" in rows[0]["Hi Δ"] and "-" not in rows[0]["Hi Δ"]


def test_tomorrow_is_a_different_day_not_a_different_table():
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    denver = [r for r in rows if r["City"] == "Denver"][0]
    assert denver["Hi Models"] == "98.2 ±2.0"


def test_a_city_without_that_day_is_omitted_rather_than_blank():
    # Miami has no Aug 8 block here; a row of dashes says nothing.
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    assert [r["City"] for r in rows] == ["Denver"]


def test_a_variable_with_no_consensus_dashes_only_its_own_cells():
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    denver = rows[0]
    assert denver["Lo Models"] == "—" and denver["Lo Δ"] == "—"
    assert denver["Hi Models"] == "98.2 ±2.0"       # the high still reports


def test_a_stale_document_yields_no_board():
    old = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    assert screen_view.board_rows(_DOC, "Today", old) == []


def test_an_empty_document_yields_no_board():
    assert screen_view.board_rows({}, "Today", _NOW) == []


def test_every_board_column_has_a_cell():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    for column in screen_view._BOARD_COLUMNS:
        assert column in rows[0]


def test_the_board_columns_are_explained():
    untipped = [c for c in screen_view._BOARD_COLUMNS
                if c not in screen_view._BOARD_TIPS]
    assert untipped == ["City"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_view_board.py -q`
Expected: FAIL — `AttributeError: module 'screen_view' has no attribute 'board_rows'`

- [ ] **Step 3: Write the implementation**

Add to `screen_view.py`:

```python
# The board's own columns and tips. A SEPARATE tip map from _TIPS for the same
# reason _TRADE_TIPS is separate: 'Hi NWS' here is a whole city's forecast,
# while 'Ref' on the candidate table is one bracket's reference.
_BOARD_COLUMNS = ["City", "Hi NWS", "Hi Models", "Hi Δ",
                  "Lo NWS", "Lo Models", "Lo Δ"]

_BOARD_TIPS = {
    "Hi NWS": "The NWS forecast high for this city's climate day, folded with "
              "any temperature already realized — the same number the Ref "
              "column measures a bracket's gap from.",
    "Hi Models": "Consensus of five models (GFS, ECMWF, ICON, GEM, HRRR) and "
                 "how far apart they are. Equal-weighted, not calibrated per "
                 "city. '—' means fewer than three models had data.",
    "Hi Δ": "Models minus NWS. A large value means the forecast every gap on "
            "this page is measured from is contested — treat that city's rows "
            "with more suspicion, in either direction.",
    "Lo NWS": "The NWS forecast low for this city's climate day, folded with "
              "any temperature already realized.",
    "Lo Models": "Consensus of five models for the low, and their spread. "
                 "'—' means fewer than three models had data.",
    "Lo Δ": "Models minus NWS for the low. See 'Hi Δ'.",
}


def _one_decimal(value) -> str:
    return "—" if value is None else f"{float(value):.1f}"


def _models_cell(entry: dict) -> str:
    """'92.1 ±1.4', or '—' when this variable had no consensus."""
    value, spread = (entry or {}).get("cons_folded"), (entry or {}).get("spread")
    if value is None:
        return "—"
    tail = "" if spread is None else f" ±{float(spread):.1f}"
    return f"{float(value):.1f}{tail}"


def _delta_cell(entry: dict) -> str:
    """Consensus minus NWS, in the app's true minus sign."""
    cons, nws = (entry or {}).get("cons_folded"), (entry or {}).get("nws_folded")
    if cons is None or nws is None:
        return "—"
    return f"{float(cons) - float(nws):+.1f}".replace("-", "−")


def board_rows(doc: dict, which: str, now=None) -> list:
    """One row per city for the chosen day, alphabetical by display name.

    A city with no block for that day is OMITTED rather than shown as a row of
    dashes: an all-dash row says only that this table does not know, which the
    table's absence says more briefly."""
    if not doc_is_fresh(doc, now):
        return []
    now = now or datetime.now(timezone.utc)
    out = []
    for code, city in ((doc or {}).get("cities") or {}).items():
        tzname = city.get("timezone")
        if not tzname:
            continue
        day = screen_forecast.in_progress_day(now, tzname)
        if which == "Tomorrow":
            day = day + timedelta(days=1)
        block = (city.get("days") or {}).get(day.isoformat())
        if not block:
            continue
        high, low = block.get("high") or {}, block.get("low") or {}
        out.append({
            "City": city.get("name") or code,
            "Hi NWS": _one_decimal(high.get("nws_folded")),
            "Hi Models": _models_cell(high),
            "Hi Δ": _delta_cell(high),
            "Lo NWS": _one_decimal(low.get("nws_folded")),
            "Lo Models": _models_cell(low),
            "Lo Δ": _delta_cell(low),
        })
    return sorted(out, key=lambda r: r["City"])


def _render_board(doc: dict) -> None:
    """The all-cities board, below the candidates and above the track record.

    Placed there because it answers 'what should I bet', while the track record
    and the trade table answer 'how did I do'."""
    st.markdown("#### Model Consensus — All Cities")
    which = st.segmented_control(
        "Day", ["Today", "Tomorrow"], default="Today", key="consensus_day",
        help="Which climate day to show. Only Today's rows can be flagged and "
             "alerted; Tomorrow is the advance look.")
    rows = board_rows(doc, which or "Today")
    if not rows:
        st.caption("No consensus published yet — it refreshes every 30 minutes, "
                   "and blanks after six hours without one.")
        return
    st.markdown(_table(_BOARD_COLUMNS, rows, _BOARD_TIPS),
                unsafe_allow_html=True)
    st.caption("Five models, equal-weighted, folded with temperature already "
               "realized. A second opinion on the forecast every gap above is "
               "measured from — not a calibrated forecast of its own.")
```

Call it in `render()`, immediately before `_render_track_record(all_rows)`:

```python
    _render_board(consensus_doc())
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Verify against real data**

Run the dashboard and screenshot the Screen page using the `verify` skill:

```bash
SCAN_GH_REPO=jaredmcelreath-a11y/Weather-Model SCAN_GH_BRANCH=scan-data \
FORECAST_LOG_GH_REPO=jaredmcelreath-a11y/Weather-Model \
  /Users/jared/Library/Python/3.9/bin/streamlit run app.py \
  --server.headless true --server.port 8599 --browser.gatherUsageStats false &
sleep 12
python3 .claude/skills/verify/cdp_shot.py \
  "http://localhost:8599/screen_page" /tmp/board.png "Model Consensus" 8
pkill -f "streamlit run app.py"
```

Confirm: the `Models` column sits beside `Ref` on the candidate rows, the board lists 20 cities, Δ signs point the right way against the NWS/Models pair on the same row, and the Tomorrow toggle changes the numbers.

- [ ] **Step 6: Commit**

```bash
git add screen_view.py tests/test_screen_view_board.py
git commit -m "$(cat <<'EOF'
feat(screen): add the 20-city model consensus board

A number for every city whether or not a bracket is flagged there, which is the
reason the board exists. Δ is consensus minus NWS, so a large value flags a city
whose forecast — the one every gap on the page is measured from — is contested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage.** Every section maps to a task: cost/one-request → 1; equal weight and the LST fold → 2; the `screen.py` realized publish → 3; architecture, document shape, folded/unfolded → 4; measurement → 5; the `scan.yml` step and dry-run script → 6; the `Models` column → 7; the board → 8. Failure modes are distributed: fetch failure and missing reference in 4, `MIN_MODELS` in 2, staleness in 7, per-city omission in 8.

**Deferred, as the spec states:** the scorer. `scan_settled.jsonl` already collects the truth, so nothing in this plan needs to change when it is written.

**Naming consistency checked:** `cons`/`cons_folded`/`nws`/`nws_folded`/`spread`/`n`/`models` are used identically in Tasks 4, 5, 7 and 8. `consensus()` returns `{"value","spread","n"}` and only Task 4 unpacks it. `city_key` is defined in Task 3 and consumed in Tasks 4 and 7. `doc_is_fresh` is defined in Task 7 and consumed in Task 8. `_table(columns, rows, tips)` matches the existing three-argument signature.

**One thing the implementer must watch:** the board's two Δ columns are named `Hi Δ` and `Lo Δ`, not both `Δ`. `_table` builds cells by column name, so two columns sharing a name would render the same value twice.
