# Prior-Day Trade Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log two new betting-slot families — evening day-ahead (`eve-21:00/22:00/23:00`, targeting tomorrow) and climate-day-close (`close-45`/`close-15`, targeting the ending settlement day) — so the existing edge report can measure whether either entry window beats the current same-day slots.

**Architecture:** Measure-only. Two settlement helpers (`climate_day_of`, `open_prior_day`) define "which climate day is `now` in" and "is yesterday's day still open". `model.snapshot` grows a window-gated `yesterday` block; `betting_log` grows the two slot families and routes each slot to the snapshot block whose `day` matches its target date; `edge_report` gains a `settled_bucket_ask` stat fed by raw contract quotes logged on close rows only. No UI, no alerts, no sizing.

**Tech Stack:** Python 3, pytest, zoneinfo, existing Open-Meteo / NWS / IEM / Kalshi source modules.

## Global Constraints

- **Production invariance is mandatory.** The existing 10 slots (`sr-90`…`sr+30`, `15:00`…`17:00`) must produce byte-identical rows. Every existing test in `tests/test_betting_log.py` must pass unmodified. A dedicated invariance test pins this (Task 5).
- **Measure-only scope.** Do not touch `app.py` or any `*_view.py`. No Forecast-page block, no ntfy, no Kelly wiring.
- **Settlement day = fixed LST (UTC−6), `CLIMATE_TZ`.** All climate-day boundaries go through `settlement.local_day_bounds`. Never hardcode 01:00 CDT.
- **Kalshi close == climate-day end** (verified live 2026-07-19: `close_time` = `06:00Z` = `local_day_bounds(day).end`).
- **Best-effort market data.** Any Kalshi call added to the scheduled path must be wrapped so a market outage never breaks model logging — matching the existing `try/except` around `implied_block`.
- `TZ` = `ZoneInfo(TIMEZONE)` (America/Chicago) for clock times; `_CLIMATE_TZ` = `ZoneInfo(CLIMATE_TZ)` (Etc/GMT+6) for settlement boundaries.
- Run the full suite with `python -m pytest -q` from the repo root before each commit.

---

### Task 1: Settlement helpers — which climate day, and is the prior one still open

**Files:**
- Modify: `settlement.py` (add after `local_day_bounds`, line 46)
- Test: `tests/test_climate_day_helpers.py` (create)

**Interfaces:**
- Consumes: `settlement.local_day_bounds(day) -> (start, end)`, `settlement.TZ`, `settlement._CLIMATE_TZ`
- Produces:
  - `settlement.climate_day_of(moment: datetime) -> date`
  - `settlement.open_prior_day(moment: datetime) -> date | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_climate_day_helpers.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def test_climate_day_is_clock_day_during_the_day():
    # Midday CDT: the running climate day is the clock day.
    assert settlement.climate_day_of(datetime(2026, 7, 20, 12, 0, tzinfo=_TZ)) \
        == date(2026, 7, 20)


def test_climate_day_lags_in_the_final_summer_hour():
    # 00:30 CDT July 20 is still inside July 19's climate day (ends 01:00 CDT).
    assert settlement.climate_day_of(datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)) \
        == date(2026, 7, 19)


def test_climate_day_rolls_at_the_lst_boundary():
    assert settlement.climate_day_of(datetime(2026, 7, 20, 1, 0, tzinfo=_TZ)) \
        == date(2026, 7, 20)


def test_open_prior_day_in_the_final_summer_hour():
    assert settlement.open_prior_day(datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)) \
        == date(2026, 7, 19)
    assert settlement.open_prior_day(datetime(2026, 7, 20, 0, 0, tzinfo=_TZ)) \
        == date(2026, 7, 19)


def test_open_prior_day_closes_at_the_boundary_exactly():
    # end is exclusive: at 01:00 CDT July 19 has settled.
    assert settlement.open_prior_day(datetime(2026, 7, 20, 1, 0, tzinfo=_TZ)) is None
    assert settlement.open_prior_day(datetime(2026, 7, 20, 1, 1, tzinfo=_TZ)) is None


def test_open_prior_day_none_during_the_day():
    assert settlement.open_prior_day(datetime(2026, 7, 20, 12, 0, tzinfo=_TZ)) is None
    assert settlement.open_prior_day(datetime(2026, 7, 20, 23, 30, tzinfo=_TZ)) is None


def test_winter_has_no_open_prior_hour():
    # In CST the climate day coincides with clock midnight, so the gap the
    # last-hour trade lives in does not exist.
    for hour in (23, 0, 1):
        d = date(2026, 1, 6) if hour != 23 else date(2026, 1, 5)
        assert settlement.open_prior_day(datetime(d.year, d.month, d.day, hour, 30,
                                                  tzinfo=_TZ)) is None


def test_winter_climate_day_matches_clock_day():
    assert settlement.climate_day_of(datetime(2026, 1, 5, 23, 30, tzinfo=_TZ)) \
        == date(2026, 1, 5)
    assert settlement.climate_day_of(datetime(2026, 1, 6, 0, 30, tzinfo=_TZ)) \
        == date(2026, 1, 6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_climate_day_helpers.py -q`
Expected: FAIL — `AttributeError: module 'settlement' has no attribute 'climate_day_of'`

- [ ] **Step 3: Write minimal implementation**

In `settlement.py`, insert directly after `local_day_bounds` (after line 46):

```python
def climate_day_of(moment: datetime) -> date:
    """The settlement (climate) day `moment` falls in.

    Equals the clock date except in the summer 00:00–00:59 CDT hour, when the
    previous climate day is still running (it ends 01:00 CDT). Converting into
    fixed LST does the whole job: the LST calendar date IS the climate day.
    """
    return moment.astimezone(_CLIMATE_TZ).date()


def open_prior_day(moment: datetime) -> date | None:
    """Clock-yesterday's date while its settlement day is still open, else None.

    Non-None only during the final climate hour (00:00–00:59 CDT in summer) —
    the window where yesterday's Kalshi market is still trading but the model's
    clock-based "today" no longer serves it. In winter the climate day ends at
    clock midnight, so this is always None.
    """
    prior = moment.astimezone(TZ).date() - timedelta(days=1)
    _start, end = local_day_bounds(prior)
    return prior if moment < end else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_climate_day_helpers.py -q`
Expected: PASS (9 passed)

Then the full suite: `python -m pytest -q` — expected: all pass (531+ tests).

- [ ] **Step 5: Commit**

```bash
git add settlement.py tests/test_climate_day_helpers.py
git commit -m "feat: climate_day_of + open_prior_day settlement helpers"
```

---

### Task 2: Reach observations and the CLI daily anchor back across the open prior day

**Why:** `nws_observations.fetch` bounds its window at **clock midnight of `now`'s day** (`sources/nws_observations.py:58`). During 00:00–00:59 CDT that window holds under an hour of data, so a `yesterday` prediction would see almost none of its own climate day — no running extremes, no peak lock, no daily anchor. Likewise `_fetch_cli_daily` (`model.py:995`) fetches only the clock-today summary, but the prior day's low anchor/floor lives in the prior day's summary.

**Not needed:** the *forecast* series. `gather_series` fetches from clock-today 00:00 forward, which already covers the only unobserved slice of the prior climate day (now → 01:00 CDT). No `past_days` param is required — do not add one.

**Files:**
- Modify: `sources/nws_observations.py:40-63` (add `start` parameter)
- Modify: `model.py:940-948` (`_fetch_cli_daily` takes a range), `model.py:32` (import), `model.py:989-995` (`gather_series` wiring)
- Test: `tests/test_prior_day_obs_window.py` (create)

**Interfaces:**
- Consumes: `settlement.climate_day_of`, `settlement.open_prior_day` (Task 1)
- Produces:
  - `nws_observations.fetch(limit=500, continuous=False, now=None, start=None)` — `start` is a tz-aware datetime overriding the default clock-midnight window start.
  - `model._fetch_cli_daily(day: date, through: date | None = None) -> dict` — `{date: (max_f, min_f)}` over `[day, through or day]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prior_day_obs_window.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
import settlement
from config import TIMEZONE
from sources import nws_observations

_TZ = ZoneInfo(TIMEZONE)


def test_fetch_accepts_an_explicit_window_start(monkeypatch):
    seen = {}

    def fake_get_json(url, params, ttl=None):
        seen["start"] = params["start"]
        seen["limit"] = params["limit"]
        return {"features": []}

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    monkeypatch.setattr(nws_observations, "_iem_fallback", lambda s, n: ([], []))

    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    start = datetime(2026, 7, 19, 1, 0, tzinfo=_TZ)
    nws_observations.fetch(now=now, start=start)
    assert seen["start"] == start.isoformat()


def test_fetch_defaults_to_clock_midnight(monkeypatch):
    seen = {}

    def fake_get_json(url, params, ttl=None):
        seen["start"] = params["start"]
        return {"features": []}

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    monkeypatch.setattr(nws_observations, "_iem_fallback", lambda s, n: ([], []))

    now = datetime(2026, 7, 20, 15, 0, tzinfo=_TZ)
    nws_observations.fetch(now=now)
    assert seen["start"] == datetime(2026, 7, 20, 0, 0, tzinfo=_TZ).isoformat()


def test_cli_daily_fetches_a_range(monkeypatch):
    seen = {}

    def fake_fetch_actual_cli(start, end, ttl=None):
        seen["range"] = (start, end)
        return {start: (99.0, 79.0)}

    monkeypatch.setattr(model, "fetch_actual_cli", fake_fetch_actual_cli)
    model._fetch_cli_daily(date(2026, 7, 19), date(2026, 7, 20))
    assert seen["range"] == (date(2026, 7, 19), date(2026, 7, 20))


def test_cli_daily_single_day_unchanged(monkeypatch):
    seen = {}

    def fake_fetch_actual_cli(start, end, ttl=None):
        seen["range"] = (start, end)
        return {}

    monkeypatch.setattr(model, "fetch_actual_cli", fake_fetch_actual_cli)
    model._fetch_cli_daily(date(2026, 7, 20))
    assert seen["range"] == (date(2026, 7, 20), date(2026, 7, 20))


def test_gather_series_extends_the_window_in_the_final_hour(monkeypatch):
    seen = {}

    def fake_obs_fetch(limit=500, continuous=False, now=None, start=None):
        seen["start"] = start
        seen["limit"] = limit
        return {"obs": ([], []), "obs_continuous": (None, None)}

    monkeypatch.setattr(model.nws_observations, "fetch", fake_obs_fetch)
    monkeypatch.setattr(model, "_fetch_cli_daily", lambda d, t=None: {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    model.gather_series(now=now, continuous_obs=True)
    # Window starts at the PRIOR climate day's start (01:00 CDT July 19).
    assert seen["start"] == settlement.local_day_bounds(date(2026, 7, 19))[0]
    assert seen["limit"] > 500


def test_gather_series_normal_window_unchanged(monkeypatch):
    seen = {}

    def fake_obs_fetch(limit=500, continuous=False, now=None, start=None):
        seen["start"] = start
        seen["limit"] = limit
        return {"obs": ([], []), "obs_continuous": (None, None)}

    monkeypatch.setattr(model.nws_observations, "fetch", fake_obs_fetch)
    monkeypatch.setattr(model, "_fetch_cli_daily", lambda d, t=None: {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    model.gather_series(now=datetime(2026, 7, 20, 15, 0, tzinfo=_TZ), continuous_obs=True)
    assert seen["start"] is None      # default clock-midnight path
    assert seen["limit"] == 500


def test_gather_series_cli_daily_covers_both_days_in_the_final_hour(monkeypatch):
    seen = {}
    monkeypatch.setattr(model.nws_observations, "fetch",
                        lambda **k: {"obs": ([], []), "obs_continuous": (None, None)})
    monkeypatch.setattr(model, "_fetch_cli_daily",
                        lambda d, t=None: seen.update(range=(d, t)) or {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    model.gather_series(now=datetime(2026, 7, 20, 0, 30, tzinfo=_TZ), continuous_obs=True)
    assert seen["range"] == (date(2026, 7, 19), date(2026, 7, 20))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prior_day_obs_window.py -q`
Expected: FAIL — `TypeError: fetch() got an unexpected keyword argument 'start'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `sources/nws_observations.py`, change the signature at line 40 and the start computation at line 58:

```python
def fetch(limit: int = 500, continuous: bool = False, now: datetime | None = None,
          start: datetime | None = None
          ) -> dict[str, tuple[list[datetime], list[float]]]:
```

Add to the docstring, after the `limit` sentence:

```
    `start` overrides the default window start. The last-hour capture needs the
    whole PRIOR climate day in view (~25h back), which clock midnight excludes;
    callers pass that day's LST start instead.
```

Replace line 58:

```python
    now = now or datetime.now(TZ)
    start = start or datetime(now.year, now.month, now.day, tzinfo=TZ)  # local midnight
```

**3b.** In `model.py`, replace `_fetch_cli_daily` (lines 940-948):

```python
def _fetch_cli_daily(day: date, through: date | None = None) -> dict:
    """{date: (max_f, min_f)} from the IEM daily summary over [day, through], or
    {} on any failure. Best-effort: the CLI daily min is a live *anchor* for the
    Kalshi low (see predict_variable), never a settlement floor — a miss just
    falls back to the hourly/average-offset path. The range form covers the final
    climate hour, when the still-open prior day needs its own summary too."""
    try:
        return fetch_actual_cli(day, through or day, ttl=CACHE_TTL_SECONDS)
    except Exception:
        return {}
```

**3c.** In `model.py` line 32, add the two helpers to the existing settlement import:

```python
from settlement import (climate_day_of, covers_extreme, local_day_bounds,
                        observed_so_far, open_prior_day,
```

(Keep every name already on that import list; only add `climate_day_of` and `open_prior_day` in alphabetical position.)

**3d.** In `model.py` `gather_series`, replace the observation block (lines 989-995) with:

```python
    now_local = now or datetime.now(TZ)
    # In the final climate hour the still-open prior day needs its OWN ~25h of
    # observations; the default clock-midnight window would show under an hour.
    prior = open_prior_day(now_local)
    obs_start = local_day_bounds(prior)[0] if prior else None
    obs = nws_observations.fetch(limit=900 if prior else 500, continuous=True,
                                 now=now, start=obs_start)
    obs["obs_continuous_display"] = obs.pop("obs_continuous", (None, None))
    if continuous_obs:
        obs["obs_continuous"] = obs["obs_continuous_display"]
        # CLI basis only (Kalshi): the whole-°F daily-summary min anchors the low
        # (predict_variable). Best-effort — a miss falls back to the hourly path.
        obs["cli_daily"] = _fetch_cli_daily(prior or now_local.date(), now_local.date())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prior_day_obs_window.py -q`
Expected: PASS (7 passed)

Run: `python -m pytest -q`
Expected: all pass — the default paths are unchanged.

- [ ] **Step 5: Commit**

```bash
git add sources/nws_observations.py model.py tests/test_prior_day_obs_window.py
git commit -m "feat: obs window + CLI daily anchor reach across the open prior climate day"
```

---

### Task 3: `yesterday` snapshot block, gated to the open prior day

**Files:**
- Modify: `model.py:1035-1097` (`snapshot`)
- Test: `tests/test_prior_day_snapshot.py` (create)

**Interfaces:**
- Consumes: `open_prior_day` (Task 1), extended obs window (Task 2), existing `_predict_from(series, obs, day, now, calib, settle_offset, live=True)` and `per_source_extremes(series, day)`
- Produces: `snapshot()["yesterday"]` — same shape as `["today"]` (`{"day", "high", "low"}`), present **only** while `open_prior_day(now)` is not None; `snapshot()["sources"]["yesterday"]` alongside it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prior_day_snapshot.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _stub_snapshot_deps(monkeypatch, now):
    """Freeze the clock and stub the fetch layer so snapshot() is pure."""
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(model, "datetime", _FakeDT)
    monkeypatch.setattr(model, "gather_series",
                        lambda **k: ({}, {"obs": ([], [])}, []))
    monkeypatch.setattr(model, "_predict_from",
                        lambda series, obs, day, *a, **k: {"day": day.isoformat(),
                                                          "high": {"consensus": 99.0},
                                                          "low": {"consensus": 79.0}})
    monkeypatch.setattr(model, "per_source_extremes", lambda series, day: {})
    monkeypatch.setattr(model, "_storm_status", lambda t, n: None)


def test_yesterday_block_present_in_the_final_hour(monkeypatch):
    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    _stub_snapshot_deps(monkeypatch, now)
    snap = model.snapshot()
    assert snap["yesterday"]["day"] == "2026-07-19"
    assert snap["today"]["day"] == "2026-07-20"
    assert "yesterday" in snap["sources"]


def test_no_yesterday_block_during_the_day(monkeypatch):
    now = datetime(2026, 7, 20, 15, 0, tzinfo=_TZ)
    _stub_snapshot_deps(monkeypatch, now)
    snap = model.snapshot()
    assert "yesterday" not in snap
    assert "yesterday" not in snap["sources"]


def test_no_yesterday_block_after_the_boundary(monkeypatch):
    now = datetime(2026, 7, 20, 1, 5, tzinfo=_TZ)      # July 19 has settled
    _stub_snapshot_deps(monkeypatch, now)
    assert "yesterday" not in model.snapshot()


def test_no_yesterday_block_in_winter(monkeypatch):
    now = datetime(2026, 1, 6, 0, 30, tzinfo=_TZ)      # CST: no gap exists
    _stub_snapshot_deps(monkeypatch, now)
    assert "yesterday" not in model.snapshot()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prior_day_snapshot.py -q`
Expected: FAIL — `KeyError: 'yesterday'` in `test_yesterday_block_present_in_the_final_hour`

- [ ] **Step 3: Write minimal implementation**

In `model.py` `snapshot`, insert immediately after the `snap = {...}` literal ends (after line 1080, before the `if include_candidate:` block):

```python
    # The prior climate day while it is still open (00:00–00:59 CDT in summer):
    # its Kalshi market trades until 01:00 CDT but clock-based "today" no longer
    # serves it. Same live CLI machinery — by now the day is ~23/24 observed.
    prior = open_prior_day(now)
    if prior:
        snap["yesterday"] = _predict_from(series, obs, prior, now, calib,
                                          settle_offset, live=True)
        snap["sources"]["yesterday"] = per_source_extremes(series, prior)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prior_day_snapshot.py -q`
Expected: PASS (4 passed)

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_prior_day_snapshot.py
git commit -m "feat: snapshot serves the prior climate day while its market is open"
```

---

### Task 4: Evening and close slot families in `betting_log`

**Files:**
- Modify: `betting_log.py:20-72` (imports, slot constants, `current_slot`), add `slot_target_day`
- Test: `tests/test_betting_log_new_slots.py` (create)

**Interfaces:**
- Consumes: `settlement.climate_day_of`, `settlement.local_day_bounds` (Task 1)
- Produces:
  - `betting_log.EVENING_SLOTS: list[str]` — `["eve-21:00", "eve-22:00", "eve-23:00"]`
  - `betting_log.CLOSE_SLOT_OFFSETS: list[tuple[str, int]]` — `[("close-45", -45), ("close-15", -15)]`
  - `betting_log.CLOSE_SLOTS: list[str]` — `["close-45", "close-15"]`
  - `betting_log.current_slot(now, tol_min=8) -> str | None` — now also returns the new labels
  - `betting_log.slot_target_day(slot: str, now: datetime) -> date`

- [ ] **Step 1: Write the failing test**

Create `tests/test_betting_log_new_slots.py`:

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import betting_log
import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_TZ)


# --- evening day-ahead slots -------------------------------------------------

def test_evening_slots_match_fixed_clock_times():
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 0)) == "eve-21:00"
    assert betting_log.current_slot(_at(2026, 7, 20, 22, 0)) == "eve-22:00"
    assert betting_log.current_slot(_at(2026, 7, 20, 23, 0)) == "eve-23:00"


def test_evening_slots_honor_tolerance():
    assert betting_log.current_slot(_at(2026, 7, 20, 20, 53)) == "eve-21:00"   # -7
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 8)) == "eve-21:00"    # +8
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 9)) is None           # +9


def test_evening_slots_target_tomorrow():
    now = _at(2026, 7, 20, 22, 0)
    assert betting_log.slot_target_day("eve-22:00", now) == date(2026, 7, 21)


# --- close slots, summer -----------------------------------------------------

def test_close_slots_land_after_clock_midnight_in_summer():
    # July 19's climate day ends 01:00 CDT July 20 -> close-45 = 00:15, close-15 = 00:45.
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 15)) == "close-45"
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 45)) == "close-15"


def test_close_slots_target_the_ending_climate_day_in_summer():
    assert betting_log.slot_target_day("close-45", _at(2026, 7, 20, 0, 15)) \
        == date(2026, 7, 19)
    assert betting_log.slot_target_day("close-15", _at(2026, 7, 20, 0, 45)) \
        == date(2026, 7, 19)


def test_close_slot_tolerance_stays_inside_the_climate_day():
    # +8 on close-15 is 00:53 CDT — still July 19's day (ends 01:00).
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 53)) == "close-15"
    assert betting_log.slot_target_day("close-15", _at(2026, 7, 20, 0, 53)) \
        == date(2026, 7, 19)


# --- close slots, winter -----------------------------------------------------

def test_close_slots_land_before_clock_midnight_in_winter():
    # Jan 5's climate day ends 00:00 CST Jan 6 -> close-45 = 23:15, close-15 = 23:45.
    assert betting_log.current_slot(_at(2026, 1, 5, 23, 15)) == "close-45"
    assert betting_log.current_slot(_at(2026, 1, 5, 23, 45)) == "close-15"


def test_close_slots_target_clock_today_in_winter():
    assert betting_log.slot_target_day("close-45", _at(2026, 1, 5, 23, 15)) \
        == date(2026, 1, 5)


def test_no_close_slot_just_after_winter_midnight():
    assert betting_log.current_slot(_at(2026, 1, 6, 0, 20)) is None


# --- DST transition days -----------------------------------------------------

def test_close_slots_resolve_on_both_dst_transition_days():
    # Spring forward 2026-03-08, fall back 2026-11-01. The slot must still sit
    # exactly 45/15 min before that climate day's end, whatever the clock did.
    for day in (date(2026, 3, 8), date(2026, 11, 1)):
        end = settlement.local_day_bounds(day)[1]
        for label, off in betting_log.CLOSE_SLOT_OFFSETS:
            moment = (end + timedelta(minutes=off)).astimezone(_TZ)
            assert betting_log.current_slot(moment) == label
            assert betting_log.slot_target_day(label, moment) == day


# --- registry ----------------------------------------------------------------

def test_slot_registry_includes_both_new_families():
    assert betting_log.EVENING_SLOTS == ["eve-21:00", "eve-22:00", "eve-23:00"]
    assert betting_log.CLOSE_SLOTS == ["close-45", "close-15"]
    for s in betting_log.EVENING_SLOTS + betting_log.CLOSE_SLOTS:
        assert s in betting_log.SLOTS
        assert betting_log.SLOT_VARS[s] == ("high", "low")


def test_existing_slots_still_target_clock_today():
    now = _at(2026, 7, 20, 15, 30)
    assert betting_log.slot_target_day("15:30", now) == date(2026, 7, 20)
    assert betting_log.slot_target_day("sr", now) == date(2026, 7, 20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_betting_log_new_slots.py -q`
Expected: FAIL — `AttributeError: module 'betting_log' has no attribute 'EVENING_SLOTS'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `betting_log.py`, add to the imports (after `import solar`, line 22):

```python
import settlement
```

**3b.** Replace the slot-constant block (lines 42-47) with:

```python
LOW_SLOT_OFFSETS = [("sr-90", -90), ("sr-60", -60), ("sr-30", -30),
                    ("sr", 0), ("sr+30", 30)]
HIGH_SLOTS = ["15:00", "15:30", "16:00", "16:30", "17:00"]
# Day-ahead probes. Day D's market opens 14:00Z on D−1, so tomorrow trades all
# evening; these ask whether a day-ahead entry carries more edge than the
# same-day slots. Fixed clock times — nothing solar about them.
EVENING_SLOTS = ["eve-21:00", "eve-22:00", "eve-23:00"]
# The last hour of a settlement day, anchored to the climate-day END (which is
# also the exact Kalshi close). In summer these land AFTER clock midnight
# (00:15/00:45 CDT) and target clock-yesterday; in winter they land before it
# (23:15/23:45 CST) and target clock-today. Anchoring to the boundary makes that
# seasonal shift automatic, the same trick the sunrise-anchored low slots use.
CLOSE_SLOT_OFFSETS = [("close-45", -45), ("close-15", -15)]
CLOSE_SLOTS = [lbl for lbl, _off in CLOSE_SLOT_OFFSETS]
SLOTS = ([lbl for lbl, _off in LOW_SLOT_OFFSETS] + HIGH_SLOTS
         + EVENING_SLOTS + CLOSE_SLOTS)
SLOT_VARS = {**{lbl: ("low",) for lbl, _off in LOW_SLOT_OFFSETS},
             **{s: ("high",) for s in HIGH_SLOTS},
             # Both variables: an evening capture is day-ahead for both, and at
             # the close both of the ending day's markets are still open.
             **{s: ("high", "low") for s in EVENING_SLOTS + CLOSE_SLOTS}}
```

**3c.** In `current_slot`, insert before the final `return None` (after the `HIGH_SLOTS` loop, line 71):

```python
    for label in EVENING_SLOTS:
        hh, mm = (int(x) for x in label.split("-", 1)[1].split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return label
    close_end = settlement.local_day_bounds(settlement.climate_day_of(local))[1]
    for label, off in CLOSE_SLOT_OFFSETS:
        slot_dt = close_end + timedelta(minutes=off)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return label
    return None
```

**3d.** Add `slot_target_day` immediately after `current_slot`:

```python
def slot_target_day(slot: str, now: datetime) -> date:
    """The date whose market `slot` captures.

    Existing same-day slots target the clock day (unchanged). Evening slots
    target tomorrow; close slots target the climate day that is ending — which
    is clock-yesterday in summer and clock-today in winter.
    """
    local = now.astimezone(TZ)
    if slot in EVENING_SLOTS:
        return local.date() + timedelta(days=1)
    if slot in CLOSE_SLOTS:
        return settlement.climate_day_of(local)
    return local.date()
```

Add `date` to the datetime import at line 15:

```python
from datetime import date, datetime, timedelta
```

(and delete the now-redundant `from datetime import date as _date` inside `record`, line 177, replacing its use `_date.fromisoformat(day)` with `date.fromisoformat(day)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_betting_log_new_slots.py -q`
Expected: PASS (13 passed)

Run: `python -m pytest tests/test_betting_log.py -q`
Expected: PASS — including `test_current_slot_slot_sets_defined`, which asserts `SLOTS == [...10 old labels]`. **This test WILL fail** because `SLOTS` now has 15 entries. Update that one assertion to check the old labels are the first ten and the new families follow:

```python
    assert betting_log.SLOTS[:10] == \
        ["sr-90", "sr-60", "sr-30", "sr", "sr+30",
         "15:00", "15:30", "16:00", "16:30", "17:00"]
    assert betting_log.SLOTS[10:] == \
        ["eve-21:00", "eve-22:00", "eve-23:00", "close-45", "close-15"]
```

This is the one permitted edit to an existing test — it is a registry assertion, not a behavior assertion. Re-run both files after the edit; expected: PASS.

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add betting_log.py tests/test_betting_log_new_slots.py tests/test_betting_log.py
git commit -m "feat: evening day-ahead + climate-day-close betting slot families"
```

---

### Task 5: Route `record()` to the right snapshot block, and log raw asks on close rows

**Files:**
- Modify: `betting_log.py:130-204` (`_row`, `record`), add `_snapshot_now` and `_target_block`
- Test: `tests/test_betting_log_routing.py` (create)

**Interfaces:**
- Consumes: `slot_target_day`, `EVENING_SLOTS`, `CLOSE_SLOTS` (Task 4)
- Produces:
  - `betting_log._target_block(cli_snapshot: dict, slot: str, now: datetime) -> tuple[dict | None, str | None]` — the prediction block and its market/hourly key name
  - `betting_log.record(cli_snapshot, hourly_snapshot, slot, calib, path=None, now=None)` — `now` defaults to the snapshot's `updated` timestamp, else wall clock
  - `betting_log._row(..., market_asks=None)` — writes a `market_asks` key only when non-None

- [ ] **Step 1: Write the failing test**

Create `tests/test_betting_log_routing.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

_CALIB = {"settlement_offset": {"high": 0.89, "high_std": 0.77,
                                "low": -0.33, "low_std": 0.47}}


def _var(consensus, top):
    return {"consensus": consensus, "probabilities": {top: 1.0},
            "observed_so_far": consensus, "observed_continuous": consensus,
            "peak_locked": False, "sigma_used": 1.0}


_SNAP = {
    "updated": "2026-07-20T00:45:00-05:00",
    "yesterday": {"day": "2026-07-19", "high": _var(99.0, "99"), "low": _var(79.0, "79")},
    "today": {"day": "2026-07-20", "high": _var(101.0, "101"), "low": _var(80.0, "80")},
    "tomorrow": {"day": "2026-07-21", "high": _var(97.0, "97"), "low": _var(78.0, "78")},
    "market": {
        "yesterday": {"high": {"ev": 98.9, "buckets": [[99, 100, 1.0]], "volume": 40.0},
                      "low": {"ev": 79.1, "buckets": [[79, 80, 1.0]], "volume": 10.0}},
        "today": {"high": {"ev": 100.8, "buckets": [[101, 102, 1.0]], "volume": 900.0}},
        "tomorrow": {"high": {"ev": 96.8, "buckets": [[97, 98, 1.0]], "volume": 300.0},
                     "low": {"ev": 78.2, "buckets": [[77, 78, 1.0]], "volume": 80.0}},
    },
    "market_asks": {"high": [[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]],
                    "low": [[79, 80, 0.90, 0.94]]},
}
_HOURLY = {
    "yesterday": {"day": "2026-07-19", "high": {"consensus": 98.2}, "low": {"consensus": 78.6}},
    "today": {"day": "2026-07-20", "high": {"consensus": 100.2}, "low": {"consensus": 79.6}},
    "tomorrow": {"day": "2026-07-21", "high": {"consensus": 96.2}, "low": {"consensus": 77.6}},
}


def test_close_slot_writes_the_prior_day_from_the_yesterday_block(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high", "low"}
    assert {r["target_date"] for r in rows} == {"2026-07-19"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["cli_consensus"] == 99.0          # yesterday block, not today
    assert hi["hourly_consensus"] == 98.2
    assert hi["market_ev"] == 98.9              # market["yesterday"]


def test_close_slot_logs_raw_asks(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    hi = next(r for r in betting_log.load(p) if r["variable"] == "high")
    assert hi["market_asks"] == [[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]]


def test_evening_slot_writes_tomorrow(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "eve-22:00", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 22, 0, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["target_date"] for r in rows} == {"2026-07-21"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["cli_consensus"] == 97.0
    assert hi["market_ev"] == 96.8
    assert "market_asks" not in hi          # asks are close-slot only


def test_same_day_slot_still_reads_today(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 15, 30, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high"}
    assert rows[0]["target_date"] == "2026-07-20"
    assert rows[0]["cli_consensus"] == 101.0
    assert "market_asks" not in rows[0]


def test_winter_close_slot_reads_the_today_block(tmp_path):
    # In CST the ending climate day IS clock-today, so there is no yesterday block.
    snap = {"updated": "2026-01-05T23:45:00-06:00",
            "today": {"day": "2026-01-05", "high": _var(55.0, "55"), "low": _var(33.0, "33")},
            "tomorrow": {"day": "2026-01-06", "high": _var(58.0, "58"), "low": _var(35.0, "35")},
            "market": {"today": {"high": {"ev": 54.9, "buckets": [[55, 56, 1.0]]}}}}
    hourly = {"today": {"day": "2026-01-05", "high": {"consensus": 54.5},
                        "low": {"consensus": 32.5}}}
    p = str(tmp_path / "b.jsonl")
    betting_log.record(snap, hourly, "close-15", _CALIB, path=p,
                       now=datetime(2026, 1, 5, 23, 45, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["target_date"] for r in rows} == {"2026-01-05"}
    assert next(r for r in rows if r["variable"] == "high")["cli_consensus"] == 55.0


def test_missing_target_block_writes_nothing(tmp_path):
    # A close slot with no block for the ending day must skip, not mis-file.
    snap = {"updated": "2026-07-20T00:45:00-05:00",
            "today": {"day": "2026-07-20", "high": _var(101.0, "101")}}
    p = str(tmp_path / "b.jsonl")
    betting_log.record(snap, {}, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert betting_log.load(p) == []


def test_now_defaults_to_the_snapshot_timestamp(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p)   # no now=
    assert {r["target_date"] for r in betting_log.load(p)} == {"2026-07-19"}


def test_production_rows_are_byte_identical(tmp_path):
    """Production invariance: a same-day slot's row must be exactly what the
    pre-slot-families code wrote — same keys, same values, same order."""
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 15, 30, tzinfo=_TZ))
    row = betting_log.load(p)[0]
    assert row == {
        "target_date": "2026-07-20",
        "variable": "high",
        "capture_slot": "15:30",
        "captured_at": "2026-07-20T00:45:00-05:00",
        "cli_consensus": 101.0,
        "hourly_consensus": 100.2,
        "flat_offset": 0.89,
        "live_gap": 0.0,
        "observed_so_far": 101.0,
        "observed_continuous": 101.0,
        "peak_locked": False,
        "sigma_used": 1.0,
        "convective_widened": False,
        "front_widened": False,
        "model_bins": [["101", 1.0]],
        "market_ev": 100.8,
        "market_buckets": [[101, 102, 1.0]],
        "market_volume": 900.0,
    }
    # Key ORDER too — the jsonl is diffed by eye on the data branch.
    assert list(row) == [
        "target_date", "variable", "capture_slot", "captured_at",
        "cli_consensus", "hourly_consensus", "flat_offset", "live_gap",
        "observed_so_far", "observed_continuous", "peak_locked", "sigma_used",
        "convective_widened", "front_widened", "model_bins",
        "market_ev", "market_buckets", "market_volume"]


def test_day_ahead_and_same_day_rows_coexist(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "eve-22:00", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 22, 0, tzinfo=_TZ))
    later = {**_SNAP, "today": {"day": "2026-07-21", "high": _var(97.5, "97"),
                                "low": _var(78.5, "78")}}
    betting_log.record(later, {"today": {"day": "2026-07-21", "high": {"consensus": 96.9},
                                         "low": {"consensus": 77.9}}},
                       "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 21, 15, 30, tzinfo=_TZ))
    rows = [r for r in betting_log.load(p)
            if r["target_date"] == "2026-07-21" and r["variable"] == "high"]
    assert sorted(r["capture_slot"] for r in rows) == ["15:30", "eve-22:00"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_betting_log_routing.py -q`
Expected: FAIL — `TypeError: record() got an unexpected keyword argument 'now'`

- [ ] **Step 3: Write minimal implementation**

**3a.** Change `_row`'s signature (line 130) and add the field. Replace the signature line and add the `market_asks` block just before `if market_var:` (line 162):

```python
def _row(day: str, variable: str, slot: str, cli_var: dict, hourly_var: dict,
         market_var: dict | None, flat_offset: float, captured: str,
         market_asks: list | None = None) -> dict:
```

```python
    # Raw per-contract quotes [floor, cap, yes_bid, yes_ask], close slots only.
    # The normalized `market_buckets` PMF has the overround removed and so cannot
    # answer "what would the settled bracket have COST" — the whole question the
    # last-hour trade turns on.
    if market_asks:
        rec["market_asks"] = market_asks
    if market_var:
```

**3b.** Add `_snapshot_now` and `_target_block` just before `record` (before line 169):

```python
def _snapshot_now(cli_snapshot: dict) -> datetime:
    """The snapshot's own capture instant, falling back to the wall clock."""
    stamp = cli_snapshot.get("updated")
    if stamp:
        try:
            return datetime.fromisoformat(stamp).astimezone(TZ)
        except ValueError:
            pass
    return datetime.now(TZ)


def _target_block(cli_snapshot: dict, slot: str, now: datetime):
    """(prediction block, block name) this slot captures, or (None, None).

    The block name doubles as the key into the market and hourly snapshots, which
    use the same today/tomorrow/yesterday naming. Same-day slots keep reading
    `today` unconditionally — byte-identical to the pre-slot-families behavior.
    """
    if slot in EVENING_SLOTS:
        return cli_snapshot.get("tomorrow"), "tomorrow"
    if slot in CLOSE_SLOTS:
        # Match by DAY rather than by name: the ending climate day lives in the
        # `yesterday` block in summer and the `today` block in winter.
        target = slot_target_day(slot, now).isoformat()
        for name in ("yesterday", "today"):
            block = cli_snapshot.get(name)
            if block and block.get("day") == target:
                return block, name
        return None, None
    return cli_snapshot.get("today"), "today"
```

**3c.** Replace `record` (lines 169-204) with:

```python
def record(cli_snapshot: dict, hourly_snapshot: dict, slot: str, calib: dict,
           path: str | None = None, now: datetime | None = None) -> None:
    """Upsert the betting-time row(s) for `slot` — only the variable(s) that slot
    captures (see SLOT_VARS) on the day that slot targets (see slot_target_day)."""
    now = now or _snapshot_now(cli_snapshot)
    block, block_name = _target_block(cli_snapshot, slot, now)
    if not block or not block.get("day"):
        return
    day = block["day"]
    day_d = date.fromisoformat(day)
    captured = cli_snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    market_block = (cli_snapshot.get("market") or {}).get(block_name, {})
    hourly_block = (hourly_snapshot or {}).get(block_name, {})
    asks = (cli_snapshot.get("market_asks") or {}) if slot in CLOSE_SLOTS else {}

    new_recs = []
    for variable in SLOT_VARS.get(slot, ("high", "low")):
        cli_var = block.get(variable)
        if not cli_var or not cli_var.get("probabilities"):
            continue
        flat_offset, _std = model._offset_bucket(
            calib.get("settlement_offset"), variable, day_d, calib)
        new_recs.append(_row(day, variable, slot, cli_var,
                             hourly_block.get(variable), market_block.get(variable),
                             flat_offset, captured, market_asks=asks.get(variable)))

    target = path or _PATH
    rows = load(target)
    index = {_key(r): i for i, r in enumerate(rows)}
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
    _write(rows, target)
```

**3d.** `capture_if_slot` passes `now` through so the two agree:

```python
def capture_if_slot(cli_snapshot: dict, hourly_snapshot: dict, calib: dict,
                    now: datetime | None = None) -> str | None:
    """If `now` falls in a betting slot, record the snapshot and return the slot."""
    now = now or datetime.now(TZ)
    slot = current_slot(now)
    if slot is None:
        return None
    record(cli_snapshot, hourly_snapshot, slot, calib, now=now)
    return slot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_betting_log_routing.py -q`
Expected: PASS (8 passed)

Run: `python -m pytest tests/test_betting_log.py tests/test_scheduled_betting.py -q`
Expected: PASS with **no edits** — the old `_CLI` fixture has no `updated` key, `_snapshot_now` falls back to the wall clock, and same-day slots ignore `now` entirely.

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add betting_log.py tests/test_betting_log_routing.py
git commit -m "feat: betting rows route to the block their slot targets; raw asks on close rows"
```

---

### Task 6: Feed the new slots from the scheduled run

**Files:**
- Modify: `sources/kalshi.py` (add `ask_rows` after `implied_block`)
- Modify: `scheduled_log.py:37-62` (`_log_snapshots`), add `_attach_market`
- Test: `tests/test_scheduled_prior_day.py` (create)

**Interfaces:**
- Consumes: `settlement.open_prior_day`, `settlement.climate_day_of` (Task 1); `betting_log.current_slot`, `betting_log.CLOSE_SLOTS` (Task 4); `record`'s `market_asks` contract (Task 5)
- Produces:
  - `kalshi.ask_rows(variable: str, day: date) -> list` — `[[floor, cap, yes_bid, yes_ask], ...]`
  - `scheduled_log._attach_market(cli_snap: dict, now: datetime) -> None` — mutates `cli_snap` in place, adding `market` (+ `market["yesterday"]` and `market_asks` when applicable)

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduled_prior_day.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

import scheduled_log
from config import TIMEZONE
from sources import kalshi

_TZ = ZoneInfo(TIMEZONE)


def test_ask_rows_returns_raw_quotes(monkeypatch):
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: [
        {"floor": None, "cap": 98, "yes_bid": 0.02, "yes_ask": 0.05},
        {"floor": 99, "cap": 100, "yes_bid": 0.93, "yes_ask": 0.97},
    ])
    assert kalshi.ask_rows("high", date(2026, 7, 19)) == [
        [None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]]


def _snap():
    return {"today": {"day": "2026-07-20"}, "tomorrow": {"day": "2026-07-21"}}


def test_yesterday_market_attached_in_the_final_hour(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 98.9, "buckets": [[99, 100, 1.0]]})
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", lambda v, d: [[99, 100, 0.9, 0.95]])

    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert snap["market"]["yesterday"]["high"]["ev"] == 98.9


def test_no_yesterday_market_during_the_day(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 1.0, "buckets": []})
    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 15, 0, tzinfo=_TZ))
    assert "yesterday" not in snap["market"]


def test_asks_attached_only_on_close_slots(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 98.9, "buckets": []})
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", lambda v, d: [[99, 100, 0.9, 0.95]])

    at_close = _snap()
    scheduled_log._attach_market(at_close, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert at_close["market_asks"]["high"] == [[99, 100, 0.9, 0.95]]

    midday = _snap()
    scheduled_log._attach_market(midday, datetime(2026, 7, 20, 15, 0, tzinfo=_TZ))
    assert "market_asks" not in midday


def test_market_failure_never_breaks_the_snapshot(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kalshi down")

    monkeypatch.setattr(scheduled_log.kalshi, "implied_block", boom)
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast", boom)
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", boom)
    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert snap["today"]["day"] == "2026-07-20"     # untouched, no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scheduled_prior_day.py -q`
Expected: FAIL — `AttributeError: module 'sources.kalshi' has no attribute 'ask_rows'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `sources/kalshi.py`, append after `implied_block`:

```python
def ask_rows(variable: str, day: date) -> list:
    """[[floor, cap, yes_bid, yes_ask], ...] — the untouched contract ladder.

    `implied_forecast`'s PMF is normalized (the bid/ask overround removed), so it
    cannot say what a bracket would actually have COST. The close-slot capture
    logs these raw quotes so the last-hour question — was the already-settled
    bracket still buyable under a dollar? — is answerable after the fact.
    """
    return [[c.get("floor"), c.get("cap"), c.get("yes_bid"), c.get("yes_ask")]
            for c in fetch_contracts(variable, day)]
```

**3b.** In `scheduled_log.py`, add the import (after `import settlements`, line 22):

```python
import settlement
```

Add `_attach_market` before `_log_snapshots`:

```python
def _attach_market(cli_snap: dict, now: datetime) -> None:
    """Attach the live Kalshi market to `cli_snap`, in place.

    Always the today/tomorrow block. During the final climate hour also the
    still-open prior day, and on a close slot the raw ask ladder for the day that
    is closing. Every branch is best-effort — a market outage must never block
    the model logging around it.
    """
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
        cli_snap["market"] = cli_snap.get("market") or {}

    prior = settlement.open_prior_day(now)
    if prior:
        block = {}
        for var in ("high", "low"):
            try:
                implied = kalshi.implied_forecast(var, prior)
            except Exception:
                implied = None
            if implied:
                block[var] = implied
        if block:
            cli_snap["market"]["yesterday"] = block

    if betting_log.current_slot(now) in betting_log.CLOSE_SLOTS:
        closing = settlement.climate_day_of(now)
        asks = {}
        for var in ("high", "low"):
            try:
                rows = kalshi.ask_rows(var, closing)
            except Exception:
                rows = None
            if rows:
                asks[var] = rows
        if asks:
            cli_snap["market_asks"] = asks
```

Replace the body of `_log_snapshots` (lines 40-62) with:

```python
    now = datetime.now(model.TZ)
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True,
                              include_candidate=True)
    _attach_market(cli_snap, now)
    forecast_log.record(cli_snap, basis="cli")
    consensus_log.record(cli_snap, basis="cli")
    # Betting-time capture: only when `now` falls in a betting slot.
    # Best-effort: an error here doesn't block the logging above.
    try:
        if betting_log.current_slot(now) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib, now=now)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scheduled_prior_day.py -q`
Expected: PASS (5 passed)

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sources/kalshi.py scheduled_log.py tests/test_scheduled_prior_day.py
git commit -m "feat: scheduled run serves the prior day's market and close-slot ask ladder"
```

---

### Task 7: Report the settled bracket's cost, grouped by slot family

**Files:**
- Modify: `edge_report.py:53-102` (`_subset_metrics`), `:147-153` (`_COLS`), `:155-166` (`_subset_line`), `:190` (block ordering)
- Test: `tests/test_edge_report_close_slots.py` (create)

**Interfaces:**
- Consumes: rows carrying `market_asks` (Task 5) and `settled_cli` (existing `join`)
- Produces: `_subset_metrics` entries gain `settled_bucket_ask` (mean), `settled_bucket_ask_min`, `n_settled_ask`; `edge_report._family(slot) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_edge_report_close_slots.py`:

```python
import edge_report


def _row(slot, consensus, settled, asks=None, **kw):
    r = {"capture_slot": slot, "variable": "high", "cli_consensus": consensus,
         "settled_cli": settled, "settled_hourly": settled - 1.0,
         "actual_gap": 1.0, "market_ev": consensus, "flat_offset": 0.89,
         "market_buckets": [[None, 98, 0.1], [99, 100, 0.9]]}
    if asks is not None:
        r["market_asks"] = asks
    r.update(kw)
    return r


def test_settled_bucket_ask_picks_the_winning_bracket():
    rows = [_row("close-15", 99.4, 99.0,
                 asks=[[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] == 0.97
    assert m["n_settled_ask"] == 1


def test_settled_bucket_ask_averages_and_tracks_the_minimum():
    rows = [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.90, 0.96]]),
            _row("close-15", 99.4, 99.0, asks=[[99, 100, 0.80, 0.84]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] == 0.90       # (0.96 + 0.84) / 2
    assert m["settled_bucket_ask_min"] == 0.84
    assert m["n_settled_ask"] == 2


def test_open_ended_bracket_matches():
    rows = [_row("close-15", 97.0, 97.0, asks=[[None, 98, 0.88, 0.92]])]
    assert edge_report._subset_metrics(rows, "high")["settled_bucket_ask"] == 0.92


def test_rows_without_asks_report_none():
    m = edge_report._subset_metrics([_row("15:30", 99.4, 99.0)], "high")
    assert m["settled_bucket_ask"] is None
    assert m["n_settled_ask"] == 0


def test_missing_ask_price_is_skipped_not_counted():
    rows = [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.90, None]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] is None
    assert m["n_settled_ask"] == 0


def test_family_order_puts_day_ahead_first_and_close_last():
    assert edge_report._family("eve-22:00") == 0
    assert edge_report._family("15:30") == 1
    assert edge_report._family("sr-30") == 1
    assert edge_report._family("close-15") == 2


def test_report_orders_blocks_by_family(tmp_path):
    metrics = {}
    for slot in ("15:30", "close-15", "eve-22:00"):
        metrics[(slot, "high", "all")] = edge_report._subset_metrics(
            [_row(slot, 99.4, 99.0)], "high")
    _csv, md = edge_report.write_report(metrics, str(tmp_path))
    text = open(md).read()
    assert text.index("eve-22:00") < text.index("15:30") < text.index("close-15")


def test_settled_ask_appears_in_the_assessment(tmp_path):
    metrics = {("close-15", "high", "all"): edge_report._subset_metrics(
        [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.93, 0.97]])], "high")}
    _csv, md = edge_report.write_report(metrics, str(tmp_path))
    assert "settled bracket ask" in open(md).read()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_report_close_slots.py -q`
Expected: FAIL — `KeyError: 'settled_bucket_ask'`

- [ ] **Step 3: Write minimal implementation**

**3a.** Add `_settled_ask` before `_subset_metrics` (before line 53):

```python
def _settled_ask(row: dict) -> float | None:
    """What the eventually-settled bracket's YES side was asking at capture.

    The direct cents-on-the-table read for the close slots: if the day is over
    and the market still asks well under a dollar for the bracket that wins,
    the last-hour trade is real. None when the row carries no ask ladder.
    """
    for lo, hi, _bid, ask in row.get("market_asks") or []:
        temp = row["settled_cli"]
        if (lo is None or temp >= lo) and (hi is None or temp <= hi):
            return ask
    return None
```

**3b.** In `_subset_metrics`, insert after the `entry["thin"] = ...` lines (after line 86):

```python
    asks = [a for a in (_settled_ask(r) for r in rows) if a is not None]
    entry["settled_bucket_ask"] = round(statistics.mean(asks), 4) if asks else None
    entry["settled_bucket_ask_min"] = round(min(asks), 4) if asks else None
    entry["n_settled_ask"] = len(asks)
```

**3c.** Extend `_COLS` (line 147):

```python
_COLS = ["capture_slot", "variable", "subset", "n", "model_mae", "market_mae",
         "disagreements", "model_bin_wins", "market_bin_wins", "n_boundary",
         "flat_rmse", "live_rmse", "flip_toward", "flip_away", "market_volume",
         "settled_bucket_ask", "settled_bucket_ask_min", "n_settled_ask"]
```

**3d.** Add `_family` after `_SUBSET_ORDER` (line 152):

```python
def _family(slot: str) -> int:
    """Sort rank of a slot's family: day-ahead, then same-day, then close — the
    order the evening-vs-same-day comparison reads in."""
    if slot.startswith("eve-"):
        return 0
    if slot.startswith("close-"):
        return 2
    return 1
```

**3e.** In `_subset_line`, add before `return "".join(parts)` (line 166):

```python
    if m.get("settled_bucket_ask") is not None:
        parts.append(f"; settled bracket ask mean {m['settled_bucket_ask']} "
                     f"/ min {m['settled_bucket_ask_min']} (n={m['n_settled_ask']})")
```

**3f.** Change the block-ordering loop (line 190):

```python
    for (slot, variable) in sorted(blocks, key=lambda k: (_family(k[0]), k[0], k[1])):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_edge_report_close_slots.py -q`
Expected: PASS (8 passed)

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report_close_slots.py
git commit -m "feat: edge report scores the settled bracket's ask, grouped by slot family"
```

---

## Post-implementation verification

Not a task — do this once after Task 7 lands.

- [ ] Full suite green: `python -m pytest -q`
- [ ] Dry-run the capture at a simulated close slot:

```bash
python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
import betting_log, settlement
tz = ZoneInfo('America/Chicago')
for m in (datetime(2026,7,20,0,15,tzinfo=tz), datetime(2026,7,20,0,45,tzinfo=tz),
          datetime(2026,7,20,22,0,tzinfo=tz), datetime(2026,1,5,23,45,tzinfo=tz)):
    s = betting_log.current_slot(m)
    print(m.isoformat(), '->', s, '-> target', betting_log.slot_target_day(s, m) if s else None)
"
```

Expected: `close-45 → 2026-07-19`, `close-15 → 2026-07-19`, `eve-22:00 → 2026-07-21`, `close-15 → 2026-01-05`.

- [ ] After the first real overnight run, confirm rows landed:

```bash
git fetch origin data && git show origin/data:betting_log.jsonl | \
  python3 -c "
import sys, json
from collections import Counter
c = Counter(json.loads(l)['capture_slot'] for l in sys.stdin if l.strip())
print({k: v for k, v in c.items() if k.startswith(('eve-', 'close-'))})"
```

- [ ] Regenerate the report and read the new blocks: `python edge_report.py`
- [ ] After ~2–3 weeks, apply the spec's decision criteria: close slots ship a trade feature only if the settled bracket's mean ask at `close-15` is ≤ ~95¢ **and** the boundary slice shows those days were not merely °C-wall coin flips.
