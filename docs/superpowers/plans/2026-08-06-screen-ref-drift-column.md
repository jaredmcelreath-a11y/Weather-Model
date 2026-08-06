# Screen Ref-Drift Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a display-only `Drift` column to the Screen page showing where `Ref` lands if the NWS forecast's current error against the station persists.

**Architecture:** Three pure functions in `screen_forecast.py` (`forecast_at`, `observed_anchor`, `forecast_drift`) computed once per city per climate day inside `screen_pass`, stashed on each candidate row as `drift`/`drift_ref`, and rendered by `screen_view`. Zero extra HTTP requests — both the hourly forecast periods and the station observations are already fetched during a pass on an in-progress day.

**Tech Stack:** Python 3.9, pytest, Streamlit. No new dependencies.

Spec: `docs/superpowers/specs/2026-08-06-screen-ref-drift-column-design.md`

## Global Constraints

- **Display-only.** `Ref`, `Gap`, `Str` and the set of flagged brackets must not change. No edits to `screen_rules.py` screening logic.
- **Never raise.** Every new function returns `None` (or `(None, None)`) on bad input. One city must not cost the others.
- **No log migration.** Rows written before this feature lack `drift`/`drift_ref` and must render `—`.
- `ANCHOR_WINDOW_MIN = 30`, `MAX_ANCHOR_AGE_MIN = 70` — exact values, defined in `screen_forecast.py`.
- Drift is applied by **re-folding**, never by shifting `Ref`: `fold_realized(forecast_extreme + drift, realized, variable)`.
- Rounding for display is **half-up** (`math.floor(v + 0.5)`), matching the whole-°F settlement basis. Python's `f"{v:.0f}"` is half-even and must not be used here.
- Run the full suite with `python3 -m pytest -q` before each commit. Baseline is 1243 passing.

---

### Task 1: `forecast_at` — the forecast interpolated to an instant

**Files:**
- Modify: `screen_forecast.py` (add after `_day_periods`, around line 63)
- Test: `tests/test_screen_forecast.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `forecast_at(periods: list, when: datetime) -> float | None`. `periods` is the raw NWS hourly `periods` list; `when` is an aware datetime. Task 3 calls it.

- [ ] **Step 0: Branch**

```bash
git checkout main && git checkout -b screen-ref-drift
```

- [ ] **Step 1: Write the failing tests**

`tests/test_screen_forecast.py` already imports `datetime` and `timezone` but not
`timedelta` — extend its first line to
`from datetime import date, datetime, timedelta, timezone`.

Append to `tests/test_screen_forecast.py`:

```python
_MDT = timezone(timedelta(hours=-6))

_FC_PERIODS = [
    {"startTime": "2026-08-06T12:00:00-06:00", "temperature": 70},
    {"startTime": "2026-08-06T13:00:00-06:00", "temperature": 74},
]


def test_forecast_interpolates_between_the_bracketing_hours():
    # Snapping to the last whole hour makes this a step function that jumps at
    # the top of each hour while the observation anchor has not yet updated --
    # the sawtooth model.py:211 documents at KDFW.
    when = datetime(2026, 8, 6, 12, 30, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 72.0


def test_forecast_exactly_on_an_hour_is_that_hour():
    when = datetime(2026, 8, 6, 13, 0, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 74.0


def test_forecast_before_the_earliest_period_is_flat():
    # NWS hourly returns exactly one past hour, so a stale anchor at a slow
    # station can fall before the payload starts. Extrapolate flat rather than
    # abstain -- it is at most ~1 hour back.
    when = datetime(2026, 8, 6, 11, 15, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 70.0


def test_forecast_after_the_last_period_is_flat():
    when = datetime(2026, 8, 6, 15, 0, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 74.0


def test_forecast_with_no_usable_period_is_none():
    when = datetime(2026, 8, 6, 12, 30, tzinfo=_MDT)
    assert sf.forecast_at([], when) is None
    assert sf.forecast_at([{"startTime": "garbage", "temperature": 70}], when) is None
    assert sf.forecast_at(
        [{"startTime": "2026-08-06T12:00:00-06:00", "temperature": None}],
        when) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: FAIL with `AttributeError: module 'screen_forecast' has no attribute 'forecast_at'`

- [ ] **Step 3: Write the implementation**

Add to `screen_forecast.py` after `_day_periods`:

```python
def forecast_at(periods: list, when: datetime):
    """The hourly forecast temperature linearly interpolated to `when`.

    Interpolated rather than snapped to the last whole hour: snapping makes the
    value a step function that jumps at the top of each hour while the
    observation it is compared against has not yet updated, which is the
    sawtooth `model.py` documents for the KDFW anchor.

    Flat outside the payload's range. NWS hourly carries exactly one past hour
    (verified 2026-08-06 at FFC, MTR and OKX), so a stale anchor at a
    slow-reporting station can sit before the first period; extrapolating that
    last ~hour flat beats abstaining. None when no period is usable."""
    points = []
    for p in periods or []:
        temp = p.get("temperature")
        if temp is None:
            continue
        try:
            start = datetime.fromisoformat(str(p.get("startTime")))
        except (TypeError, ValueError):
            continue
        if start.utcoffset() is None:
            continue
        points.append((start, float(temp)))
    if not points:
        return None
    points.sort(key=lambda pair: pair[0])
    before = [pt for pt in points if pt[0] <= when]
    after = [pt for pt in points if pt[0] > when]
    if not before:
        return after[0][1]
    if not after:
        return before[-1][1]
    (t0, v0), (t1, v1) = before[-1], after[0]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return v0
    frac = (when - t0).total_seconds() / span
    return round(v0 + (v1 - v0) * frac, 2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
git add screen_forecast.py tests/test_screen_forecast.py
git commit -m "feat(screen): interpolate the hourly forecast to an instant"
```

---

### Task 2: `observed_anchor` — the station's current temperature

**Files:**
- Modify: `screen_forecast.py` (add after `forecast_at`)
- Test: `tests/test_screen_forecast.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `observed_anchor(readings: list, now: datetime) -> tuple`. `readings` is a list of `(aware datetime, temp_f float)` pairs. Returns `(temperature, timestamp)` or `(None, None)`. Also exports module constants `ANCHOR_WINDOW_MIN = 30` and `MAX_ANCHOR_AGE_MIN = 70`. Task 3 calls it; Task 4 supplies the pairs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_forecast.py`:

```python
_ANCHOR_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=_MDT)


def _reading(minutes_ago, temp):
    return (_ANCHOR_NOW - timedelta(minutes=minutes_ago), temp)


def test_the_anchor_averages_the_readings_in_the_window():
    # A single whole-degC reading jitters by up to 1.8F between samples, and the
    # drift shifts the whole remaining forecast 1:1 -- so a lone reading swings
    # the implied Ref while the temperature is flat.
    readings = [_reading(20, 68.0), _reading(10, 70.0), _reading(5, 72.0)]
    temp, at = sf.observed_anchor(readings, _ANCHOR_NOW)
    assert temp == 70.0
    # The timestamp is the mean of the contributing readings: 35/3 min ago.
    assert abs((_ANCHOR_NOW - at).total_seconds() - 700.0) < 1.0


def test_the_anchor_ignores_readings_outside_the_window():
    readings = [_reading(90, 50.0), _reading(10, 70.0)]
    temp, _ = sf.observed_anchor(readings, _ANCHOR_NOW)
    assert temp == 70.0


def test_a_slow_station_falls_back_to_its_newest_reading():
    # KDEN reports hourly and KNYC every ~31 min (measured 2026-08-06), so
    # nothing lands in the 30-minute window. Abstaining there would blank those
    # cities permanently rather than occasionally.
    temp, at = sf.observed_anchor([_reading(55, 66.0)], _ANCHOR_NOW)
    assert temp == 66.0
    assert (_ANCHOR_NOW - at).total_seconds() == 55 * 60


def test_an_anchor_past_the_age_cap_abstains():
    assert sf.observed_anchor([_reading(71, 66.0)], _ANCHOR_NOW) == (None, None)


def test_an_anchor_with_nothing_to_read_abstains():
    assert sf.observed_anchor([], _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor(None, _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor([(None, 70.0)], _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor([_reading(5, None)], _ANCHOR_NOW) == (None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: FAIL with `AttributeError: module 'screen_forecast' has no attribute 'observed_anchor'`

- [ ] **Step 3: Write the implementation**

Add to `screen_forecast.py` after `forecast_at`:

```python
# How far back the drift anchor averages, and how stale a reading may be before
# it says nothing about now. Station cadence is NOT uniform -- measured
# 2026-08-06: 5 min at KATL/KSFO/KMIA/KLAS, 31 min at KNYC, 60 min at KDEN, with
# newest readings up to 67 min old. A fixed "last N readings" anchor (the KDFW
# rule, written for a 5-minute feed) would silently span four hours at Denver.
ANCHOR_WINDOW_MIN = 30
MAX_ANCHOR_AGE_MIN = 70


def observed_anchor(readings: list, now: datetime) -> tuple:
    """(temperature, timestamp) for the station's current reading, or
    (None, None).

    The mean of the readings inside ANCHOR_WINDOW_MIN, paired with the mean of
    their own timestamps. Averaging rather than taking the newest reading damps
    the whole-degC jitter that would otherwise swing the implied reference while
    the temperature is flat.

    A station too slow to put anything in the window falls back to its single
    newest reading, provided it is inside MAX_ANCHOR_AGE_MIN. Pairing the value
    with the time it was actually taken is what lets the caller compare it
    against the forecast for THAT hour rather than for now."""
    usable = [(t, v) for t, v in (readings or [])
              if t is not None and v is not None and t <= now]
    if not usable:
        return (None, None)
    window = [(t, v) for t, v in usable
              if (now - t).total_seconds() <= ANCHOR_WINDOW_MIN * 60]
    if not window:
        newest = max(usable, key=lambda pair: pair[0])
        if (now - newest[0]).total_seconds() > MAX_ANCHOR_AGE_MIN * 60:
            return (None, None)
        window = [newest]
    temp = sum(float(v) for _, v in window) / len(window)
    base = min(t for t, _ in window)
    mean_offset = sum((t - base).total_seconds() for t, _ in window) / len(window)
    return (round(temp, 2), base + timedelta(seconds=mean_offset))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
git add screen_forecast.py tests/test_screen_forecast.py
git commit -m "feat(screen): anchor the station's current temperature for drift"
```

---

### Task 3: `forecast_drift` — the signed error

**Files:**
- Modify: `screen_forecast.py` (add after `observed_anchor`)
- Test: `tests/test_screen_forecast.py`

**Interfaces:**
- Consumes: `forecast_at(periods, when)` from Task 1; `observed_anchor(readings, now)` from Task 2.
- Produces: `forecast_drift(periods: list, readings: list, now: datetime) -> float | None`. Signed °F. Task 4 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_forecast.py`:

```python
_DRIFT_PERIODS = [
    {"startTime": "2026-08-06T11:00:00-06:00", "temperature": 68},
    {"startTime": "2026-08-06T12:00:00-06:00", "temperature": 71},
    {"startTime": "2026-08-06T13:00:00-06:00", "temperature": 73},
]


def test_a_station_warmer_than_the_forecast_drifts_positive():
    # Positive means the forecast is running COLD.
    readings = [_reading(10, 74.0), _reading(5, 74.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, readings, _ANCHOR_NOW) == 3.0


def test_a_forecast_running_hot_drifts_negative():
    # The San Francisco case of 2026-08-06: the grid said 71 for the hour while
    # KSFO read 68, and Str was quoting a 4F gap off the unadjusted number.
    readings = [_reading(10, 68.0), _reading(5, 68.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, readings, _ANCHOR_NOW) == -3.0


def test_drift_is_measured_against_the_anchors_own_hour():
    # A 55-minute-old reading compared against the forecast for NOW would
    # manufacture drift out of the diurnal ramp alone. At 11:05 the forecast is
    # 68.25, so a 68.25 reading is no drift at all -- against the 12:00 value of
    # 71 it would have read -2.75.
    assert sf.forecast_drift(_DRIFT_PERIODS, [_reading(55, 68.25)],
                             _ANCHOR_NOW) == 0.0


def test_drift_abstains_when_either_input_does():
    readings = [_reading(10, 68.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, [], _ANCHOR_NOW) is None
    assert sf.forecast_drift([], readings, _ANCHOR_NOW) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: FAIL with `AttributeError: module 'screen_forecast' has no attribute 'forecast_drift'`

- [ ] **Step 3: Write the implementation**

Add to `screen_forecast.py` after `observed_anchor`:

```python
def forecast_drift(periods: list, readings: list, now: datetime):
    """How wrong the hourly forecast currently is at this station, in F.

    Positive: the station is warmer than the forecast said, i.e. the forecast is
    running cold. Negative: it is running hot.

    Measured against the forecast interpolated to the ANCHOR'S OWN timestamp,
    not to `now`. Station cadence is not uniform, and comparing an hour-old
    Denver reading against the forecast for now would manufacture drift out of
    the diurnal ramp; against what the forecast said for that hour it is apples
    to apples, and staleness costs recency rather than correctness."""
    anchor, at = observed_anchor(readings, now)
    if anchor is None:
        return None
    expected = forecast_at(periods, at)
    if expected is None:
        return None
    return round(anchor - expected, 2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
git add screen_forecast.py tests/test_screen_forecast.py
git commit -m "feat(screen): measure the forecast's live error against the station"
```

---

### Task 4: Wire drift into the pass

**Files:**
- Modify: `screen.py:61-80` (rename `_observed_temps_f` → `_observed_readings`), `screen.py:118-156` (the day loop)
- Modify: `docs/superpowers/specs/2026-08-06-screen-ref-drift-column-design.md` (two corrections, see Step 6)
- Test: `tests/test_screen_pass.py`

**Interfaces:**
- Consumes: `screen_forecast.forecast_drift(periods, readings, now)` from Task 3; `screen_forecast.fold_realized(forecast_value, realized_temps, variable)` which already exists.
- Produces: candidate rows carrying `drift` (signed float or `None`) and `drift_ref` (float or `None`). Task 5 renders them. `_observed_readings(features, tzname, day) -> list[(datetime, float)]` replaces `_observed_temps_f`.

**Note — dead rows get no drift.** A `dead` row's `Ref` is the realized bound, a fact, not a forecast. Attaching a drift arrow whose left-hand number is not the `Ref` shown would be a visible inconsistency. Only `forecast` rows carry it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_pass.py`:

```python
# 11:00 and 12:00 and 15:00 MDT, i.e. 17:00Z / 18:00Z / 21:00Z. _NOW is 18:00Z.
_DRIFT_PERIODS = [
    {"startTime": "2026-08-03T11:00:00-06:00", "temperature": 90},
    {"startTime": "2026-08-03T12:00:00-06:00", "temperature": 92},
    {"startTime": "2026-08-03T15:00:00-06:00", "temperature": 94},
]

# Two readings of 30.0C = 86.0F at 10 and 5 minutes before _NOW.
_DRIFT_OBS = [
    {"properties": {"timestamp": "2026-08-03T17:50:00+00:00",
                    "temperature": {"value": 30.0}}},
    {"properties": {"timestamp": "2026-08-03T17:55:00+00:00",
                    "temperature": {"value": 30.0}}},
]


def _drift_deps(sink, markets, obs):
    d = _deps(sink, markets, obs=obs)
    d.fetch_forecast = lambda url: _DRIFT_PERIODS
    return d


def test_a_forecast_candidate_carries_the_drift_and_the_implied_reference():
    sink = []
    d = _drift_deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)],
                    _DRIFT_OBS)
    screen.screen_pass(_NOW, d)
    c = [r for r in sink if r["kind"] == "forecast"][0]
    # Anchor 86.0F at 17:52:30Z; forecast interpolated there is 91.75.
    assert c["drift"] == -5.75
    # Re-folded, NOT Ref + drift: fold(90 - 5.75, [86, 86], "low") = 84.25.
    assert c["drift_ref"] == 84.25
    assert c["forecast"] == 86.0          # Ref itself is untouched


def test_a_dead_candidate_carries_no_drift():
    # A dead row's Ref is the realized bound, a fact -- there is no forecast
    # drifting, and an arrow off a different number would just confuse.
    #
    # Denver's HIGH series is KXHIGHDEN, with no 'T' -- Kalshi's naming is
    # inconsistent and KXHIGHTDEN is not in scan_cities._SERIES_CITY. Using it
    # makes screen_pass skip the city as unmapped and the test passes vacuously.
    sink = []
    d = _drift_deps(sink, [_market("KXHIGHDEN-26AUG03-T72.5", 72, 73)],
                    _DRIFT_OBS)
    d.list_series = lambda: [{"ticker": "KXHIGHDEN", "title": "Denver high"}]
    screen.screen_pass(_NOW, d)
    # Realized max 86.0F could settle at 85-87, so a 72-73 high bracket is dead.
    dead = [r for r in sink if r["kind"] == "dead"]
    assert dead, "fixture must actually produce a dead row"
    for r in dead:
        assert r["drift"] is None and r["drift_ref"] is None


def test_a_day_not_yet_started_has_no_drift():
    sink = []
    # Aug 4 markets on an Aug 3 pass: no observations exist to compare against.
    d = _drift_deps(sink, [_market("KXLOWTDEN-26AUG04-B72.5", 72, 73)], [])
    d.fetch_forecast = lambda url: [
        {"startTime": "2026-08-04T02:00:00-06:00", "temperature": 66}]
    screen.screen_pass(_NOW, d)
    for r in sink:
        assert r["drift"] is None and r["drift_ref"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_pass.py -q`
Expected: FAIL with `KeyError: 'drift'`

- [ ] **Step 3: Replace `_observed_temps_f` with `_observed_readings`**

In `screen.py`, replace the whole function at lines 61-80:

```python
def _observed_readings(features, tzname, day):
    """[(timestamp, F)] for readings inside the LST climate day.

    The timestamp kept is the reading's OWN aware time, not the LST-shifted one
    used to pick the day: the drift anchor compares it against `now` and against
    forecast period times, which are both real instants."""
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
        utc_offset = stamp.utcoffset()
        if utc_offset is None:
            continue
        lst = stamp - utc_offset + offset
        if lst.date() == day:
            out.append((stamp, temp))
    return out
```

- [ ] **Step 4: Wire it into the day loop**

In `screen.py`, replace the block from `realized = []` through the `storm = ...` assignment (lines ~125-141) with:

```python
            readings = []
            if in_progress:
                try:
                    station = deps.station_for(resolved.get("stations_url"))
                    features = deps.fetch_obs(station, start, now) if station else []
                    readings = _observed_readings(features, tzname, day)
                except Exception as e:    # noqa: BLE001 - degrade to forecast
                    print(f"[screen] {series}: observations skipped ({e})")
            realized = [temp for _, temp in readings]

            extremes = screen_forecast.daily_extremes(periods, day, tzname)
            forecast = screen_forecast.fold_realized(
                extremes.get(variable), realized, variable)
            # Context for the human, not a screening input: a gap is only as
            # good as the forecast it is measured from, and convection is when
            # that forecast is least reliable. Free — the same payload.
            storm = screen_forecast.storm_chance(
                periods, day, tzname, variable, now)
            # Likewise free, and likewise never a screening input: how wrong the
            # forecast is against the station right now, applied to the forecast
            # HALF of the reference and re-folded. Shifting `forecast` directly
            # would move a realized extreme that has already happened.
            drift = screen_forecast.forecast_drift(periods, readings, now) \
                if in_progress else None
            drift_ref = None
            if drift is not None and extremes.get(variable) is not None:
                drift_ref = screen_forecast.fold_realized(
                    extremes[variable] + drift, realized, variable)
```

Then in the same loop, attach to forecast candidates only:

```python
            for r in day_rows:
                hit = screen_rules.forecast_candidate(r, forecast, now_iso)
                if hit:
                    hit["storm"] = storm
                    hit["drift"] = drift
                    hit["drift_ref"] = drift_ref
                    candidates.append(hit)
```

And in the dead loop below it, set both to `None` explicitly so every row has the keys:

```python
            for r in day_rows:
                hit = screen_rules.dead_candidate(r, bound, now_iso)
                if hit:
                    hit["storm"] = storm
                    # A dead row's Ref is the realized bound, not a forecast.
                    hit["drift"] = hit["drift_ref"] = None
                    candidates.append(hit)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_pass.py -q && python3 -m pytest -q`
Expected: PASS. The full suite must still be at 1243 + the new tests, with no pre-existing failures.

- [ ] **Step 6: Correct two details in the spec**

Two statements in the spec are wrong against the real code and must be fixed now that the code exists:

1. Under **Storage**, it says the fields are "written by `screen_rules._candidate`". They are not — `storm` is attached in `screen.py` and these follow the same pattern. Change to "attached in `screen.py`'s day loop, the same way `storm` already is".
2. Under **Abstain rules**, add a bullet: "the row is a `dead` candidate — its `Ref` is the realized bound, a fact, so there is no forecast drifting."

- [ ] **Step 7: Commit**

```bash
git add screen.py tests/test_screen_pass.py docs/superpowers/specs/2026-08-06-screen-ref-drift-column-design.md
git commit -m "feat(screen): compute the Ref drift on every in-progress pass"
```

---

### Task 5: The `Drift` column

**Files:**
- Modify: `screen_view.py` — imports (line ~11), `_TIPS` (line ~69), `_COLUMNS` (line 740), a new `drift_of` beside `storm_of` (line ~574), `_candidate_row` (line 752)
- Test: `tests/test_screen_view.py`

**Interfaces:**
- Consumes: candidate rows carrying `forecast`, `drift` and `drift_ref` from Task 4.
- Produces: `drift_of(row: dict) -> str`. Renders `"75→72"` or `"—"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_view.py`:

```python
def test_the_drift_cell_shows_the_reference_and_where_it_lands():
    assert sv.drift_of({"forecast": 75.0, "drift_ref": 72.4}) == "75→72"


def test_the_drift_cell_rounds_half_up_to_the_settlement_basis():
    # Kalshi settles on whole degrees F, so the arrow speaks in them. Python's
    # own "%.0f" is half-EVEN and would render 72.5 as 72.
    assert sv.drift_of({"forecast": 71.6, "drift_ref": 71.6}) == "72→72"
    assert sv.drift_of({"forecast": 72.5, "drift_ref": 72.5}) == "73→73"


def test_a_row_without_drift_reads_as_a_dash():
    # Rows logged before this feature existed, and dead rows, both land here.
    assert sv.drift_of({"forecast": 75.0}) == "—"
    assert sv.drift_of({"forecast": 75.0, "drift_ref": None}) == "—"
    assert sv.drift_of({}) == "—"


def test_the_drift_column_sits_immediately_before_the_reference():
    assert "Drift" in sv._COLUMNS
    assert sv._COLUMNS.index("Drift") == sv._COLUMNS.index("Ref") - 1


def test_every_candidate_column_has_a_cell():
    row = sv._candidate_row({"series": "KXLOWTDEN", "variable": "low",
                             "ticker": "T", "label": "72° to 73°",
                             "floor": 72, "cap": 73, "strike_type": "between",
                             "forecast": 75.0, "drift_ref": 72.4, "gap": 4.0,
                             "hours_to_close": 11.0, "price": 0.35},
                            {}, set())
    for column in sv._COLUMNS:
        assert column in row
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_view.py -q`
Expected: FAIL with `AttributeError: module 'screen_view' has no attribute 'drift_of'`

- [ ] **Step 3: Write the implementation**

Add `import math` to `screen_view.py`'s stdlib imports (beside `import html`).

Add after `storm_of`:

```python
def drift_of(row: dict) -> str:
    """The reference, and where it lands if the forecast's current error holds.

    Both sides are rounded half-up to whole degrees because that is the basis
    Kalshi settles on -- which is why a row can read '72→72' beside a Ref column
    showing 71.6. The unrounded number stays visible in that column.

    '—' covers three cases that all mean "nothing to say": a row logged before
    this field existed, a dead row (whose Ref is realized fact, not a forecast),
    and a live row with no usable observation to anchor against."""
    ref, implied = row.get("forecast"), row.get("drift_ref")
    if ref is None or implied is None:
        return "—"
    return f"{math.floor(float(ref) + 0.5)}→{math.floor(float(implied) + 0.5)}"
```

Add to `_TIPS`:

```python
    "Drift": "How the NWS forecast is verifying against the station right now, "
             "applied to Ref. '75→72' means the forecast is running 3°F hot at "
             "this hour, so if that error persists the real extreme is nearer "
             "72. A conditional, not a forecast — it assumes the current error "
             "holds until the extreme forms, which it may not. Gap and Str are "
             "NOT adjusted by it. '—' means no recent observation, or the "
             "extreme has already formed.",
```

Change `_COLUMNS` to:

```python
_COLUMNS = ["City", "Var", "Bracket", "Price", "NO Now", "Gap", "Str", "Storm",
            "Settled", "Drift", "Ref", "Hrs", "Side"]
```

Add to `_candidate_row`, between `"Settled"` and `"Ref"`:

```python
        "Drift": drift_of(r),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_view.py -q && python3 -m pytest -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
git add screen_view.py tests/test_screen_view.py
git commit -m "feat(screen): show the Ref drift column"
```

---

### Task 6: Verify against live payloads

**Files:**
- Create: `scripts/check_screen_drift.py` (throwaway verification, committed for reruns)

**Interfaces:**
- Consumes: `screen_forecast.forecast_drift` (Task 3), `screen_forecast.fold_realized`, `screen_forecast.daily_extremes`.
- Produces: nothing importable. A console report.

`docs/superpowers/specs/2026-08-03-mispriced-bracket-screen-design.md` records that unit tests passed against both of that feature's original defects and only a live pass caught them. Do not skip this task.

- [ ] **Step 1: Write the verification script**

Create `scripts/check_screen_drift.py`:

```python
"""Print the live Ref/drift for a few screened cities. Run by hand.

Unit tests passed against both of the screen's original defects; only a live
pass caught them. Usage: python3 scripts/check_screen_drift.py
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

import scan_cities            # noqa: E402
import screen_forecast        # noqa: E402
import screen_rules           # noqa: E402

CITIES = [("SFO", "KXHIGHTSFO", "high"), ("ATL", "KXLOWTATL", "low"),
          ("DEN", "KXHIGHTDEN", "high"), ("NYC", "KXLOWTNYC", "low")]


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "screen-drift-check"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    now = datetime.now(timezone.utc)
    for city, series, variable in CITIES:
        point = scan_cities.point_for(series)
        resolved = scan_cities.resolve(*point, fetch=lambda u: get(u))
        tzname = resolved["timezone"]
        periods = get(resolved["forecast_hourly"])["properties"]["periods"]
        day = (now + timedelta(hours=screen_forecast.lst_offset_hours(tzname))).date()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
            - timedelta(hours=screen_forecast.lst_offset_hours(tzname))
        station = scan_cities.station_for(resolved["stations_url"],
                                          fetch=lambda u: get(u))
        feats = get(f"https://api.weather.gov/stations/{station}/observations",
                    {"start": start.isoformat().replace("+00:00", "Z"),
                     "end": now.isoformat().replace("+00:00", "Z"),
                     "limit": 500})["features"]
        readings = []
        for f in feats:
            p = f["properties"]
            temp = screen_rules.c_to_f((p.get("temperature") or {}).get("value"))
            if temp is not None:
                readings.append((datetime.fromisoformat(p["timestamp"]), temp))
        realized = [t for _, t in readings]
        extreme = screen_forecast.daily_extremes(periods, day, tzname).get(variable)
        ref = screen_forecast.fold_realized(extreme, realized, variable)
        drift = screen_forecast.forecast_drift(periods, readings, now)
        implied = None if drift is None or extreme is None else \
            screen_forecast.fold_realized(extreme + drift, realized, variable)
        print(f"{city:5} {variable:4} station={station:5} n_obs={len(readings):3} "
              f"ref={ref} drift={drift} implied={implied}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python3 scripts/check_screen_drift.py`

Expected: four lines. Confirm each of these by eye:

- `SFO` and `ATL` print a numeric `drift` (5-minute stations, always fresh).
- `drift` is plausible — single digits. A drift over ~8°F means the anchor and the forecast are being compared at different times; re-check `forecast_at` is receiving the anchor timestamp, not `now`.
- For any city whose extreme has already formed, `implied == ref`. That is the re-fold working.
- `NYC` and `DEN` may print `drift=None`. That is correct behaviour, not a bug — their stations report every ~31 and ~60 minutes.

- [ ] **Step 3: Cross-check one city by hand**

For whichever city printed a drift, fetch its forecast hour and its latest observations and confirm the sign: if the station is cooler than the forecast said, `drift` must be negative and `implied` must be below `ref` for a high.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_screen_drift.py
git commit -m "chore(screen): add a live drift verification script"
```

---

### Task 7: Merge

- [ ] **Step 1: Run the full suite one final time**

Run: `python3 -m pytest -q`
Expected: PASS. 1243 baseline plus roughly 25 new tests.

- [ ] **Step 2: Merge to main**

```bash
git checkout main
git merge --no-ff screen-ref-drift -m "merge: show how the forecast is verifying against the station"
git branch -d screen-ref-drift
```

- [ ] **Step 3: Confirm before pushing**

Do NOT push without asking. This repo is public (see the repo-public-for-actions note) and the Screen page is live.
