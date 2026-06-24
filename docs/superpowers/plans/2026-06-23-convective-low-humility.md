# Convective Low Humility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the daily-low forecast printing false ~90% confidence on storm days by widening (only) the low's spread when evening convection could still set a new lower minimum before midnight.

**Architecture:** A new best-effort trigger (`convective.py`, backed by `sources/nws_alerts.py` and a new `open_meteo_models.convective_window`) decides whether storm risk is present from point POP/CAPE at KDFW or an active severe-thunderstorm warning in the N/NW approach counties. `model.predict_variable` reads that decision and, for *today's low only*, floors `sigma` at `CONVECTIVE_SIGMA`. The existing `_apply_hard_bound` then deletes all mass above the observed low, so the widening is one-sided (downside) for free.

**Tech Stack:** Python 3, pytest, Open-Meteo Forecast API, NWS `alerts/active` API, existing `sources/common.get_json` disk cache.

## Global Constraints

- **Best-effort:** any data/network failure in the trigger path returns `False` (no widening) and must never raise into a prediction.
- **Scope:** today's low only — `variable == "low"` and `lead_bucket(now, day) == 0`. Never the high, never tomorrow, never a storm-free day.
- **Floor only:** the gate may only *raise* the low's `sigma` (via `max`); never lower it, never shift the consensus/mean.
- **One-sidedness:** rely on the existing `_apply_hard_bound`; the result must carry zero probability above the observed low.
- **Config values (verbatim):** `CONVECTIVE_SIGMA = 3.0`, `CONVECTIVE_POP_MIN = 30`, `CONVECTIVE_CAPE_MIN = 1000`, window `[now, local midnight]`, upstream UGC set includes `"TXC497"` (Wise County).
- All new tests live in `tests/test_convective.py` and are synthetic (no live network), matching the house style in `tests/test_accuracy.py`.

---

### Task 1: Config knobs

**Files:**
- Modify: `config.py` (append after `PEAK_LOCK_DROP`, ~line 98)
- Test: `tests/test_convective.py`

**Interfaces:**
- Produces: `config.CONVECTIVE_SIGMA: float`, `config.CONVECTIVE_POP_MIN: float`, `config.CONVECTIVE_CAPE_MIN: float`, `config.CONVECTIVE_UPSTREAM_UGC: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_convective.py`:

```python
"""Tests for the convective downside-humility trigger and the model sigma gate.
All synthetic — no live network — mirroring tests/test_accuracy.py.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DAY = date(2026, 6, 16)


def test_convective_config_constants():
    assert config.CONVECTIVE_SIGMA >= 2.0
    assert config.CONVECTIVE_POP_MIN > 0
    assert config.CONVECTIVE_CAPE_MIN > 0
    ugc = set(config.CONVECTIVE_UPSTREAM_UGC)
    assert "TXC497" in ugc  # Wise County — the NW approach
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py::test_convective_config_constants -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'CONVECTIVE_SIGMA'`

- [ ] **Step 3: Write minimal implementation**

Append to `config.py` after the `PEAK_LOCK_DROP = 2.0` block:

```python
# --- Convective downside humility (daily low) ---
# Smooth gridded fields can't see a thunderstorm downdraft, so on a storm day the
# model locks to the morning low and over-reports confidence. When evening
# convection could still set a new lower minimum before midnight, we floor the
# low's 1-sigma spread at CONVECTIVE_SIGMA instead of collapsing to observation
# noise. Trigger fires on point POP/CAPE at KDFW OR an active severe-thunderstorm
# warning in the N/NW approach counties (storms move SE toward the metroplex; the
# airport sits on its north side). Widening is one-sided: the hard bound deletes
# all mass above the observed low.
CONVECTIVE_SIGMA = 3.0       # °F floor on today's low spread when storm risk is live
CONVECTIVE_POP_MIN = 30      # % precip probability (remaining hours) that arms the point trigger
CONVECTIVE_CAPE_MIN = 1000   # J/kg CAPE that arms the point trigger

# NWS county UGC codes for the N/NW storm approach to KDFW plus the metro counties
# themselves. A Severe Thunderstorm Warning intersecting this set arms the
# upstream trigger. (TXC + 3-digit county FIPS.)
CONVECTIVE_UPSTREAM_UGC = (
    "TXC497",  # Wise
    "TXC237",  # Jack
    "TXC367",  # Parker
    "TXC363",  # Palo Pinto
    "TXC503",  # Young
    "TXC121",  # Denton
    "TXC097",  # Cooke
    "TXC337",  # Montague
    "TXC439",  # Tarrant (airport county)
    "TXC113",  # Dallas
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py::test_convective_config_constants -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_convective.py
git commit -m "feat: add convective-low humility config knobs"
```

---

### Task 2: `open_meteo_models.convective_window`

**Files:**
- Modify: `sources/open_meteo_models.py` (add after `historical_night_conditions`, ~line 128)
- Test: `tests/test_convective.py`

**Interfaces:**
- Consumes: `config.LAT`, `config.LON`, `config.TIMEZONE`, `sources.common.get_json`, `sources.common.parse_local_times`, `settlement.local_day_bounds` (all already imported in the module).
- Produces:
  - `_window_max(times: list[datetime], pop: list[float|None], cape: list[float|None], day: date, now: datetime) -> tuple[float|None, float|None]` — `(max_pop, max_cape)` over `[now, midnight)` for `day`.
  - `convective_window(day: date, now: datetime, forecast_days: int = 2) -> tuple[float|None, float|None]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def test_window_max_reduces_to_remaining_hours():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    pop = [float(h) for h in range(24)]          # 0..23, increasing
    cape = [100.0 * h for h in range(24)]         # 0..2300
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, pop, cape, DAY, now)
    assert mp == 23.0 and mc == 2300.0            # max over [18:00, midnight)


def test_window_max_empty_window_is_none():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(5)]   # only 00:00-04:00
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, [1.0] * 5, [1.0] * 5, DAY, now)
    assert mp is None and mc is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py::test_window_max_reduces_to_remaining_hours -v`
Expected: FAIL with `ImportError: cannot import name '_window_max'`

- [ ] **Step 3: Write minimal implementation**

Add to `sources/open_meteo_models.py` (after `historical_night_conditions`):

```python
# Remaining-hours convective fields for the daily-low humility trigger.
CONVECTIVE_VARS = "precipitation_probability,cape"


def _window_max(times, pop, cape, day: date, now: datetime):
    """(max_pop, max_cape) over the remaining window [now, midnight) for `day`.

    These are the hours that could still set a new daily low via a storm
    downdraft. (None, None) for whichever field has no points in window."""
    start, end = local_day_bounds(day)
    ps, cs = [], []
    for t, p, c in zip(times, pop, cape):
        t = t.astimezone(start.tzinfo)
        if now <= t < end:
            if p is not None:
                ps.append(p)
            if c is not None:
                cs.append(c)
    return (max(ps) if ps else None, max(cs) if cs else None)


def convective_window(day: date, now: datetime, forecast_days: int = 2):
    """Forecast (max_pop_pct, max_cape) over [now, midnight) for `day` at KDFW."""
    data = get_json(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": CONVECTIVE_VARS,
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    return _window_max(times, hourly["precipitation_probability"],
                       hourly["cape"], day, now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py -k window_max -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add sources/open_meteo_models.py tests/test_convective.py
git commit -m "feat: add convective_window POP/CAPE fetch for remaining-hours low risk"
```

---

### Task 3: `sources/nws_alerts.py` active-alerts feed

**Files:**
- Create: `sources/nws_alerts.py`
- Test: `tests/test_convective.py`

**Interfaces:**
- Consumes: `sources.common.get_json`.
- Produces: `fetch_active(area: str = "TX", ttl: int = 300) -> dict` — raw NWS alerts JSON; `{"features": []}` on any error.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def test_fetch_active_returns_data_on_success(monkeypatch):
    from sources import nws_alerts, common
    payload = {"features": [{"properties": {"event": "Heat Advisory"}}]}
    monkeypatch.setattr(common, "get_json", lambda *a, **k: payload)
    assert nws_alerts.fetch_active() == payload


def test_fetch_active_returns_empty_on_error(monkeypatch):
    from sources import nws_alerts, common

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(common, "get_json", boom)
    assert nws_alerts.fetch_active() == {"features": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py -k fetch_active -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.nws_alerts'`

- [ ] **Step 3: Write minimal implementation**

Create `sources/nws_alerts.py`:

```python
"""Active NWS alerts feed (best-effort) for the convective-low trigger.

One call to api.weather.gov/alerts/active for a state; the caller scans the
returned features for a Severe Thunderstorm Warning intersecting the upstream
counties. Best-effort: any failure yields an empty feature list so a prediction
never breaks on the alerts API.
"""

from __future__ import annotations

from sources.common import get_json

ALERTS_URL = "https://api.weather.gov/alerts/active"


def fetch_active(area: str = "TX", ttl: int = 300) -> dict:
    """Raw active-alerts JSON for `area`. {'features': []} on any error."""
    try:
        return get_json(ALERTS_URL, {"area": area, "status": "actual",
                                     "message_type": "alert"}, ttl=ttl)
    except Exception:
        return {"features": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py -k fetch_active -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add sources/nws_alerts.py tests/test_convective.py
git commit -m "feat: add best-effort NWS active-alerts feed"
```

---

### Task 4: `convective.py` pure decision helpers

**Files:**
- Create: `convective.py`
- Test: `tests/test_convective.py`

**Interfaces:**
- Consumes: `config.CONVECTIVE_POP_MIN`, `config.CONVECTIVE_CAPE_MIN`, `config.CONVECTIVE_UPSTREAM_UGC`.
- Produces:
  - `_point_triggered(pop, cape, pop_min=..., cape_min=...) -> bool`
  - `_upstream_triggered(alerts: dict, zones=UPSTREAM_UGC) -> bool`
  - `risk_label(low_pred: dict) -> str | None`
  - `UPSTREAM_UGC: frozenset[str]`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def test_point_triggered():
    from convective import _point_triggered
    assert _point_triggered(40, 100, pop_min=30, cape_min=1000) is True   # POP over
    assert _point_triggered(10, 1500, pop_min=30, cape_min=1000) is True  # CAPE over
    assert _point_triggered(10, 100, pop_min=30, cape_min=1000) is False  # both under
    assert _point_triggered(None, None, pop_min=30, cape_min=1000) is False


def test_upstream_triggered():
    from convective import _upstream_triggered
    zones = frozenset({"TXC497", "TXC237"})
    svr = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning",
        "geocode": {"UGC": ["TXC497", "TXC367"]}}}]}
    assert _upstream_triggered(svr, zones) is True
    # right counties, wrong event
    flood = {"features": [{"properties": {
        "event": "Flood Warning", "geocode": {"UGC": ["TXC497"]}}}]}
    assert _upstream_triggered(flood, zones) is False
    # right event, counties outside the approach set
    far = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning", "geocode": {"UGC": ["TXC999"]}}}]}
    assert _upstream_triggered(far, zones) is False
    assert _upstream_triggered({}, zones) is False


def test_risk_label():
    from convective import risk_label
    assert risk_label({"convective_widened": True}) is not None
    assert risk_label({"convective_widened": False}) is None
    assert risk_label({}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py -k "point_triggered or upstream_triggered or risk_label" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'convective'`

- [ ] **Step 3: Write minimal implementation**

Create `convective.py` (orchestration `convective_risk` added in Task 5):

```python
"""Convective downside trigger for today's daily low.

The smooth gridded fields the model ingests cannot see a thunderstorm
downdraft, so on a storm day the model locks to the morning low and reports
false high confidence. This module decides, best-effort, whether evening
convection could still set a new lower minimum before midnight — from point
POP/CAPE at KDFW or an active severe-thunderstorm warning in the N/NW approach
counties. model.py uses the decision to floor the low's spread.
"""

from __future__ import annotations

from config import (CONVECTIVE_CAPE_MIN, CONVECTIVE_POP_MIN,
                    CONVECTIVE_UPSTREAM_UGC)

UPSTREAM_UGC = frozenset(CONVECTIVE_UPSTREAM_UGC)
_SEVERE = "Severe Thunderstorm Warning"


def _point_triggered(pop, cape, pop_min=CONVECTIVE_POP_MIN,
                     cape_min=CONVECTIVE_CAPE_MIN) -> bool:
    """True when remaining-hours POP or CAPE clears its arming threshold."""
    return ((pop is not None and pop >= pop_min)
            or (cape is not None and cape >= cape_min))


def _upstream_triggered(alerts: dict, zones=UPSTREAM_UGC) -> bool:
    """True when an active Severe Thunderstorm Warning intersects `zones`."""
    for f in (alerts or {}).get("features", []):
        props = f.get("properties", {}) or {}
        if props.get("event") != _SEVERE:
            continue
        ugc = (props.get("geocode", {}) or {}).get("UGC", []) or []
        if zones.intersection(ugc):
            return True
    return False


def risk_label(low_pred: dict) -> str | None:
    """Dashboard caption when the low's spread was convectively widened."""
    if (low_pred or {}).get("convective_widened"):
        return ("⚡ Convective risk — evening storms could set a new low; "
                "confidence on the low has been widened.")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py -k "point_triggered or upstream_triggered or risk_label" -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add convective.py tests/test_convective.py
git commit -m "feat: add convective trigger decision helpers"
```

---

### Task 5: `convective.convective_risk` orchestration

**Files:**
- Modify: `convective.py`
- Test: `tests/test_convective.py`

**Interfaces:**
- Consumes: `sources.open_meteo_models.convective_window` (Task 2), `sources.nws_alerts.fetch_active` (Task 3), `_point_triggered`, `_upstream_triggered` (Task 4).
- Produces: `convective_risk(day: date, now: datetime) -> bool`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def test_convective_risk_ors_signals_and_is_best_effort(monkeypatch):
    import convective
    from sources import nws_alerts, open_meteo_models
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    no_alerts = {"features": []}
    one_zone = list(convective.UPSTREAM_UGC)[0]
    svr = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning", "geocode": {"UGC": [one_zone]}}}]}

    # point signal alone fires
    monkeypatch.setattr(open_meteo_models, "convective_window", lambda d, n: (50.0, 200.0))
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: no_alerts)
    assert convective.convective_risk(DAY, now) is True

    # upstream signal alone fires (point quiet)
    monkeypatch.setattr(open_meteo_models, "convective_window", lambda d, n: (0.0, 0.0))
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: svr)
    assert convective.convective_risk(DAY, now) is True

    # neither
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: no_alerts)
    assert convective.convective_risk(DAY, now) is False

    # any exception -> False (best-effort)
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(open_meteo_models, "convective_window", boom)
    monkeypatch.setattr(nws_alerts, "fetch_active", boom)
    assert convective.convective_risk(DAY, now) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py::test_convective_risk_ors_signals_and_is_best_effort -v`
Expected: FAIL with `AttributeError: module 'convective' has no attribute 'convective_risk'`

- [ ] **Step 3: Write minimal implementation**

Add to `convective.py`. First extend the import line, then append the function:

```python
from datetime import date, datetime

from sources import nws_alerts, open_meteo_models
```

```python
def convective_risk(day: date, now: datetime) -> bool:
    """True if evening convection could push today's low lower before midnight.

    Best-effort: each signal is guarded independently, and any data/network
    failure contributes no risk (returns without raising). Point POP/CAPE OR an
    upstream severe-thunderstorm warning is sufficient."""
    try:
        pop, cape = open_meteo_models.convective_window(day, now)
        if _point_triggered(pop, cape):
            return True
    except Exception:
        pass
    try:
        if _upstream_triggered(nws_alerts.fetch_active()):
            return True
    except Exception:
        pass
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py::test_convective_risk_ors_signals_and_is_best_effort -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convective.py tests/test_convective.py
git commit -m "feat: add convective_risk orchestration (point OR upstream, best-effort)"
```

---

### Task 6: `model.py` sigma-floor gate (live-only)

**Files:**
- Modify: `model.py` (import block ~line 23-26; `predict_variable` signature ~line 317, body after the settlement-gap widening ~line 422, return dict ~line 430; `_predict_from` ~line 519; `predict` ~line 516; `snapshot` ~line 564-565)
- Test: `tests/test_convective.py`

**Interfaces:**
- Consumes: `convective.convective_risk` (Task 5), `config.CONVECTIVE_SIGMA` (Task 1), `lead_bucket` (already imported).
- Produces:
  - `predict_variable(..., live: bool = False)` — return dict gains `"convective_widened": bool`; `model.convective_risk` is monkeypatchable in tests.
  - `_predict_from(..., live: bool = False)` threads the flag to both high/low calls.
  - `predict(...)` and `snapshot(...)` pass `live=True` (the genuine live paths). `backtest` keeps the default `False`, so replay never triggers the live trigger.

**Why the `live` flag:** the trigger reads live POP/CAPE and live NWS alerts, valid only for the real current day. `backtest.run_intraday` (backtest.py:249) calls `predict_variable` with a *today-relative* `now` while replaying *past* days; without the flag the gate would fire a spurious, semantically-wrong network call there (and break `test_run_intraday_anchors_to_observations`). `live` defaults `False` so all direct/replay callers are no-ops.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def _locked_low_inputs():
    """Obs V-shape: low 79 at 05:00, risen to 90 by 16:00 (low locked), plus
    three full-day forecast members with mins straddling 79."""
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    ot = [base + timedelta(hours=h) for h in range(17)]
    ov = [79 + abs(h - 5) for h in range(17)]          # 84..79..90
    ftimes = [base + timedelta(hours=h) for h in range(24)]
    fc = {f"det_{i}": (ftimes, [79 + m + abs(h - 5) for h in range(24)])
          for i, m in enumerate((-1, 0, 1))}
    return fc, {"obs": (ot, ov)}, base


def test_convective_widens_locked_low(monkeypatch):
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)

    monkeypatch.setattr(model, "convective_risk", lambda day, now: False)
    off = model.predict_variable(fc, obs, DAY, "low", now, None, live=True)
    monkeypatch.setattr(model, "convective_risk", lambda day, now: True)
    on = model.predict_variable(fc, obs, DAY, "low", now, None, live=True)

    # sanity: the low is locked in both runs
    assert off["peak_locked"] and on["peak_locked"]
    # the flag is set only when risk is live
    assert on["convective_widened"] and not off["convective_widened"]
    # confidence loosens: spread widens to the convective floor
    assert on["sigma_used"] > off["sigma_used"]
    assert on["sigma_used"] >= config.CONVECTIVE_SIGMA - 1e-9
    # one-sided: zero mass above the observed low (79) either way
    assert model.prob_at_least(on["probabilities"], 80) < 1e-9
    assert model.prob_at_least(off["probabilities"], 80) < 1e-9
    # real downside mass appears at/below 77
    assert model.prob_at_most(on["probabilities"], 77) > model.prob_at_most(off["probabilities"], 77)
    # consensus (mean) is unchanged — only spread moved
    assert on["consensus"] == off["consensus"]


def test_convective_does_not_touch_high(monkeypatch):
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    monkeypatch.setattr(model, "convective_risk", lambda day, now: True)
    hi_on = model.predict_variable(fc, obs, DAY, "high", now, None, live=True)
    monkeypatch.setattr(model, "convective_risk", lambda day, now: False)
    hi_off = model.predict_variable(fc, obs, DAY, "high", now, None, live=True)
    assert hi_on["probabilities"] == hi_off["probabilities"]
    assert hi_on["convective_widened"] is False


def test_convective_no_op_when_not_live(monkeypatch):
    # The default (non-live) path — what backtest/replay uses — must never call
    # convective_risk, even for today's low.
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)

    def boom(day, now):
        raise AssertionError("convective_risk must not run when live=False")

    monkeypatch.setattr(model, "convective_risk", boom)
    out = model.predict_variable(fc, obs, DAY, "low", now, None)   # live defaults False
    assert out["convective_widened"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convective.py -k "widens_locked_low or does_not_touch_high" -v`
Expected: FAIL with `KeyError: 'convective_widened'` (or `AttributeError` on `model.convective_risk`)

- [ ] **Step 3: Write minimal implementation**

In `model.py`, extend the config import (currently ends `bin_labels, lead_bucket)` at line 25) to add `CONVECTIVE_SIGMA`:

```python
from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    CONVECTIVE_SIGMA, LEAD_SIGMA_INFLATION, PEAK_LOCK_DROP,
                    TIMEZONE, bin_labels, lead_bucket)
```

Add a new import after the `settlement` import (line 26):

```python
from convective import convective_risk
```

Add a `live` parameter to the `predict_variable` signature (currently
`def predict_variable(series, obs_series, day, variable, now, calib, settle_offset=None):`):

```python
def predict_variable(series, obs_series, day, variable, now, calib,
                     settle_offset=None, live=False):
```

In `predict_variable`, immediately after the settlement-gap widening block (the `if settle_gap_std: sigma = math.hypot(...)` at ~line 421-422) and before `probs = _bin_probabilities(...)`:

```python
    # Convective downside humility: on a storm-risk day the smooth fields can't
    # see an evening downdraft, so a locked low collapses sigma to ~0.7 and
    # over-reports confidence. Floor the spread at CONVECTIVE_SIGMA for *today's
    # low only*; the hard bound below then makes the extra spread one-sided
    # (downside). Best-effort and floor-only: it never lowers sigma, shifts the
    # mean, or touches the high/tomorrow. Storm-free days never trigger. Gated on
    # `live`: the trigger reads live POP/CAPE and live alerts, so it must not fire
    # in backtest/replay (which calls this with a today-relative `now` on a past
    # day).
    convective_widened = False
    if live and variable == "low" and now is not None and lead_bucket(now, day) == 0:
        try:
            if convective_risk(day, now):
                sigma = max(sigma, CONVECTIVE_SIGMA)
                convective_widened = True
        except Exception:
            pass
```

Add the flag to the return dict (after `"peak_locked": locked,`):

```python
        "convective_widened": convective_widened,
```

Thread `live` through `_predict_from` (currently
`def _predict_from(series, obs, day, now, calib, settle_offset=None):`):

```python
def _predict_from(series, obs, day, now, calib, settle_offset=None, live=False):
    return {
        "day": day.isoformat(),
        "high": predict_variable(series, obs, day, "high", now, calib, settle_offset, live=live),
        "low": predict_variable(series, obs, day, "low", now, calib, settle_offset, live=live),
    }
```

In `predict` (line ~516), pass `live=True` (the live single-day entry point):

```python
    return _predict_from(series, obs, day, now, calib, settle_offset, live=True)
```

In `snapshot` (the today/tomorrow block, ~line 564-565), pass `live=True` to both
(`tomorrow` naturally no-ops because its `lead_bucket != 0`):

```python
        "today": _predict_from(series, obs, today, now, calib, settle_offset, live=True),
        "tomorrow": _predict_from(series, obs, tomorrow, now, calib, settle_offset, live=True),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convective.py -k "widens_locked_low or does_not_touch_high or no_op_when_not_live" -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: all pass. The `live=False` default keeps every existing caller a no-op: `backtest.run_intraday` (backtest.py:249) calls `predict_variable` without `live`, so `test_run_intraday_anchors_to_observations` makes no convective network call; the `test_conditional_offset.py` and `test_cli_basis.py` low tests pass `now=None`, which the gate also skips. No existing test needs editing.

- [ ] **Step 6: Commit**

```bash
git add model.py tests/test_convective.py
git commit -m "feat: floor today's low sigma on convective risk (live-only, one-sided via hard bound)"
```

---

### Task 7: Dashboard visibility caption

**Files:**
- Modify: `market_view.py` (the `variable == "low"` branch, after the `cooling_applied` caption at ~line 243-244)
- Test: `tests/test_convective.py` (the pure `risk_label` is already covered in Task 4; this task wires it into the panel and verifies the call site renders nothing when the flag is absent)

**Interfaces:**
- Consumes: `convective.risk_label` (Task 4); the low prediction dict carrying `convective_widened` (Task 6).
- Produces: a `st.caption` under the low panel when convective widening is active.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_convective.py`:

```python
def test_risk_label_matches_model_flag():
    # End-to-end glue: a low prediction with the flag set yields a caption; a
    # plain one yields nothing. Guards against the panel reading the wrong key.
    from convective import risk_label
    assert risk_label({"convective_widened": True, "consensus": 77}) is not None
    assert risk_label({"convective_widened": False, "consensus": 77}) is None
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `pytest tests/test_convective.py::test_risk_label_matches_model_flag -v`
Expected: PASS (the helper exists from Task 4). This test pins the contract the panel relies on; proceed to wire the call site.

- [ ] **Step 3: Wire the caption into the panel**

In `market_view.py`, locate the low-branch cooling caption (~line 243-244):

```python
        if d.get("cooling_applied"):
            st.caption("🌙 Clear/calm night — extra radiational-cooling offset "
```

Immediately after that `st.caption(...)` statement (still inside the `variable == "low"` branch), add:

```python
        from convective import risk_label
        _conv = risk_label(d)
        if _conv:
            st.caption(_conv)
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Manual smoke check (optional, no assertion)**

Run: `python -c "import market_view, convective; print(convective.risk_label({'convective_widened': True}))"`
Expected: prints the `⚡ Convective risk …` string (confirms the import wiring resolves).

- [ ] **Step 6: Commit**

```bash
git add market_view.py tests/test_convective.py
git commit -m "feat: surface convective-low widening in the dashboard low panel"
```

---

## Self-Review

**Spec coverage:**
- Convective sigma floor for today's low → Task 6. ✓
- Point POP/CAPE trigger → Tasks 2 (fetch) + 4 (`_point_triggered`). ✓
- Upstream severe-warning trigger via curated UGC list → Tasks 1 (list) + 3 (feed) + 4 (`_upstream_triggered`). ✓
- One-sidedness via existing hard bound → asserted in Task 6 (`prob_at_least(..., 80) < 1e-9`). ✓
- Best-effort / no-raise → Tasks 3, 5, 6 each guard and assert the failure path. ✓
- Floor-only, mean unchanged → Task 6 asserts `consensus` equal and `sigma_used` strictly greater. ✓
- High untouched → Task 6 `test_convective_does_not_touch_high`. ✓
- Storm-free day unchanged → covered by the `off` path in Task 6 plus the gate condition; existing suite green in Task 6 Step 5. ✓
- Visibility line → Task 7. ✓
- Scope: today's low only → gate condition `variable == "low" and lead_bucket(now, day) == 0` in Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. ✓

**Type consistency:** `convective_window`/`_window_max` return `(float|None, float|None)` consumed by `_point_triggered` (handles `None`). `fetch_active` returns `dict` consumed by `_upstream_triggered`. `convective_risk(day, now) -> bool` consumed by the `model.py` gate. `risk_label(dict) -> str|None` consumed by the panel. `convective_widened` written in Task 6, read in Task 4's `risk_label` and Task 7's call site — consistent key. The `live` flag added in Task 6 is keyword-threaded (`live=...`) everywhere, so the existing positional `settle_offset` arguments are unaffected. ✓

**Replay/network safety:** The `live` flag (default `False`) is what keeps backtests and every existing direct `predict_variable` caller free of live network calls — verified against the three real call sites (backtest.py:249 with no `live`; `test_conditional_offset.py` / `test_cli_basis.py` low calls with `now=None`). Only `predict()` and `snapshot()` pass `live=True`. ✓
