# Sunrise-gated Early Low Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the daily low ~an hour earlier by gating on computed sunrise plus a small confirming rise, instead of waiting for the full 2°F `PEAK_LOCK_DROP`.

**Architecture:** New dependency-free `solar.py` computes local sunrise from `LAT/LON` (NOAA fractional-year equations). `model._extreme_locked`'s low branch gains an early-lock trigger — `now ≥ sunrise AND cur − min ≥ LOW_LOCK_RISE` — OR'd with the unchanged 2°F fallback. Low only; high untouched.

**Tech Stack:** Python 3 (`math`, `datetime`, `zoneinfo`), pytest, project-local `.venv`.

## Global Constraints

- Low only. Do not change the high branch, `PEAK_LOCK_DROP` (stays 2.0), the settlement basis, sigma logic, or the bias corrections.
- Sunrise is computed locally — no network, no new dependency (only `math`/`datetime`/`zoneinfo`).
- The 2°F rise stays as the fallback trigger; the early lock is OR'd with it.
- `LOW_LOCK_RISE = 0.8°F`; no separate sunrise buffer.
- Run pytest via `.venv/bin/python -m pytest`.

---

### Task 1: `solar.py` — dependency-free sunrise

**Files:**
- Create: `solar.py`
- Create: `tests/test_solar.py`

**Interfaces:**
- Consumes: `config.LAT (32.90)`, `config.LON (-97.04)`, `config.TIMEZONE`.
- Produces: `solar.sunrise(day: date, lat: float = LAT, lon: float = LON, tz: ZoneInfo = TZ) -> datetime` — local, tz-aware sunrise for `day`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_solar.py`:

```python
"""Dependency-free sunrise for KDFW."""
from datetime import date, timezone

from solar import sunrise
from config import LAT, LON


def test_kdfw_summer_sunrise():
    sr = sunrise(date(2026, 7, 2))
    assert sr.tzinfo is not None
    assert sr.date() == date(2026, 7, 2)
    # ~06:23 CDT (UTC-5). Allow a few minutes of algorithm slack.
    assert sr.utcoffset().total_seconds() == -5 * 3600          # CDT
    mins = sr.hour * 60 + sr.minute
    assert 6 * 60 + 18 <= mins <= 6 * 60 + 28                    # 06:18–06:28


def test_kdfw_winter_sunrise_is_cst():
    sr = sunrise(date(2026, 1, 15))
    assert sr.date() == date(2026, 1, 15)
    assert sr.utcoffset().total_seconds() == -6 * 3600          # CST (DST handled)
    mins = sr.hour * 60 + sr.minute
    assert 7 * 60 + 20 <= mins <= 7 * 60 + 40                    # ~07:30


def test_accepts_explicit_coords():
    # Same call with explicit KDFW coords matches the default-arg call.
    assert sunrise(date(2026, 7, 2), LAT, LON) == sunrise(date(2026, 7, 2))
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_solar.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'solar'`.

- [ ] **Step 3: Create `solar.py`**

```python
"""Local sunrise for a fixed station, computed from NOAA's general solar
position equations (fractional-year method). No network, no dependencies — the
lock path calls this on every render, so it must be pure and cheap. Accurate to
~1 minute, far finer than the low lock needs.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import LAT, LON, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def sunrise(day: date, lat: float = LAT, lon: float = LON,
            tz: ZoneInfo = TZ) -> datetime:
    """Local, tz-aware sunrise for `day` at `(lat, lon)` (lon east-positive).

    Uses the refraction-corrected zenith of 90.833°. Computed in UTC and
    converted to `tz`, so DST is handled by zoneinfo. On a polar day/night the
    hour-angle cosine is clamped (never triggers at KDFW's latitude).
    """
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365.0 * (n - 1)                       # fractional year (rad)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g)
                       - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                       - 0.040849 * math.sin(2 * g))         # minutes
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))   # radians
    latr = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(latr) * math.cos(decl))
              - math.tan(latr) * math.tan(decl))
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))                    # sunrise hour angle (deg)
    minutes = 720 - 4 * (lon + ha) - eqtime                 # minutes past UTC midnight
    sr_utc = datetime(day.year, day.month, day.day,
                      tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return sr_utc.astimezone(tz)
```

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest tests/test_solar.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add solar.py tests/test_solar.py
git commit -m "feat: add dependency-free sunrise (solar.py)

NOAA fractional-year sunrise from LAT/LON, tz-aware (DST via zoneinfo).
Validated against KDFW summer/winter times.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Early low lock in `_extreme_locked`

**Files:**
- Modify: `config.py` — add `LOW_LOCK_RISE = 0.8`.
- Modify: `model.py` — import `solar` + `LOW_LOCK_RISE`; extend `_extreme_locked` low branch.
- Create: `tests/test_low_lock.py`.

**Interfaces:**
- Consumes: `solar.sunrise(day)` from Task 1; `config.LOW_LOCK_RISE`; existing `model._extreme_locked(times, temps, day, variable, now, drop=PEAK_LOCK_DROP) -> bool` and `model.predict_variable(...)`.
- Produces: the low returns `True` from `_extreme_locked` once `now ≥ sunrise(day)` and `cur − min ≥ LOW_LOCK_RISE`, in addition to the existing 2°F rule.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_low_lock.py`:

```python
"""Sunrise-gated early low lock."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)          # KDFW sunrise ~06:23 CDT


def _series(times, temps):
    """Single 'member' obs series (times tz-aware, temps °F)."""
    return times, temps


def _hours(*hhtemp):
    """[(hour, temp), ...] -> (times, temps) on _DAY, local tz."""
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    temps = [t for _, t in hhtemp]
    return times, temps


def test_low_early_locks_after_sunrise_on_small_rise():
    # Min 78.8 at 06:00, risen to 80.0 by 07:00 (risen 1.2 < 2.0). Past sunrise.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 7, tzinfo=_TZ)
    assert model._extreme_locked(times, temps, _DAY, "low", now) is True


def test_low_would_not_lock_under_old_2f_rule():
    # Same rise of 1.2°F must NOT satisfy the 2°F fallback on its own (guards
    # against the early-lock accidentally being a no-op / the fallback moving).
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 7, tzinfo=_TZ)
    # Before sunrise the same series stays unlocked (early gate closed, <2°F).
    pre = datetime(_DAY.year, _DAY.month, _DAY.day, 5, tzinfo=_TZ)
    early = _hours((0, 84), (2, 82), (4, 80), (5, 78.8))
    assert model._extreme_locked(early[0], early[1], _DAY, "low", pre) is False


def test_low_no_predawn_false_lock():
    # A pre-dawn wiggle: min 79 at 03:00, up to 80 at 04:00 (risen 1.0 >= 0.8)
    # but 04:00 is before sunrise -> must NOT lock.
    times, temps = _hours((0, 82), (1, 81), (2, 80), (3, 79.0), (4, 80.0))
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 4, tzinfo=_TZ)
    assert model._extreme_locked(times, temps, _DAY, "low", now) is False


def test_low_2f_fallback_still_fires_before_sunrise():
    # A full 2°F rise locks regardless of time of day (fallback unchanged).
    times, temps = _hours((0, 82), (2, 80), (3, 79.0), (4, 81.5))   # risen 2.5
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 4, tzinfo=_TZ)
    assert model._extreme_locked(times, temps, _DAY, "low", now) is True


def test_high_branch_unaffected_by_morning_rise():
    # The same rising-morning series, asked for the HIGH, must not lock: the
    # running max (midnight) precedes the running min, so the high guard holds.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 7, tzinfo=_TZ)
    assert model._extreme_locked(times, temps, _DAY, "high", now) is False


def test_predict_variable_locks_low_earlier():
    # Integration: rising morning obs, now 07:00 -> low locks to the observed
    # minimum, where the 2°F rule (risen 1.2) would still leave it unlocked.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 7, tzinfo=_TZ)
    fc_times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h in range(24)]
    series = {"det_a": (fc_times, [90 - abs(h - 15) for h in range(24)])}
    out = model.predict_variable(series, {"obs": (times, temps)}, _DAY, "low",
                                 now, None)
    assert out["peak_locked"] is True
    assert out["consensus"] == 78.8            # locked to the realized min
```

- [ ] **Step 2: Run and confirm the early-lock tests fail**

Run: `.venv/bin/python -m pytest tests/test_low_lock.py -q`
Expected: FAIL on `test_low_early_locks_after_sunrise_on_small_rise` and
`test_predict_variable_locks_low_earlier` (low not yet locking on <2°F). The
pre-dawn / fallback / high / pre-sunrise tests already pass.

- [ ] **Step 3: Add the config constant**

In `config.py`, right after `PEAK_LOCK_DROP = 2.0`:

```python
# The low locks early once past sunrise and risen this many °F above the running
# min — the dawn minimum is behind us; the margin clears obs/rounding jitter.
LOW_LOCK_RISE = 0.8
```

- [ ] **Step 4: Wire imports into `model.py`**

Add `LOW_LOCK_RISE` to the `from config import (...)` block, and add `import solar`
next to the other local imports (e.g. after `from convective import convective_sigma`):

```python
import solar
```

and in the config import list add `LOW_LOCK_RISE` (keep alphabetical grouping tidy):

```python
from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    LEAD_SIGMA_INFLATION, LOW_LOCK_RISE, PEAK_LOCK_DROP,
                    TIMEZONE, bin_labels, lead_bucket)
```

- [ ] **Step 5: Extend the low branch of `_extreme_locked`**

In `model.py`, the low branch currently ends the function:

```python
    return (cur - min(vals)) >= drop
```

Replace that final `return` with:

```python
    risen = cur - min(vals)
    if risen >= drop:
        return True
    # Early lock: past sunrise the dawn minimum is behind us; a small confirming
    # rise (clears obs + rounding jitter) means we're off the trough. The margin
    # naturally waits for a min that lands shortly after sunrise, since temps are
    # still falling toward it until then (risen <= 0).
    try:
        sr = solar.sunrise(day)
    except Exception:
        return False
    return now.astimezone(TZ) >= sr and risen >= LOW_LOCK_RISE
```

(The `variable == "high"` branch above this is unchanged.)

- [ ] **Step 6: Run the low-lock tests to green**

Run: `.venv/bin/python -m pytest tests/test_low_lock.py -q`
Expected: PASS (6 tests).

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — including the existing lock regressions
(`tests/test_cli_basis.py::test_locked_low_anchors_on_continuous_and_skips_widening`,
`::test_unlocked_low_still_widens_with_continuous`, and the high lock tests in
`tests/test_accuracy.py`).

- [ ] **Step 8: Commit**

```bash
git add config.py model.py tests/test_low_lock.py
git commit -m "feat: sunrise-gated early low lock

Lock the low once past sunrise and risen LOW_LOCK_RISE (0.8F) above the running
min, OR'd with the unchanged 2F fallback. On days like 2026-07-02 the low locks
~an hour earlier (the dawn min is set) instead of lagging its realized value all
morning. Low only.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- `solar.py` sunrise (NOAA, zenith 90.833°, tz/DST, dependency-free) → Task 1.
- `LOW_LOCK_RISE = 0.8` config → Task 2 Step 3.
- `_extreme_locked` low branch: `risen ≥ drop` OR (`now ≥ sunrise` AND `risen ≥ LOW_LOCK_RISE`), high untouched → Task 2 Step 5.
- No network / no new dependency → Task 1 uses only `math`/`datetime`/`zoneinfo`.
- Tests: sunrise summer+winter/tz; early-lock; no pre-dawn false lock; 2°F fallback; high unaffected; integration; existing regressions → Tasks 1–2.
- Non-goals (high, PEAK_LOCK_DROP, settlement/sigma/bias) → untouched.

**Placeholder scan:** none — all code and expected values are concrete (sunrise
values validated: 2026-07-02 → 06:23 CDT, 2026-01-15 → 07:30 CST).

**Type consistency:** `solar.sunrise(day, lat=LAT, lon=LON, tz=TZ) -> datetime`
used identically in `_extreme_locked` (`solar.sunrise(day)`) and the tests.
`_extreme_locked` keeps its signature and bool return. `LOW_LOCK_RISE` is a float
config constant referenced only in the low branch.
