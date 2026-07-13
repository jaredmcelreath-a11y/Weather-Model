# Front-Aware Locked Low Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A locked daily low reopens when forecast members project a post-noon evening reading that undercuts the observed morning min — so a non-convective cold front can't produce a confident wrong forecast.

**Architecture:** Per-member undercut inside `model._member_extreme` (spec: `docs/superpowers/specs/2026-07-13-front-aware-low-lock-design.md`). When the low is `locked`, a member returns `min(anchored post-noon forecast temps)` instead of `observed` iff that min undercuts `observed − 0.5°F`. Consensus shift, sigma reopening (via `locked_ratio`), and bins all fall out of the existing sample machinery. A `front_widened` flag flows to the dashboard for the Resolved cap and an amber badge.

**Tech Stack:** Pure Python (stdlib only), pytest via the repo venv, existing model/dashboard modules.

## Global Constraints

- Python 3.9 venv: run everything with `.venv/bin/python` from the repo root (`/Users/jared/Desktop/Weather Model`); no new dependencies.
- Branch: `front-aware-low-lock` (already created; spec committed).
- All timestamps tz-aware in `America/Chicago` (`config.TIMEZONE`); tests build datetimes with `ZoneInfo(TIMEZONE)` like `tests/test_low_lock.py`.
- The HIGH's behavior must be byte-identical before/after — its locked branch still returns `observed` unconditionally.
- A calm summer day (all post-noon anchored forecasts above the morning min) must produce identical samples to today's code.
- New constants (exact values from the spec): `FRONT_UNDERCUT_MARGIN = 0.5`, `FRONT_SCAN_FROM_HOUR = 12`.
- No `live=` gating on the guard — it is purely forecast-driven and must run in backtest/replay (deliberately unlike the convective floor).
- Comment style: prose comments explaining *why*, matching model.py's existing density.

---

### Task 1: Config constants + the undercut mechanism in `_member_extreme`

**Files:**
- Modify: `config.py` (after the `HIGH_BUMPY_STD` block, ~line 125)
- Modify: `model.py` (import block lines 25–29; `_member_extreme` lines 179–251)
- Test: `tests/test_front_guard.py` (new)

**Interfaces:**
- Consumes: existing `_member_extreme(times, temps, day, variable, now, observed, obs_now=None, locked=False) -> float | None`. Signature is UNCHANGED.
- Produces: same function; new behavior only on `(variable == "low", locked=True)` path. `config.FRONT_UNDERCUT_MARGIN: float = 0.5` and `config.FRONT_SCAN_FROM_HOUR: int = 12`, both imported into model's namespace (Task 4's replay monkeypatches `model.FRONT_UNDERCUT_MARGIN`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_front_guard.py`:

```python
"""Front-aware locked low: a locked member reports its anchored post-noon
forecast minimum instead of the observed min when that projection undercuts it
by FRONT_UNDERCUT_MARGIN — so a dry evening cold front (which the POP-gated
convective floor can't see) reopens the low instead of being discarded."""
from datetime import date, datetime

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


def _fc(curve):
    """{hour: temp} -> full-day hourly (times, temps) forecast series.
    Hours not listed interpolate nothing — list every hour you need."""
    hours = sorted(curve)
    return ([_at(h) for h in hours], [curve[h] for h in hours])


def _curve(evening):
    """A standard day shape: cool dawn, warm afternoon, then `evening` values
    for hours 18/21/23. Morning min ~78, peak 95 at 15:00."""
    base = {0: 84, 2: 82, 4: 80, 6: 78, 8: 82, 10: 86, 12: 90, 13: 92,
            14: 93, 15: 95, 16: 94, 17: 92}
    base.update(evening)
    return base


# ---- the locked-low undercut path (unit: _member_extreme directly) ----

def test_calm_locked_low_returns_observed():
    # Evening stays well above the 78.0 morning min -> locked exactly as today.
    times, temps = _fc(_curve({18: 88, 21: 84, 23: 81}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_front_undercut_reports_forecast_min():
    # Front: evening drops to 74.5 (3.5 under the observed min) -> the member
    # reports its projected new low, not the stale morning min.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 74.5


def test_pre_noon_dip_cannot_trigger():
    # A 9am forecast dip 2 under the min (dawn-adjacent jitter, the reason the
    # early sunrise lock exists) must NOT reopen the lock: scan starts at 12:00.
    curve = _curve({18: 88, 21: 84, 23: 81})
    curve[9] = 76.0                      # pre-noon dip, still in `remaining` at 08:00
    times, temps = _fc(curve)
    got = model._member_extreme(times, temps, _DAY, "low", _at(8),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_margin_graze_ignored():
    # Post-noon min 77.7 vs observed 78.0: undercut 0.3 < 0.5 margin -> locked.
    times, temps = _fc(_curve({18: 82, 21: 79, 23: 77.7}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_no_remaining_postnoon_hours_falls_back():
    # 23:30 with the last forecast point at 23:00 (already past): nothing left
    # to scan -> observed, no crash.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(23, 30),
                                observed=74.5, obs_now=None, locked=True)
    assert got == 74.5


def test_anchoring_offset_applies_to_scan():
    # Raw post-noon min is 78.2 (no trigger vs 78.0), but the member currently
    # reads 1°F warm (obs_now 89 vs fc_now 90 at 13:00 — the curve's 13:00 value
    # is pinned to 90 so the interpolated fc_now is exact), so its anchored
    # evening projection is 77.2 -> undercut fires at the ANCHORED value.
    curve = _curve({18: 82, 21: 79.5, 23: 78.2})
    curve[13] = 90.0                     # fc_now at 13:00 -> offset = 89 - 90 = -1
    times, temps = _fc(curve)
    unanchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                       observed=78.0, obs_now=None, locked=True)
    anchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                     observed=78.0, obs_now=89.0, locked=True)
    assert unanchored == 78.0
    assert anchored == 77.2


def test_locked_high_still_pins_to_observed():
    # The high's locked branch is untouched: a forecast projecting hotter later
    # is still ignored once locked (peak-postdates-trough guard owns the high).
    times, temps = _fc(_curve({18: 96, 21: 90, 23: 86}))
    got = model._member_extreme(times, temps, _DAY, "high", _at(16),
                                observed=95.0, obs_now=None, locked=True)
    assert got == 95.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v`
Expected: `test_front_undercut_reports_forecast_min` and `test_anchoring_offset_applies_to_scan` FAIL (locked path returns `observed` today); the calm/graze/pre-noon/high tests may already pass — that's fine, they're regression guards.

- [ ] **Step 3: Add the config constants**

In `config.py`, insert after the `HIGH_BUMPY_STD = 1.5` line (before `MAX_CLI_GAP`):

```python
# --- Front-aware locked low ---
# A locked morning min can still be undercut by a real evening cold front
# before midnight (Kalshi settles the full-day min), and the POP-gated
# convective floor can't see a dry front. A member whose obs-anchored
# afternoon/evening projection undercuts the observed min by at least the
# margin reports that projection instead of the observed min. The margin
# clears anchor jitter; scanning only hours >= FRONT_SCAN_FROM_HOUR keeps the
# dawn-adjacent wobble (the reason the early sunrise lock exists) from
# reopening a calm day's lock — "a new low later today" is an
# afternoon/evening event.
FRONT_UNDERCUT_MARGIN = 0.5   # °F below the observed min a projection must reach
FRONT_SCAN_FROM_HOUR = 12     # local hour the undercut scan starts
```

- [ ] **Step 4: Implement the guard in `model.py`**

4a. Extend the config import (lines 25–29) to include the two new names, keeping alphabetical order:

```python
from config import (BIN_HIGH, BIN_LOW, CACHE_TTL_SECONDS, CALM_WIND_MAX,
                    CLEAR_CLOUD_MAX, FRONT_SCAN_FROM_HOUR,
                    FRONT_UNDERCUT_MARGIN, HIGH_BUMPY_STD, HIGH_LOCK_DROP,
                    HIGH_LOCK_NOON_OFFSET_HOURS, HIGH_PLATEAU_MAX,
                    LEAD_SIGMA_INFLATION, LOW_LOCK_RISE, MAX_CLI_GAP,
                    PEAK_LOCK_DROP, TIMEZONE, bin_labels, lead_bucket)
```

4b. In `_member_extreme`, change `remaining` to keep `(local_time, temp)` pairs. The collection loop becomes:

```python
    start, end = local_day_bounds(day)
    day_vals, remaining = [], []   # remaining: (local time, temp) pairs after `now`
    # Bracket the forecast around `now` so the anchor can be interpolated to the
    # exact time. Snapping fc_now to the last whole hour made it a step function
    # that jumped at the top of each hour while the observation anchor hadn't yet
    # updated — collapsing the offset and dropping the projected extreme (the
    # sawtooth dip visible on the consensus at :00-:01 during the morning climb).
    lo_t = lo_v = hi_t = hi_v = None
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if not (start <= t < end):
            continue
        day_vals.append(v)
        if now is not None:
            if t > now:
                remaining.append((t, v))
                if hi_t is None:            # first forecast hour after now
                    hi_t, hi_v = t, v
            else:
                lo_t, lo_v = t, v           # ascending -> latest hour <= now
```

4c. Replace the block from the `locked` early-return through the end of the function (currently: locked-return, then offset, then the high/low combine) with — note the offset now computes *before* the locked branch so the low's scan sees anchored values:

```python
    # Anchor the remaining forecast to the current observation.
    offset = (obs_now - fc_now) if (obs_now is not None and fc_now is not None) else 0.0
    remaining = [(t, v + offset) for t, v in remaining]

    # Extreme already passed: the realized value is the answer; ignore the
    # forecast's projected further rise/fall. Exception — the low's front
    # guard: a locked morning min can still be undercut by a real evening cold
    # front before midnight (Kalshi settles the full-day min), which the
    # POP-gated convective floor can't see when the front is dry. A member
    # whose anchored post-noon projection undercuts the observed min by
    # FRONT_UNDERCUT_MARGIN reports that projection, so the consensus and
    # spread follow the forecast signal; everyone else stays pinned to the
    # observed min. The scan starts at FRONT_SCAN_FROM_HOUR so dawn-adjacent
    # jitter (the reason the early sunrise lock exists) can't reopen a calm
    # day's lock. The high keeps the unconditional pin: nothing sets a new
    # daytime max after its window, and its lock already carries the
    # peak-postdates-trough guard.
    if locked and observed is not None:
        if variable == "high":
            return observed
        scan = [v for t, v in remaining if t.hour >= FRONT_SCAN_FROM_HOUR]
        if scan and min(scan) <= observed - FRONT_UNDERCUT_MARGIN:
            return min(scan)
        return observed

    # Today: combine realized-so-far with the anchored forecast of what's left.
    if variable == "high":
        fcst = max((v for _, v in remaining), default=-math.inf)
        return max(observed if observed is not None else -math.inf, fcst)
    else:
        fcst = min((v for _, v in remaining), default=math.inf)
        return min(observed if observed is not None else math.inf, fcst)
```

4d. Update the `_member_extreme` docstring's last paragraph (currently "When `locked` … return `observed`, so a forecast still projecting more rise/fall can't push past what already happened.") to:

```
    When `locked` (the extreme has demonstrably passed — see `_extreme_locked`),
    the realized extreme supersedes the forecast: return `observed`. The one
    exception is the low's front guard — a member whose anchored post-noon
    forecast undercuts the observed min by FRONT_UNDERCUT_MARGIN returns that
    projected new low instead, so an evening cold front reopens the locked low.
```

- [ ] **Step 5: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v`
Expected: all 7 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (269 before this change; the calm-day identity keeps every existing lock/nowcast test green).

- [ ] **Step 6: Commit**

```bash
git add config.py model.py tests/test_front_guard.py
git commit -m "feat: front guard — a locked low member reports an anchored post-noon undercut"
```

---

### Task 2: `front_widened` flag from `predict_variable`

**Files:**
- Modify: `model.py` (`predict_variable`: after the `_collect_samples` call ~line 522, and the return dict ~line 690)
- Test: `tests/test_front_guard.py` (extend)

**Interfaces:**
- Consumes: Task 1's `_member_extreme` behavior (undercut members return values `< observed`).
- Produces: `predict_variable(...)` return dict gains key `"front_widened": bool` — True iff the low is locked and ≥1 member took the undercut path. Task 3's dashboard code reads `d.get("front_widened")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_front_guard.py`:

```python
# ---- integration: predict_variable consensus/sigma/flag ----

def _obs_locked_afternoon():
    """Observed series: dawn min 78.0 at 06:00, risen to 93 by 14:00 (locked).
    The 14:00 reading matches the members' 14:00 forecast (93 in `_curve`) so
    the anchoring offset is 0 and the evening scenarios stay uncontaminated."""
    hours = [(0, 84), (2, 82), (4, 80), (6, 78.0), (8, 82), (10, 86),
             (12, 88), (14, 93)]
    return ([_at(h) for h, _ in hours], [t for _, t in hours])


def test_predict_variable_front_day_shifts_and_widens():
    # Two members agree through the afternoon (fc 90 at 14:00 -> offset 0), then
    # disagree on the evening: det_a stays warm, det_b drops to 74. The locked
    # low must shift below the morning min, reopen its spread, and set the flag.
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 80, 21: 76, 23: 74}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["peak_locked"] is True
    assert out["front_widened"] is True
    assert out["consensus"] < 78.0          # mean of 78 and 74
    assert out["sigma_used"] > model._SIGMA_FLOOR


def test_predict_variable_calm_day_unchanged():
    # Both members keep the evening above the min: byte-identical to today —
    # consensus pinned to the observed min, sigma collapsed to the floor.
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 84, 21: 82, 23: 81}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["peak_locked"] is True
    assert out["front_widened"] is False
    assert out["consensus"] == 78.0
    assert out["sigma_used"] == model._SIGMA_FLOOR


def test_high_never_sets_front_widened():
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "high", _at(14), None)
    assert out["front_widened"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v`
Expected: the three new tests FAIL with `KeyError: 'front_widened'`.

- [ ] **Step 3: Implement the flag**

In `predict_variable`, immediately after the samples guard (`if not samples or not fullday: return None`), add:

```python
    # Front guard fired: at least one locked-low member reported an anchored
    # post-noon projection below the observed min (see _member_extreme).
    # Computed here, before the cooling/settle/bias offsets move the samples,
    # so the comparison against `observed` is clean.
    front_widened = (locked and variable == "low" and observed is not None
                     and any(s < observed for s in samples))
```

And add to the return dict, right after `"convective_widened": convective_widened,`:

```python
        "front_widened": front_widened,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v` — all PASS.
Run: `.venv/bin/python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_front_guard.py
git commit -m "feat: surface the front guard as a front_widened flag on predictions"
```

---

### Task 3: Dashboard — Resolved cap, amber badge, caption

**Files:**
- Modify: `market_view.py` (`displayed_resolved` ~line 681; `lock_status` low section ~line 739; `render_variable` captions ~line 907)
- Test: `tests/test_lock_status_front.py` (new)

**Interfaces:**
- Consumes: `d.get("front_widened")` from Task 2's prediction dict.
- Produces: display behavior only — no new interfaces.

Design note (refinement over the spec's "move the `consensus < obs − 1` check"): the badge triggers on the `front_widened` flag, not on the consensus gap. The CLI measured-gap anchor can legitimately put a calm locked consensus ~1°F under the hourly observed min (`settle_shift = cli_low − observed`, trusted down to −3), so the consensus-gap test would false-alarm on calm days; the flag is the precise signal. The existing pre-lock `consensus < obs − 1.0` warning stays where it is, unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lock_status_front.py`:

```python
"""Display-layer reconciliation for the front guard: when forecast members
project an evening undercut of the locked low, the badge and Resolved metric
must stop claiming the low is settled (mirrors test_lock_status_convective)."""

from market_view import lock_status, displayed_resolved


def _low(**over):
    d = {
        "locked_ratio": 0.3,
        "resolved": 1.0,          # the low's time window closed at 9am
        "observed_so_far": 78.0,
        "consensus": 76.0,        # members project a colder evening
        "sigma_used": 2.1,
        "peak_locked": True,
        "convective_widened": False,
        "front_widened": True,
    }
    d.update(over)
    return d


def test_front_widened_downgrades_lock():
    level, headline, detail = lock_status(_low(), "low")
    assert level == "warning"
    assert "front" in (headline + detail).lower()
    assert "prime buy window" not in detail.lower()


def test_no_front_still_green():
    level, headline, _ = lock_status(_low(front_widened=False, consensus=78.0,
                                          locked_ratio=0.0, sigma_used=0.7), "low")
    assert level == "success"
    assert headline == "Locked — Dawn Trough Is In"


def test_displayed_resolved_capped_on_front():
    assert displayed_resolved(_low()) <= 90
    assert displayed_resolved(_low(front_widened=False)) == 100


def test_convective_badge_still_wins_when_both():
    # A stormy front day can set both flags; either warning is fine, but the
    # level must be warning and the box must not read as settled.
    level, _, detail = lock_status(_low(convective_widened=True), "low")
    assert level == "warning"
    assert "prime buy window" not in detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lock_status_front.py -v`
Expected: `test_front_widened_downgrades_lock` FAILS (returns the green locked badge today) and `test_displayed_resolved_capped_on_front` FAILS (returns 100).

- [ ] **Step 3: Implement the display changes**

3a. `displayed_resolved` — change the cap condition:

```python
    if d.get("convective_widened") or d.get("front_widened"):
        pct = min(pct, CONVECTIVE_RESOLVED_CAP)
```

(Also update its docstring's first line to "…clamped on a convective- or front-risk day.")

3b. `lock_status` — in the `# variable == "low"` section, insert between the `convective_widened` branch and the `peak_locked` branch:

```python
    if d.get("front_widened"):
        # Forecast members project a post-noon reading under the locked morning
        # min — a cold front may set a new daily low before midnight. Don't show
        # the green settled badge the flag contradicts.
        return ("warning", "Front Risk — Colder Evening Reading Forecast",
                f"Dawn trough is in at {obs:.1f}°F, but forecast members project "
                f"a colder reading before midnight (consensus {consensus:.1f}°F) "
                "— a front may undercut the morning low. NOT safe to treat as "
                "settled — wait or size down.")
```

3c. `render_variable` — after the convective `risk_label` caption block, add:

```python
        if d.get("front_widened"):
            st.caption("Forecast front risk — models project a colder evening "
                       "reading; the low may not be final.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lock_status_front.py tests/test_lock_status_convective.py -v`
Expected: all PASS (the convective file proves the existing badges are undisturbed).

Run: `.venv/bin/python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add market_view.py tests/test_lock_status_front.py
git commit -m "feat: front-risk badge + Resolved cap when the front guard reopens the low"
```

---

### Task 4: Historical replay validation (front days vs calm control)

**Files:**
- Create: `docs/benchmarks/2026-07-13/front-guard/replay_front_guard.py`
- Create (generated by running it): `docs/benchmarks/2026-07-13/front-guard/RESULTS.md`

**Interfaces:**
- Consumes: `model.predict_variable`, `model.FRONT_UNDERCUT_MARGIN` (module attribute — setting it to `inf` disables the guard, giving today's behavior as the A/B baseline), `sources.station_history._fetch_series`, `sources.common.to_hourly`, `sources.open_meteo_models.fetch_historical`, `sources.station_history.fetch_actual_cli`.
- Produces: a committed RESULTS.md; no shipped-code changes. Network required (IEM + Open-Meteo archives) — run locally, results are cached to `.cache/`.

Note: the Open-Meteo *ensemble* archive only retains ~5 days, so the replay runs on the five deterministic members — an accepted spec limitation (the mechanism is member-shape-agnostic).

- [ ] **Step 1: Write the replay script**

```python
"""A/B replay of the front-aware locked low on real KDFW days.

Finds recent days whose daily minimum occurred in the EVENING (a front undercut
the morning low — the exact failure the guard targets) plus a calm dawn-low
control day, then replays predict_variable at several intraday times with the
guard ON (shipped config) and OFF (margin=inf → today's pre-guard behavior).

Success criteria (spec): on front days the guarded run shifts the locked low
toward the coming front while the baseline stays pinned to the morning min; on
the control day the two runs are identical.

Run from the repo root:  .venv/bin/python docs/benchmarks/2026-07-13/front-guard/replay_front_guard.py
"""
import math
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import model
from config import TIMEZONE
from settlement import local_day_bounds
from sources import open_meteo_models, station_history
from sources.common import to_hourly
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
OUT = os.path.join(os.path.dirname(__file__), "RESULTS.md")

SEARCH_START = date(2026, 1, 15)   # winter/spring: front season
SEARCH_END = date(2026, 5, 15)
NOW_HOURS = [10, 14, 18, 21]       # intraday replay times (local)


def _daily_min_hour(times, temps, day):
    """(min_temp, local hour of the min) for `day` from the hourly series."""
    start, end = local_day_bounds(day)
    best = None
    for t, v in zip(times, temps):
        t = t.astimezone(TZ)
        if start <= t < end and (best is None or v < best[0]):
            best = (v, t.hour + t.minute / 60.0)
    return best


def find_days():
    """(front_days, control_day): days whose min landed after 18:00 local, and
    one calm day whose min landed before 09:00."""
    times, temps = to_hourly(*station_history._fetch_series(SEARCH_START, SEARCH_END))
    fronts, control = [], None
    day = SEARCH_START
    while day <= SEARCH_END:
        got = _daily_min_hour(times, temps, day)
        if got:
            _, hr = got
            if hr >= 18:
                fronts.append(day)
            elif hr <= 9 and control is None:
                control = day
        day += timedelta(days=1)
    return fronts[-2:], control     # the two most recent front days


def replay(day, guard_on):
    """[(hour, consensus, sigma, front_widened, observed_min_so_far)] for `day`."""
    series = open_meteo_models.fetch_historical(day, day + timedelta(days=1))
    obs = {"obs": to_hourly(*station_history._fetch_series(day, day))}
    saved = model.FRONT_UNDERCUT_MARGIN
    model.FRONT_UNDERCUT_MARGIN = saved if guard_on else math.inf
    rows = []
    try:
        for h in NOW_HOURS:
            now = datetime(day.year, day.month, day.day, h, tzinfo=TZ)
            out = model.predict_variable(series, obs, day, "low", now, None)
            if out:
                rows.append((h, out["consensus"], out["sigma_used"],
                             out["front_widened"], out["observed_so_far"]))
    finally:
        model.FRONT_UNDERCUT_MARGIN = saved
    return rows


def main():
    fronts, control = find_days()
    days = [(d, "FRONT") for d in fronts] + ([(control, "CONTROL")] if control else [])
    actual = station_history.fetch_actual_cli(min(d for d, _ in days),
                                              max(d for d, _ in days))
    lines = ["# Front-guard replay — guarded (ON) vs today's behavior (OFF)", ""]
    for day, kind in days:
        settled = actual.get(day, (None, None))[1]
        lines.append(f"## {day} ({kind}) — settled CLI low: {settled}")
        lines.append("| now | consensus ON | consensus OFF | sigma ON | sigma OFF | flag |")
        lines.append("|---|---|---|---|---|---|")
        on, off = replay(day, True), replay(day, False)
        for (h, c1, s1, fw, _obs), (_, c0, s0, _, _) in zip(on, off):
            lines.append(f"| {h}:00 | {c1} | {c0} | {s1} | {s0} | {fw} |")
        lines.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python docs/benchmarks/2026-07-13/front-guard/replay_front_guard.py`
Expected: `wrote docs/benchmarks/2026-07-13/front-guard/RESULTS.md`. First run takes a minute (archive fetches); IEM/Open-Meteo hiccups → rerun (responses cache to `.cache/`).

- [ ] **Step 3: Judge the results against the spec's success criteria**

Open RESULTS.md and check:
- FRONT days: at ≥1 afternoon/evening `now`, consensus ON < consensus OFF (the guard tracks the coming front; OFF stays pinned at the morning min), sigma ON ≥ sigma OFF, flag True — and consensus ON should sit closer to the settled CLI low.
- CONTROL day: every ON row identical to its OFF row, flag False.

If a searched window yields no front days (possible in a mild spring), widen `SEARCH_START` back to `date(2025, 11, 1)` and rerun. If results contradict the criteria, STOP and investigate before committing — do not commit a failing validation.

- [ ] **Step 4: Commit the script + results**

```bash
git add docs/benchmarks/2026-07-13/front-guard/
git commit -m "test: historical A/B replay validating the front-aware locked low"
```

---

### Task 5: Final verification

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (269 pre-existing + 14 new).

- [ ] **Step 2: Live smoke (optional but cheap)**

Run: `.venv/bin/python -c "import model, calibration; c=calibration.get(refresh=False); s=model.snapshot(c, settle_offset=(c or {}).get('settlement_offset'), continuous_obs=True); print(s['today']['low']['front_widened'], s['today']['low']['consensus'])"`
Expected: prints `False <consensus>` on a calm July day (flag quiet, consensus sane).

- [ ] **Step 3: Use superpowers:finishing-a-development-branch to merge/PR `front-aware-low-lock`**
