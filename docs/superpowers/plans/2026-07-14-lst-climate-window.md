# LST Climate-Day Settlement Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the model's settlement-day window from clock-midnight (America/Chicago) to the NWS climate day (fixed Local Standard Time, UTC−6), so obs slicing, locks, the hard bound, backtest extremes, and the front-guard scan all align with what Kalshi actually settles on.

**Architecture:** `local_day_bounds` builds its `[start, end)` window in a fixed `Etc/GMT+6` zone instead of `America/Chicago`; because every window comparison is instant-based (tz-aware datetimes compare by absolute time) the change flows through untouched. Hour-of-day/diurnal checks intentionally stay on Central time. Two explicit code touch-points: the window zone, and the front-guard scan (which must extend past clock midnight to the settlement day's true final hour). Spec: `docs/superpowers/specs/2026-07-14-lst-climate-window-design.md`; verification: `docs/benchmarks/2026-07-14/climate-day/FINDINGS.md`.

**Tech Stack:** Pure Python stdlib (`zoneinfo`), pytest via repo venv.

## Global Constraints

- Python 3.9 venv: run everything with `.venv/bin/python` from the repo root (`/Users/jared/Desktop/Weather Model`); no new dependencies (`Etc/GMT+6` ships with `zoneinfo`/tzdata).
- Branch: `lst-climate-window` (already created; spec committed). **Before Task 1, ensure `settlement.py` is at its committed state** — an exploratory LST patch may be uncommitted in the working tree; `git checkout settlement.py` first if `git status` shows it modified.
- Settlement zone: exactly `Etc/GMT+6` (fixed UTC−6 = DFW Local Standard Time year-round). `TIMEZONE` (`America/Chicago`) stays the wall-clock/diurnal zone for all hour-of-day logic and display — do NOT change any `t.hour` check or `TIMEZONE` usage.
- Winter byte-identity is the safety net: in CST, `America/Chicago` IS UTC−6, so `local_day_bounds` on a winter date returns the same absolute window as before. Every re-based test pins to a January date for exactly this reason; the LST-specific behavior is covered by dedicated new tests, never by weakening an existing one.
- "Today" stays clock-based (`now.date()`); do NOT touch `snapshot()`'s today/tomorrow or `lead_bucket`.
- Comment style: prose comments explaining why, matching each file's existing density.

---

### Task 1: LST window + re-base the affected logic-tests

**Files:**
- Modify: `config.py` (add `CLIMATE_TZ`)
- Modify: `settlement.py` (import `CLIMATE_TZ`; add `_CLIMATE_TZ`; `local_day_bounds` builds in it)
- Test: `tests/test_settlement.py` (re-base `DAY`; add 3 new tests), `tests/test_warm_low_bias.py` (re-base dates), `tests/test_conditional_offset.py` (re-base dates)

**Interfaces:**
- Consumes: existing `local_day_bounds(day) -> (datetime, datetime)`, `day_high_low`, `config.TIMEZONE`.
- Produces: `config.CLIMATE_TZ = "Etc/GMT+6"`; `settlement._CLIMATE_TZ` (a `ZoneInfo`). `local_day_bounds` keeps its signature; its window is now LST-based. Task 2 relies on `local_day_bounds` returning LST-tz-aware bounds.

- [ ] **Step 1: Write the failing/boundary tests**

Add to `tests/test_settlement.py` (after the existing `import`s, `TZ`, `DAY`):

```python
LST = ZoneInfo("Etc/GMT+6")


def test_local_day_bounds_is_lst_not_clock():
    # Summer: the settlement window starts at 00:00 LST = 01:00 CDT, one hour
    # after clock midnight — this is the CLIDFW climate day (verified May 2026).
    summer = date(2026, 7, 14)
    start, end = S.local_day_bounds(summer)
    assert start == datetime(2026, 7, 14, tzinfo=LST)
    assert start.astimezone(TZ).hour == 1          # 01:00 CDT, not 00:00
    assert (end - start) == timedelta(days=1)       # always exactly 24h (no DST)


def test_local_day_bounds_winter_matches_clock():
    # Winter: LST == CST == the old America/Chicago clock window, byte-identical.
    winter = date(2026, 1, 14)
    start, end = S.local_day_bounds(winter)
    assert start == datetime(2026, 1, 14, tzinfo=TZ)   # same absolute instant


def test_post_clock_midnight_reading_settles_prior_day():
    # The May 26 2026 pattern: a min recorded 00:30 CDT the NEXT clock day still
    # belongs to THIS settlement day (window ends 01:00 CDT next day). The old
    # clock window dropped it; the LST window keeps it.
    summer = date(2026, 7, 14)
    start, _ = S.local_day_bounds(summer)
    # a warm afternoon plus a cold reading at 00:30 CDT the next clock day
    times = [datetime(2026, 7, 14, 15, tzinfo=TZ),
             datetime(2026, 7, 15, 0, 30, tzinfo=TZ)]
    temps = [95.0, 70.0]
    hi, lo = S.day_high_low(times, temps, summer)
    assert lo == 70    # the post-midnight reading settles this day
    assert hi == 95
```

Re-base the existing summer fixtures to winter (so their clock-midnight-offset series stay in-window, exactly as before the change):

- `tests/test_settlement.py`: change `DAY = date(2025, 7, 15)` → `DAY = date(2025, 1, 15)`.
- `tests/test_warm_low_bias.py`: change every `day = date(2030, 7, 1)` → `day = date(2030, 1, 1)` (leave the `date.today()` test untouched).
- `tests/test_conditional_offset.py`: change every `day = date(2030, 7, 1)` → `day = date(2030, 1, 1)`; change `d_clear = date(2026, 6, 10)` → `date(2026, 1, 10)` and `d_cloud = date(2026, 6, 11)` → `date(2026, 1, 11)`. (Leave `_days`' `date(2026, 5, 1)` default and the line-67 `date(2026, 5, 1)` — those operate on pre-computed extreme maps, not windowed series, so LST doesn't touch them.)

- [ ] **Step 2: Run tests to verify the state**

Run: `.venv/bin/python -m pytest tests/test_settlement.py::test_local_day_bounds_is_lst_not_clock tests/test_settlement.py::test_post_clock_midnight_reading_settles_prior_day -v`
Expected: both FAIL (window is still clock-based). `test_local_day_bounds_winter_matches_clock` PASSES already (winter is unchanged).

- [ ] **Step 3: Implement the LST window**

3a. `config.py` — add after the `TIMEZONE` definition (near the top station block):

```python
# The NWS Climatological Report (CLIDFW) — what Kalshi settles on — defines its
# climate day as midnight-to-midnight LOCAL STANDARD TIME (UTC−6) year-round,
# i.e. 1:00 AM → 1:00 AM CDT during daylight saving. This is the settlement-day
# boundary, distinct from TIMEZONE (America/Chicago), which stays the
# wall-clock/diurnal zone for hour-of-day logic and all display. Verified
# 2026-07-14 (docs/benchmarks/2026-07-14/climate-day/FINDINGS.md).
CLIMATE_TZ = "Etc/GMT+6"
```

3b. `settlement.py` — extend the config import and add the zone:

```python
from config import BIN_HIGH, BIN_LOW, CLIMATE_TZ, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
_CLIMATE_TZ = ZoneInfo(CLIMATE_TZ)
```

3c. `settlement.py` — `local_day_bounds` builds in the climate zone:

```python
def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of the settlement (NWS climate) day, as tz-aware datetimes.

    Built in fixed Local Standard Time (CLIMATE_TZ, UTC−6) — the CLIDFW climate
    day Kalshi settles on — NOT clock time: in summer this window is 01:00 CDT →
    01:00 CDT, one hour after clock midnight. Comparisons elsewhere convert obs
    to America/Chicago and compare against these bounds by absolute instant, so
    the zone difference is transparent to them; only the day *boundary* moves.
    Fixed UTC−6 means every settlement day is exactly 24h (no DST 23h/25h days).
    """
    start = datetime(day.year, day.month, day.day, tzinfo=_CLIMATE_TZ)
    end = start + timedelta(days=1)
    return start, end
```

- [ ] **Step 4: Run the three test files, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_settlement.py tests/test_warm_low_bias.py tests/test_conditional_offset.py -v`
Expected: all pass (3 new + the re-based ones).
Run: `.venv/bin/python -m pytest -q`
Expected: all pass (302 pre-existing + 3 new). If any test outside these three files fails, STOP — it means a fixture wasn't caught by the exploratory run; report it rather than weakening it.

- [ ] **Step 5: Commit**

```bash
git add config.py settlement.py tests/test_settlement.py tests/test_warm_low_bias.py tests/test_conditional_offset.py
git commit -m "feat: settlement window is the NWS climate day (fixed LST), not clock midnight"
```

---

### Task 2: Front-guard scan reaches the settlement day's final hour

**Files:**
- Modify: `model.py` (`_member_extreme`, the locked-low front scan ~line 259)
- Test: `tests/test_front_guard.py` (add 2 tests)

**Interfaces:**
- Consumes: Task 1's LST `local_day_bounds` (already called as `start, end = local_day_bounds(day)` at the top of `_member_extreme`); `config.FRONT_SCAN_FROM_HOUR` (12), `TZ`.
- Produces: no new interface; the front scan now includes the post-midnight tail of the settlement day.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_front_guard.py` (uses the existing `_at`, `_fc`, `_curve`, `_obs_locked_afternoon`, `_DAY`):

```python
def test_front_scan_includes_post_midnight_tail():
    # Under the LST window, _DAY's settlement day runs to 01:00 CDT the next
    # clock day. A front whose only undercut is a forecast reading at 00:30 CDT
    # the next day must reopen the locked low — the old `t.hour >= 12` clock
    # filter dropped it (hour 0).
    ev = _curve({18: 88, 21: 85, 23: 82})          # evening stays warm...
    series = {"det_a": _fc(ev), "det_b": _fc(ev)}
    # ...but append a cold projection at 00:30 CDT the NEXT clock day (in-window)
    tail_t = _at_next(0, 30)
    for lbl in series:
        t, v = series[lbl]
        series[lbl] = (t + [tail_t], v + [70.0])
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is True
    assert out["consensus"] < 78.0                 # pulled toward the 70 tail


def test_front_scan_still_excludes_pre_noon_dip():
    # A pre-noon dip (hour 9) still cannot trigger the guard.
    curve = _curve({18: 88, 21: 85, 23: 82})
    curve[9] = 70.0
    series = {"det_a": _fc(curve), "det_b": _fc(curve)}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is False
```

Add the `_at_next` helper near the top of `tests/test_front_guard.py` (next to `_at`):

```python
def _at_next(hour, minute=0):
    """A datetime on the clock day AFTER _DAY (the settlement day's LST tail)."""
    nxt = _DAY + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, hour, minute, tzinfo=_TZ)
```

(Ensure `timedelta` is imported in the file; if not, add it to the `datetime` import.)

- [ ] **Step 2: Run tests to verify state**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py::test_front_scan_includes_post_midnight_tail tests/test_front_guard.py::test_front_scan_still_excludes_pre_noon_dip -v`
Expected: `test_front_scan_includes_post_midnight_tail` FAILS (the `t.hour >= 12` filter drops the hour-0 tail); the pre-noon test PASSES already.

- [ ] **Step 3: Implement the noon-anchored scan**

In `model.py`, `_member_extreme`, the locked-low branch currently reads:

```python
        scan = [v for t, v in remaining if t.hour >= FRONT_SCAN_FROM_HOUR]
        if scan and min(scan) <= observed - FRONT_UNDERCUT_MARGIN:
            return min(scan)
        return observed
```

Replace with:

```python
        # Scan from local noon of the settlement day's primary date through the
        # window end — NOT by raw clock hour. Under the LST window the day's
        # final hour is 00:00–00:59 of the next clock day (t.hour == 0), which a
        # `t.hour >= 12` filter would silently drop, defeating the guard exactly
        # on the post-midnight-front nights it exists for. `noon` is derived from
        # the window start, so this is correct in summer and byte-identical in
        # winter (start is clock midnight there, so noon == start + 12h either way).
        noon = start.astimezone(TZ).replace(
            hour=FRONT_SCAN_FROM_HOUR, minute=0, second=0, microsecond=0)
        scan = [v for t, v in remaining if t >= noon]
        if scan and min(scan) <= observed - FRONT_UNDERCUT_MARGIN:
            return min(scan)
        return observed
```

(`start` is already in scope from `start, end = local_day_bounds(day)` at the top of `_member_extreme`; `remaining` holds `(t, v)` pairs with `t` already `astimezone(TZ)`.)

- [ ] **Step 4: Run tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v`
Expected: all pass (existing front-guard tests unaffected — their evening readings at hours 18/21/23 are ≥ noon; the pre-noon dip at hour 9 is < noon).
Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_front_guard.py
git commit -m "fix: front-guard scan reaches the settlement day's post-midnight tail"
```

---

### Task 3: Real-data pipeline replay on May 26, 2026 (validation gate)

**Files:**
- Create: `docs/benchmarks/2026-07-14/lst-window/replay_lst_window.py`
- Create (generated): `docs/benchmarks/2026-07-14/lst-window/RESULTS.md`

**Interfaces:**
- Consumes: `settlement.day_high_low`, `settlement.local_day_bounds`, `sources.station_history._fetch_series`, `settlement._CLIMATE_TZ`. Network: IEM 5-min ASOS archive (cached to `.cache/`).
- Produces: a committed RESULTS.md; no shipped-code changes.

Note: this confirms on REAL obs what Task 1's unit test asserts synthetically — that the model's computed daily extreme for a real boundary day now matches the CLIDFW settlement.

- [ ] **Step 1: Write the replay script**

```python
"""Real-data confirmation of the LST settlement window on the discriminating
day found during verification (May 26 2026: CLI min 67 recorded 11:59 PM LST =
12:59 AM CDT May 27, which the old clock window dropped).

Recomputes day_high_low for May 26 under BOTH windows from the real 5-min ASOS
feed and checks the LST window matches the CLIDFW value (67) while the clock
window does not; a winter control day must be identical under both.

Run from the repo root:
  .venv/bin/python docs/benchmarks/2026-07-14/lst-window/replay_lst_window.py
"""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import settlement
from config import TIMEZONE
from sources.station_history import _fetch_series

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "RESULTS.md")
TZ = ZoneInfo(TIMEZONE)
CLOCK_TZ = TZ  # the OLD window basis

# (day, expected CLI low from the CLIDFW product, kind)
CASES = [
    (date(2026, 5, 26), 67, "BOUNDARY (min recorded 12:59 AM CDT May 27)"),
    (date(2026, 1, 15), None, "WINTER CONTROL (LST == clock)"),
]


def _clock_bounds(day):
    start = datetime(day.year, day.month, day.day, tzinfo=CLOCK_TZ)
    return start, start + timedelta(days=1)


def _min_in(times, temps, start, end):
    vals = [v for t, v in zip(times, temps)
            if v is not None and start <= t.astimezone(TZ) < end]
    return settlement.round_half_up(min(vals)) if vals else None


def main():
    lines = ["# LST window replay — real 5-min ASOS obs", ""]
    all_ok = True
    for day, cli_low, kind in CASES:
        # pull a two-day span so the post-midnight tail is available
        times, temps = _fetch_series(day, day + timedelta(days=1))
        lst_start, lst_end = settlement.local_day_bounds(day)
        clk_start, clk_end = _clock_bounds(day)
        lst_low = _min_in(times, temps, lst_start, lst_end)
        clk_low = _min_in(times, temps, clk_start, clk_end)
        lines.append(f"## {day} — {kind}")
        lines.append(f"- LST-window min:   {lst_low}")
        lines.append(f"- clock-window min: {clk_low}")
        if cli_low is not None:
            ok = (lst_low == cli_low and clk_low != cli_low)
            lines.append(f"- CLIDFW low: {cli_low} — "
                         f"{'PASS (LST matches, clock does not)' if ok else 'FAIL'}")
            all_ok = all_ok and ok
        else:
            ok = (lst_low == clk_low)
            lines.append(f"- control: {'PASS (identical)' if ok else 'FAIL (differ)'}")
            all_ok = all_ok and ok
        lines.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python docs/benchmarks/2026-07-14/lst-window/replay_lst_window.py`
Expected: prints both cases and exits 0 — May 26 shows LST-window min 67 (matching CLIDFW) while the clock window differs, and the January control is identical under both windows. First run fetches the archive (~a few seconds; retry on a transient network error). If May 26 does NOT reproduce (e.g. the archive's rounding differs), STOP and report the numbers — do not commit a failing gate.

- [ ] **Step 3: Commit**

```bash
git add docs/benchmarks/2026-07-14/lst-window/
git commit -m "test: real-data replay confirming the LST window settles May 26 correctly"
```

---

### Task 4: Final verification

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (302 pre-existing + 5 new).

- [ ] **Step 2: Confirm the diurnal/clock logic is untouched**

Run: `.venv/bin/python -c "
import re
src = open('model.py').read() + open('settlement.py').read()
# hour-of-day checks must still read wall-clock (TZ), not the climate zone
assert '_CLIMATE_TZ' not in open('model.py').read(), 'model.py should use local_day_bounds, not the climate zone directly'
print('diurnal/clock logic stays on America/Chicago; only the day boundary moved')"`
Expected: prints the confirmation (model.py never references the climate zone directly — it only calls `local_day_bounds`).

- [ ] **Step 3: Post-merge note (for the controller, not a code step)**

The window change is inert in winter and active from mid-March. There is no live dashboard-visible change on a normal summer day except that the first clock hour after midnight is no longer double-counted; the effect surfaces only on boundary nights. No Action/deploy step needed beyond the merge.

- [ ] **Step 4: Use superpowers:finishing-a-development-branch to merge/PR `lst-climate-window`**
