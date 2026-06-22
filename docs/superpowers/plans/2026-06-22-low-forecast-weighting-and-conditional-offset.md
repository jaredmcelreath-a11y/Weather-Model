# Skill-weighted consensus + conditional settlement offset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve KDFW low-temp accuracy on the Kalshi/CLI basis via (1) a skill-weighted, group-rebalanced forecast consensus and (2) a two-bucket (clear-calm vs other) conditional settlement offset — each gated to fall back to current behavior when it doesn't win out-of-sample.

**Architecture:** Calibration computes per-system weights (one combined ensemble-mean + 5 deterministic + neutral NWS, inverse-MAE with strong shrinkage λ=0.25, walk-forward OOS-gated) and a bucketed settlement offset (gated). The model reads both from `calibration.json` and consumes them in a weighted consensus / weighted Gaussian-mixture and per-night bucket selection. All new shapes are additive: when calibration emits nothing new (or weights are uniform / offset is flat), behavior is byte-identical to today, so the existing test suite stays green.

**Tech Stack:** Python 3.9+ (stdlib only: `math`, `csv`, `datetime`), pytest, Open-Meteo forecast/ensemble/historical APIs (via `sources/common.get_json`), IEM ASOS archive.

**Spec:** `docs/superpowers/specs/2026-06-22-low-forecast-weighting-and-conditional-offset-design.md`

---

## File map

- `calibration.py` — Modify: add `_conditional_settlement_offset`, `_system_extremes`, `_system_weights`; wire both into `compute()`.
- `model.py` — Modify: `_bin_probabilities` (optional weights), `_collect_samples` (return values+weights), `_sample_weights` (new), `_offset_bucket` (new), `predict_variable` (weighted consensus + bucket selection).
- `sources/open_meteo_ensemble.py` — Modify: add `fetch_historical`.
- `backtest.py` — Modify: bucketed-offset support; apply system weights when calibration provides them.
- `tests/test_weighting.py` — Create.
- `tests/test_conditional_offset.py` — Create.

Test command for the whole suite: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest -q`

---

## PHASE A — Conditional settlement offset (#2)

### Task A1: `_conditional_settlement_offset` in calibration

**Files:**
- Modify: `calibration.py`
- Test: `tests/test_conditional_offset.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_conditional_offset.py`:

```python
"""Two-bucket (clear-calm vs other) conditional settlement offset + model use."""
from datetime import date

from calibration import _conditional_settlement_offset


def _days(n, start=date(2026, 5, 1)):
    from datetime import timedelta
    return [start + timedelta(days=i) for i in range(n)]


def test_emits_buckets_when_low_gap_differs_and_enough_nights():
    # 8 clear/calm nights with low gap -0.8, 8 other nights with low gap -0.2.
    days = _days(16)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 8
        low_gap = -0.8 if clear else -0.2
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + low_gap)        # high gap +1 both buckets
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    off = _conditional_settlement_offset(cli, hourly, cond)
    assert off is not None
    assert round(off["low"]["clear_calm"], 2) == -0.8
    assert round(off["low"]["other"], 2) == -0.2
    assert "clear_calm_std" in off["low"] and "other_std" in off["low"]
    # high gap is identical in both buckets -> high gate fails -> equal buckets
    assert off["high"]["clear_calm"] == off["high"]["other"] == 1.0


def test_returns_none_when_too_few_clear_calm_nights():
    days = _days(10)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 3                          # only 3 clear/calm (< 5)
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.8 if clear else -0.2))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_buckets_too_similar():
    # plenty of clear/calm nights but the gap barely differs -> no value in split
    days = _days(16)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 8
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.45 if clear else -0.40))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py -q`
Expected: FAIL with `ImportError: cannot import name '_conditional_settlement_offset'`.

- [ ] **Step 3: Implement the function**

In `calibration.py`, add after the existing `_settlement_offset` function:

```python
def _var_bucket(gaps_cc, gaps_ot, min_nights, margin, min_sep):
    """Per-variable bucket means/stds + whether the split is worth keeping.

    Returns (cc_mean, ot_mean, cc_std, ot_std, passed). `passed` is True only
    when there are >= min_nights clear/calm nights, the two bucket means differ
    by at least `min_sep` degrees (so a near-identical split is rejected), AND
    splitting reduces the mean absolute residual vs a single flat mean by at
    least `margin`. The separation guard is what makes "buckets too similar"
    fall back to flat — with real within-bucket noise the residual check alone
    is not enough.
    """
    n_cc = len(gaps_cc)
    all_gaps = gaps_cc + gaps_ot
    flat = sum(all_gaps) / len(all_gaps)
    cc_mean, cc_std = _mean_std(gaps_cc) if gaps_cc else (flat, 0.0)
    ot_mean, ot_std = _mean_std(gaps_ot) if gaps_ot else (flat, 0.0)
    resid_flat = sum(abs(g - flat) for g in all_gaps) / len(all_gaps)
    resid_cond = (sum(abs(g - cc_mean) for g in gaps_cc)
                  + sum(abs(g - ot_mean) for g in gaps_ot)) / len(all_gaps)
    passed = (n_cc >= min_nights
              and abs(cc_mean - ot_mean) >= min_sep
              and resid_cond <= resid_flat - margin)
    if not passed:
        return flat, flat, 0.0, 0.0, False
    return cc_mean, ot_mean, cc_std, ot_std, True


def _conditional_settlement_offset(cli, hourly, cond, min_nights=5, margin=0.02,
                                   min_sep=0.25):
    """Bucketed (clear_calm/other) CLI-hourly offset, or None to use the flat one.

    Splits the per-day gap by overnight conditions (cloud<CLEAR_CLOUD_MAX and
    wind<CALM_WIND_MAX). Returns the bucketed dict only if at least one variable's
    split is worth keeping (see `_var_bucket`); otherwise None so the caller falls
    back to the flat `_settlement_offset`.
    """
    cc = {"high": [], "low": []}
    ot = {"high": [], "low": []}
    for day, (chi, clo) in cli.items():
        if day not in hourly or day not in cond:
            continue
        hhi, hlo = hourly[day]
        cloud, wind = cond[day]
        bucket = cc if (cloud < CLEAR_CLOUD_MAX and wind < CALM_WIND_MAX) else ot
        bucket["high"].append(chi - hhi)
        bucket["low"].append(clo - hlo)
    if not cc["low"] and not ot["low"]:
        return None
    out = {}
    any_passed = False
    for var in ("high", "low"):
        cm, om, cs, os_, passed = _var_bucket(cc[var], ot[var], min_nights,
                                              margin, min_sep)
        any_passed = any_passed or passed
        out[var] = {"clear_calm": cm, "other": om,
                    "clear_calm_std": cs, "other_std": os_}
    if not any_passed:
        return None
    out["n_days"] = len(cc["high"]) + len(ot["high"])
    out["n_clear_calm"] = len(cc["high"])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_conditional_offset.py
git commit -m "calibration: two-bucket conditional settlement offset (gated)"
```

---

### Task A2: model reads the bucketed offset shape

**Files:**
- Modify: `model.py` (imports near line 23; `predict_variable` offset blocks ~lines 255-290)
- Test: `tests/test_conditional_offset.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conditional_offset.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


_BUCKETED = {"high": {"clear_calm": 0.0, "other": 0.0,
                      "clear_calm_std": 0.0, "other_std": 0.0},
             "low": {"clear_calm": -0.8, "other": -0.2,
                     "clear_calm_std": 0.0, "other_std": 0.0}}


def test_model_picks_clear_calm_bucket(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (10.0, 5.0))           # clear + calm
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    # unshifted low consensus is peak-15 -> mean(75,77)=76; clear/calm shift -0.8
    assert out["consensus"] == 75.2


def test_model_picks_other_bucket(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (90.0, 25.0))          # cloudy + windy
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # 76 - 0.2


def test_model_other_bucket_when_conditions_unavailable(monkeypatch):
    day = date(2030, 7, 1)
    def boom(d):
        raise RuntimeError("no network")
    monkeypatch.setattr(model.open_meteo_models, "night_conditions", boom)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # falls back to 'other'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py -q`
Expected: FAIL — bucketed dict hits `settle_offset.get(variable, 0.0)` returning a dict, breaking the arithmetic.

- [ ] **Step 3: Implement bucket-aware offset reading**

In `model.py`, add the two config names to the existing config import (line 23-24), so it reads:

```python
from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    LEAD_SIGMA_INFLATION, TIMEZONE, bin_labels, lead_bucket)
```

Add this helper just above `predict_variable` (before line 214):

```python
def _offset_bucket(settle_offset, variable, day, calib):
    """(shift, gap_std) for `variable` from a settlement-offset spec.

    Accepts the flat shape ({var: float, var_std: float}) and the bucketed shape
    ({var: {clear_calm, other, clear_calm_std, other_std}}). For the bucketed
    shape, the bucket is chosen from the overnight forecast conditions for `day`,
    defaulting to 'other' when conditions can't be fetched.
    """
    spec = (settle_offset or {}).get(variable)
    if isinstance(spec, dict):
        cool = (calib or {}).get("cooling") or {}
        ct = cool.get("cloud_thresh", CLEAR_CLOUD_MAX)
        wt = cool.get("wind_thresh", CALM_WIND_MAX)
        bucket = "other"
        try:
            cloud, wind = open_meteo_models.night_conditions(day)
            if cloud is not None and cloud < ct and wind < wt:
                bucket = "clear_calm"
        except Exception:
            pass
        return spec.get(bucket, 0.0), spec.get(f"{bucket}_std", 0.0)
    return ((settle_offset or {}).get(variable, 0.0),
            (settle_offset or {}).get(f"{variable}_std", 0.0))
```

Replace the offset shift block (currently lines ~260-264):

```python
    if settle_offset:
        off = settle_offset.get(variable, 0.0)
        if off:
            samples = [s + off for s in samples]
            fullday = [s + off for s in fullday]
```

with:

```python
    settle_shift, settle_gap_std = _offset_bucket(settle_offset, variable, day, calib)
    if settle_shift:
        samples = [s + settle_shift for s in samples]
        fullday = [s + settle_shift for s in fullday]
```

Replace the gap-std widening block (currently lines ~287-290):

```python
    if settle_offset:
        gap_std = settle_offset.get(f"{variable}_std", 0.0)
        if gap_std:
            sigma = math.hypot(sigma, gap_std)
```

with:

```python
    if settle_gap_std:
        sigma = math.hypot(sigma, settle_gap_std)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py tests/test_cli_basis.py -q`
Expected: all pass (the flat-shape `test_cli_basis.py` cases still work via the `_offset_bucket` flat branch).

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_conditional_offset.py
git commit -m "model: select clear-calm/other settlement-offset bucket per night"
```

---

### Task A3: wire the conditional offset into `calibration.compute()`

**Files:**
- Modify: `calibration.py` (`compute()`, ~lines 123-139)
- Test: covered by Task A1 + a live smoke check

- [ ] **Step 1: Edit `compute()` to prefer the conditional offset**

In `calibration.py`, inside `compute()`, replace the settlement-offset line in the returned dict (currently `"settlement_offset": _settlement_offset(cli_actual, actual),`) by first computing it above the `return`:

```python
    try:
        cond = open_meteo_models.historical_night_conditions(start, end)
    except Exception:
        cond = {}
    settlement_offset = _conditional_settlement_offset(cli_actual, actual, cond) \
        or _settlement_offset(cli_actual, actual)
```

and change the dict entry to:

```python
        "settlement_offset": settlement_offset,
```

- [ ] **Step 2: Smoke-test the live calibration**

Run:
```bash
.venv/bin/python -c "import calibration, json; print(json.dumps(calibration.compute()['settlement_offset'], indent=2))"
```
Expected: a `settlement_offset` block — either bucketed (`low` has `clear_calm`/`other`) if the gate passed on real data, or the flat shape otherwise. No exception either way.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add calibration.py
git commit -m "calibration: emit conditional settlement offset, fall back to flat"
```

---

### Task A4: backtest honors the bucketed offset

**Files:**
- Modify: `backtest.py` (`run`, ~lines 118-157)
- Test: `tests/test_conditional_offset.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conditional_offset.py`:

```python
import backtest
import calibration
from sources import open_meteo_models, station_history


def test_backtest_applies_bucketed_offset_per_day(monkeypatch):
    d_clear = date(2026, 6, 10)
    d_cloud = date(2026, 6, 11)
    series = {"det_a": _member(d_clear, 90.0), "det_b": _member(d_cloud, 90.0)}
    # one series spanning both days
    base = {}
    for d in (d_clear, d_cloud):
        t, v = _member(d, 90.0)
        base.setdefault("det_a", ([], []))
        base["det_a"] = (base["det_a"][0] + t, base["det_a"][1] + v)
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: base)
    monkeypatch.setattr(open_meteo_models, "historical_night_conditions",
                        lambda s, e: {d_clear: (10.0, 5.0), d_cloud: (90.0, 25.0)})
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {d_clear: (90.0, 75.0), d_cloud: (90.0, 75.0)})
    # CLI low truth equals the bucketed shift applied to the hourly low (75):
    #   clear/calm -> 75-0.8=74.2 ; other -> 75-0.2=74.8
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {d_clear: (91.0, 74.2), d_cloud: (91.0, 74.8)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    off = {"high": {"clear_calm": 1.0, "other": 1.0,
                    "clear_calm_std": 0.0, "other_std": 0.0},
           "low": {"clear_calm": -0.8, "other": -0.2,
                   "clear_calm_std": 0.0, "other_std": 0.0}}
    res = backtest.run(cli=True, settle_offset=off)
    assert res["low"]["mae"] == 0.0          # each day's shift matches its CLI low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py::test_backtest_applies_bucketed_offset_per_day -q`
Expected: FAIL — current backtest does `(settle_offset or {}).get(var, 0.0)`, which returns a dict for the bucketed shape and raises in the arithmetic.

- [ ] **Step 3: Implement bucketed-offset support in `backtest.run`**

In `backtest.py`, at the top of `run` after `actual = ...` and `series = ...` (around line 123), add a per-day condition lookup only when needed:

```python
    bucketed = cli and isinstance((settle_offset or {}).get("high"), dict)
    cond = {}
    if bucketed:
        try:
            cond = open_meteo_models.historical_night_conditions(start, end)
        except Exception:
            cond = {}
```

Then replace, inside the `for var in (...)` loop, the offset/std setup (currently lines ~131-133):

```python
        off = (settle_offset or {}).get(var, 0.0) if cli else 0.0
        if cli:
            sigma = math.hypot(sigma, (settle_offset or {}).get(f"{var}_std", 0.0))
```

with a flat-vs-bucketed branch that resolves per day inside the day loop. Concretely, remove those two lines and add a helper just inside `run` (above the `for var` loop):

```python
    def _offset_for(var, day):
        spec = (settle_offset or {}).get(var) if cli else 0.0
        if isinstance(spec, dict):
            cloud, wind = cond.get(day, (100.0, 100.0))
            from config import CLEAR_CLOUD_MAX, CALM_WIND_MAX
            b = "clear_calm" if (cloud < CLEAR_CLOUD_MAX and wind < CALM_WIND_MAX) else "other"
            return spec.get(b, 0.0), spec.get(f"{b}_std", 0.0)
        return (spec or 0.0), ((settle_offset or {}).get(f"{var}_std", 0.0) if cli else 0.0)
```

In the `for var in ("high", "low"):` block, change the sigma init so the std is applied per day (move it into the day loop). Replace:

```python
        sigma = max(sigma_cfg.get(var) or 3.0, _MIN_SIGMA)
        off = (settle_offset or {}).get(var, 0.0) if cli else 0.0
        if cli:
            sigma = math.hypot(sigma, (settle_offset or {}).get(f"{var}_std", 0.0))
```

with:

```python
        sigma_base = max(sigma_cfg.get(var) or 3.0, _MIN_SIGMA)
```

and inside the `for day, (act_hi, act_lo) in actual.items():` loop, replace:

```python
            corrected = [s - bias.get(var, 0.0) + off for s in samples]
            probs = _bin_probabilities(corrected, sigma)
```

with:

```python
            off, gap_std = _offset_for(var, day)
            sigma = math.hypot(sigma_base, gap_std) if gap_std else sigma_base
            corrected = [s - bias.get(var, 0.0) + off for s in samples]
            probs = _bin_probabilities(corrected, sigma)
```

Note: the baseline block further down still uses `samples`/3.0 and is unaffected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py tests/test_cli_basis.py -q`
Expected: all pass (flat-offset `test_cli_basis.py` cases use the non-dict branch of `_offset_for`).

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_conditional_offset.py
git commit -m "backtest: per-day bucketed settlement offset on the CLI basis"
```

---

## PHASE B — Skill-weighted consensus (#1)

### Task B1: historical ensemble fetch

**Files:**
- Modify: `sources/open_meteo_ensemble.py`
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_weighting.py`:

```python
"""Skill-weighted, group-rebalanced consensus."""
from datetime import date

from sources import open_meteo_ensemble
from sources import common


def test_fetch_historical_parses_members(monkeypatch):
    fake = {"hourly": {
        "time": ["2026-05-01T00:00", "2026-05-01T01:00"],
        "temperature_2m_member01_ncep_gefs_seamless": [70.0, 71.0],
        "temperature_2m_member02_ncep_gefs_seamless": [69.0, 72.0],
        "temperature_2m": [70.5, 71.5],   # control column
    }}
    monkeypatch.setattr(common, "get_json", lambda *a, **k: fake)
    monkeypatch.setattr(open_meteo_ensemble, "get_json", lambda *a, **k: fake)
    out = open_meteo_ensemble.fetch_historical(date(2026, 5, 1), date(2026, 5, 1))
    assert "ens_member01_ncep_gefs_seamless" in out
    assert "ens_control" in out
    times, temps = out["ens_member01_ncep_gefs_seamless"]
    assert len(times) == 2 and temps == [70.0, 71.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: FAIL with `AttributeError: module 'sources.open_meteo_ensemble' has no attribute 'fetch_historical'`.

- [ ] **Step 3: Implement `fetch_historical`**

In `sources/open_meteo_ensemble.py`, change the import line to also bring in the date type and factor the member parsing out of `fetch`:

```python
from datetime import date, datetime
```

Add a shared parser and the historical fetch (the ensemble archive is reachable via `start_date`/`end_date`):

```python
def _parse(data: dict) -> dict[str, tuple[list[datetime], list[float]]]:
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "ens_") if key != "temperature_2m" else "ens_control"
        out[label] = (times, values)
    return out


def fetch_historical(start: date, end: date,
                     ttl: int = 24 * 3600) -> dict[str, tuple[list[datetime], list[float]]]:
    """Archived ensemble members over [start, end] for skill weighting."""
    data = get_json(URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(ENSEMBLE_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }, ttl=ttl)
    return _parse(data)
```

And change `fetch` to reuse `_parse` (replace its trailing manual loop with `return _parse(data)`):

```python
def fetch(forecast_days: int = 2) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {member_label: (times, temps_f)} across all ensemble systems."""
    data = get_json(URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(ENSEMBLE_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _parse(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add sources/open_meteo_ensemble.py tests/test_weighting.py
git commit -m "ensemble: add historical member fetch for skill weighting"
```

---

### Task B2: weighted `_bin_probabilities` (backward compatible)

**Files:**
- Modify: `model.py` (`_bin_probabilities`, ~lines 119-156)
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_weighting.py`:

```python
import model


def test_bin_probabilities_uniform_weights_match_unweighted():
    samples = [88.0, 90.0, 92.0]
    a = model._bin_probabilities(samples, 2.0)
    b = model._bin_probabilities(samples, 2.0, weights=[1.0, 1.0, 1.0])
    assert a == b


def test_bin_probabilities_weight_shifts_mass():
    samples = [85.0, 95.0]
    low_heavy = model._bin_probabilities(samples, 2.0, weights=[9.0, 1.0])
    high_heavy = model._bin_probabilities(samples, 2.0, weights=[1.0, 9.0])
    assert model.prob_at_least(low_heavy, 95) < model.prob_at_least(high_heavy, 95)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: FAIL — `_bin_probabilities` takes no `weights` keyword.

- [ ] **Step 3: Implement weighted mixture**

In `model.py`, replace the whole `_bin_probabilities` function with:

```python
def _bin_probabilities(samples, target_sigma, weights=None):
    """Gaussian-mixture density over weighted samples -> probability per bin.

    `weights` are per-sample mixture weights (default uniform). The ensemble
    members supply the shape; the total spread is pinned to `target_sigma` by
    scaling samples about their weighted mean with a fixed bandwidth, so total
    variance == target_sigma^2 regardless of the raw spread. Uniform weights
    reproduce the unweighted result exactly.
    """
    n = len(samples)
    if weights is None:
        weights = [1.0] * n
    W = sum(weights) or 1.0
    mean = sum(w * s for w, s in zip(weights, samples)) / W
    raw_var = sum(w * (s - mean) ** 2 for w, s in zip(weights, samples)) / W
    bw = _MIN_BANDWIDTH
    needed = target_sigma ** 2 - bw ** 2
    if needed <= 0 or raw_var < 1e-6:
        samples = [mean]
        weights = [1.0]
        W = 1.0
        bw = max(target_sigma, _MIN_BANDWIDTH)
    else:
        alpha = math.sqrt(needed / raw_var)
        samples = [mean + alpha * (s - mean) for s in samples]

    probs = {}
    for label in bin_labels():
        if label.startswith("<="):
            edge = BIN_LOW + 0.5
            p = sum(w * _norm_cdf(edge, s, bw) for w, s in zip(weights, samples)) / W
        elif label.startswith(">="):
            edge = BIN_HIGH - 0.5
            p = sum(w * (1.0 - _norm_cdf(edge, s, bw)) for w, s in zip(weights, samples)) / W
        else:
            t = int(label)
            lo, hi = t - 0.5, t + 0.5
            p = sum(w * (_norm_cdf(hi, s, bw) - _norm_cdf(lo, s, bw))
                    for w, s in zip(weights, samples)) / W
        probs[label] = p
    total = sum(probs.values()) or 1.0
    return {k: v / total for k, v in probs.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_weighting.py tests/test_cli_basis.py -q`
Expected: all pass (backtest and existing model tests call `_bin_probabilities` without weights → uniform branch → identical results).

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_weighting.py
git commit -m "model: weighted Gaussian-mixture bins (uniform = current behavior)"
```

---

### Task B3: weighted consensus in `_collect_samples` / `predict_variable`

**Files:**
- Modify: `model.py` (`_collect_samples` ~lines 99-116; `predict_variable` ~lines 232-295; add `_sample_weights`)
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_weighting.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    return times, [peak - abs(h - 15) for h in range(24)]


def test_sample_weights_split_ensemble_mass_across_members():
    series = {"ens_a": None, "ens_b": None, "det_gfs_seamless": None, "nws_x": None}
    w = model._sample_weights(series, {"ensemble_mean": 0.6,
                                       "det_gfs_seamless": 0.3, "nws": 0.1})
    assert abs(w["ens_a"] - 0.3) < 1e-9      # 0.6 / 2 members
    assert abs(w["ens_b"] - 0.3) < 1e-9
    assert abs(w["det_gfs_seamless"] - 0.3) < 1e-9
    assert abs(w["nws_x"] - 0.1) < 1e-9


def test_consensus_unchanged_without_weights():
    day = date(2030, 7, 1)
    series = {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}
    out = model.predict_variable(series, {"obs": ([], [])}, day, "high", None, None)
    assert out["consensus"] == 91.0          # plain mean of 90 and 92


def test_weights_pull_consensus_toward_skilled_model():
    day = date(2030, 7, 1)
    series = {"det_gfs_seamless": _member(day, 90.0),
              "det_gem_seamless": _member(day, 96.0)}
    calib = {"weights": {"high": {"det_gfs_seamless": 0.9, "det_gem_seamless": 0.1}}}
    out = model.predict_variable(series, {"obs": ([], [])}, day, "high", None, calib)
    # weighted mean = 0.9*90 + 0.1*96 = 90.6
    assert out["consensus"] == 90.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: FAIL — `model._sample_weights` doesn't exist and `predict_variable` ignores `calib["weights"]`.

- [ ] **Step 3: Implement weighted collection + consensus**

In `model.py`, add `_sample_weights` just above `_collect_samples`:

```python
def _sample_weights(series, weights):
    """Map each member label to its per-sample weight from system weights.

    The combined ensemble's mass (`weights['ensemble_mean']`) is split evenly
    across its members so they still shape the distribution; each deterministic
    model keys by its own label; NWS keys by 'nws'. Missing systems fall back to
    the average system weight so an unexpected label can't be silently dropped.
    """
    avg = (sum(weights.values()) / len(weights)) if weights else 1.0
    ens_labels = [l for l in series if l.startswith("ens_")]
    m = len(ens_labels) or 1
    w_ens = weights.get("ensemble_mean", avg)
    out = {}
    for label in series:
        if label.startswith("ens_"):
            out[label] = w_ens / m
        elif label.startswith("det_"):
            out[label] = weights.get(label, avg)
        else:
            out[label] = weights.get("nws", avg)
    return out
```

Replace `_collect_samples` (lines ~99-116) with a version returning parallel value/weight lists:

```python
def _collect_samples(series, day, variable, now, observed, bias, obs_now=None,
                     weights=None):
    """(values, weights) lists of daily extremes for `day`.

    Bias correction applies only to pure forecasts (skipped while anchoring to a
    live obs). `weights` is an optional {system: weight} map; when absent every
    sample weighs 1.0 (identical to the old equal-weight behavior).
    """
    anchoring = obs_now is not None
    wmap = _sample_weights(series, weights) if weights else None
    vals, ws = [], []
    for label, (times, temps) in series.items():
        val = _member_extreme(times, temps, day, variable, now, observed, obs_now)
        if val is None or not math.isfinite(val):
            continue
        if not anchoring:
            val -= bias.get(_group_of(label), {}).get(variable, 0.0)
        vals.append(val)
        ws.append(wmap[label] if wmap else 1.0)
    return vals, ws
```

In `predict_variable`, change the two `_collect_samples` calls (lines ~232-233) to unpack and to pass weights:

```python
    var_weights = (calib or {}).get("weights", {}).get(variable)
    fullday, _fw = _collect_samples(series, day, variable, None, None, bias)
    samples, weights = _collect_samples(series, day, variable, now, observed, bias,
                                        obs_now, var_weights)
    if not samples or not fullday:
        return None
```

The cooling block and the settlement-offset block already operate on the `samples` list with order-preserving comprehensions, so `weights` stays aligned — no change needed there.

Change the consensus mean (line ~295, `mean = sum(samples) / len(samples)`) to the weighted mean, and pass weights into the bin builder. Replace:

```python
    probs = _bin_probabilities(samples, sigma)
    probs = _apply_hard_bound(probs, variable, observed)

    mean = sum(samples) / len(samples)
```

with:

```python
    probs = _bin_probabilities(samples, sigma, weights)
    probs = _apply_hard_bound(probs, variable, observed)

    _w = sum(weights) or 1.0
    mean = sum(w * s for w, s in zip(weights, samples)) / _w
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_weighting.py tests/test_cli_basis.py -q`
Expected: all pass (no-weights path reproduces current consensus exactly).

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_weighting.py
git commit -m "model: skill-weighted consensus (no weights = current behavior)"
```

---

### Task B4: per-system weights with shrinkage + OOS gate in calibration

**Files:**
- Modify: `calibration.py` (add `_system_extremes`, `_system_weights`; wire into `compute()`)
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_weighting.py`:

```python
import calibration


def test_system_weights_shrink_toward_equal_and_favor_skill():
    # 'good' system nails the actual; 'bad' is 4 off, every day.
    days = []
    from datetime import timedelta
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        days.append(d)
        actual[d] = (90.0, 70.0)
        ext[d] = {"good": (90.0, 70.0), "bad": (94.0, 74.0)}
    w = calibration._system_weights(ext, actual, ["good", "bad"], lam=0.25)
    # high: good must outweigh bad, but shrinkage keeps both within [0.2, 0.8]
    assert w["high"]["good"] > w["high"]["bad"]
    assert 0.2 < w["high"]["good"] < 0.8
    assert abs(w["high"]["good"] + w["high"]["bad"] - 1.0) < 1e-9


def test_system_weights_equal_when_skill_is_equal():
    from datetime import timedelta
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        # both systems equally (un)skilled: symmetric errors
        ext[d] = {"a": (91.0, 71.0), "b": (89.0, 69.0)}
    w = calibration._system_weights(ext, actual, ["a", "b"], lam=0.25)
    assert abs(w["high"]["a"] - w["high"]["b"]) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: FAIL — `calibration._system_weights` doesn't exist.

- [ ] **Step 3: Implement system extremes + shrinkage weights**

In `calibration.py`, add near the other helpers:

```python
def _system_extremes(start, end):
    """{day: {system: {'high':v, 'low':v}}} over [start, end].

    Systems = one combined 'ensemble_mean' (mean of all member extremes) plus
    each deterministic model by its label. NWS has no archive, so it is absent.
    Degrades to deterministic-only if the ensemble archive can't be fetched.
    """
    det = open_meteo_models.fetch_historical(start, end)
    try:
        ens = open_meteo_ensemble.fetch_historical(start, end)
    except Exception:
        ens = {}
    out: dict = {}
    day = start
    while day <= end:
        systems: dict[str, dict] = {}
        for label, (t, v) in det.items():
            hi, lo = day_high_low(t, v, day)
            if hi is not None:
                systems[label] = {"high": hi, "low": lo}
        ens_hi, ens_lo = [], []
        for _label, (t, v) in ens.items():
            hi, lo = day_high_low(t, v, day)
            if hi is not None:
                ens_hi.append(hi)
                ens_lo.append(lo)
        if ens_hi:
            systems["ensemble_mean"] = {"high": sum(ens_hi) / len(ens_hi),
                                        "low": sum(ens_lo) / len(ens_lo)}
        if systems:
            out[day] = systems
        day += timedelta(days=1)
    return out


def _system_weights(ext, actual, systems, lam=0.25):
    """{var: {system: weight}} from trailing skill, strongly shrunk to equal.

    For each variable: weight_i ∝ (1-lam)*equal + lam*invMAE_norm_i, where
    invMAE_norm normalizes inverse per-system MAE to sum 1. lam small => near
    equal (conservative). Systems with no data on a day are skipped that day.
    """
    weights = {}
    n = len(systems)
    equal = 1.0 / n if n else 0.0
    for var in ("high", "low"):
        mae = {}
        for s in systems:
            errs = [abs(ext[d][s][var] - (actual[d][0] if var == "high" else actual[d][1]))
                    for d in ext if d in actual and s in ext[d]]
            mae[s] = (sum(errs) / len(errs)) if errs else None
        inv = {s: 1.0 / max(mae[s], 0.1) for s in systems if mae[s] is not None}
        inv_sum = sum(inv.values()) or 1.0
        inv_norm = {s: inv.get(s, 0.0) / inv_sum for s in systems}
        raw = {s: (1.0 - lam) * equal + lam * inv_norm[s] for s in systems}
        total = sum(raw.values()) or 1.0
        weights[var] = {s: raw[s] / total for s in systems}
    return weights
```

Add the ensemble import at the top of `calibration.py` (the existing line imports `open_meteo_models, station_history`):

```python
from sources import open_meteo_ensemble, open_meteo_models, station_history
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_weighting.py
git commit -m "calibration: per-system inverse-MAE weights with strong shrinkage"
```

---

### Task B5: OOS gate + emit weights from `compute()`

**Files:**
- Modify: `calibration.py` (`compute()`, add `_weights_beat_equal`)
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_weighting.py`:

```python
def test_gate_keeps_weights_only_when_they_beat_equal():
    from datetime import timedelta
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {"good": (90.0, 70.0), "bad": (95.0, 75.0)}
    systems = ["good", "bad"]
    w = calibration._system_weights(ext, actual, systems, lam=0.25)
    # weighting (favoring 'good') should beat equal weight on this data
    assert calibration._weights_beat_equal(ext, actual, systems, w, "high",
                                           margin=0.02) is True


def test_gate_rejects_when_no_improvement():
    from datetime import timedelta
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {"a": (91.0, 71.0), "b": (89.0, 69.0)}   # symmetric, equal skill
    systems = ["a", "b"]
    w = calibration._system_weights(ext, actual, systems, lam=0.25)
    assert calibration._weights_beat_equal(ext, actual, systems, w, "high",
                                           margin=0.02) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: FAIL — `calibration._weights_beat_equal` doesn't exist.

- [ ] **Step 3: Implement the gate and wire `compute()`**

In `calibration.py`, add:

```python
def _consensus_mae(ext, actual, systems, var, wmap):
    """Mean abs error of the wmap-weighted consensus over days with data."""
    errs = []
    for d in ext:
        if d not in actual:
            continue
        num = den = 0.0
        for s in systems:
            if s in ext[d]:
                w = wmap[s]
                num += w * ext[d][s][var]
                den += w
        if den <= 0:
            continue
        cons = num / den
        act = actual[d][0] if var == "high" else actual[d][1]
        errs.append(abs(cons - act))
    return (sum(errs) / len(errs)) if errs else float("inf")


def _weights_beat_equal(ext, actual, systems, weights, var, margin=0.02):
    """True iff the skill-weighted consensus MAE beats equal weight by >= margin."""
    equal = {s: 1.0 for s in systems}
    eq_mae = _consensus_mae(ext, actual, systems, var, equal)
    w_mae = _consensus_mae(ext, actual, systems, var, weights[var])
    return w_mae <= eq_mae - margin
```

In `compute()`, after `fcst = _forecast_daily_extremes(...)` and the bias/sigma blocks, build and gate weights before the `return`:

```python
    weights = {"high": {}, "low": {}}
    try:
        ext = _system_extremes(start, end)
        systems = sorted({s for day in ext.values() for s in day})
        if ext and len(systems) >= 2:
            cand = _system_weights(ext, actual, systems)
            for var in ("high", "low"):
                if _weights_beat_equal(ext, actual, systems, cand, var):
                    weights[var] = cand[var]
                else:
                    weights[var] = {s: 1.0 / len(systems) for s in systems}
    except Exception:
        weights = {"high": {}, "low": {}}
```

Add `"weights": weights,` to the returned dict (next to `"sigma"`).

- [ ] **Step 4: Run tests + live smoke**

Run: `.venv/bin/python -m pytest tests/test_weighting.py -q`
Expected: all pass.

Run: `.venv/bin/python -c "import calibration, json; print(json.dumps(calibration.compute()['weights'], indent=2))"`
Expected: a `weights` block; `low` likely favors GFS, `high` likely equal (gate rejects weighting). No exception.

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_weighting.py
git commit -m "calibration: OOS-gate per-variable weights, emit to calibration.json"
```

---

### Task B6: apply weights in the backtest accuracy panel

**Files:**
- Modify: `backtest.py` (`run`)
- Test: `tests/test_weighting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_weighting.py`:

```python
import backtest
from sources import open_meteo_models, open_meteo_ensemble, station_history


def test_backtest_uses_system_weights_when_provided(monkeypatch):
    day = date(2026, 6, 10)
    det = {"det_gfs_seamless": _member(day, 90.0),
           "det_gem_seamless": _member(day, 96.0)}
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: det)
    monkeypatch.setattr(open_meteo_ensemble, "fetch_historical", lambda s, e: {})
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0},
        "weights": {"high": {"det_gfs_seamless": 0.9, "det_gem_seamless": 0.1},
                    "low": {"det_gfs_seamless": 0.5, "det_gem_seamless": 0.5}}})
    res = backtest.run()
    # weighted high consensus = 0.9*90 + 0.1*96 = 90.6 -> MAE vs 90 = 0.6
    assert res["high"]["mae"] == 0.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_weighting.py::test_backtest_uses_system_weights_when_provided -q`
Expected: FAIL — backtest uses an unweighted `mu` (would be (90+96)/2=93 → MAE 3.0).

- [ ] **Step 3: Implement weighted consensus in backtest**

In `backtest.py`, in `run`, read weights once after `calib = calibration.get(...)`:

```python
    weights_cfg = calib.get("weights") or {}
```

Build per-sample weights aligned with the per-model `samples`. Replace the sample-collection loop (currently lines ~139-150):

```python
            samples = []
            for _lab, (t, v) in series.items():
                hi, lo = day_high_low(t, v, day)
                if hi is None:
                    continue
                samples.append(hi if var == "high" else lo)
            if not samples:
                continue
            actual_label = bin_for_temp(act)

            corrected = [s - bias.get(var, 0.0) + off for s in samples]
            probs = _bin_probabilities(corrected, sigma)
            mu = sum(corrected) / len(corrected)
```

with a label-aware version that carries weights (deterministic backtest: each model label maps to its system weight; default 1.0):

```python
            samples, sweights = [], []
            vw = weights_cfg.get(var, {})
            for lab, (t, v) in series.items():
                hi, lo = day_high_low(t, v, day)
                if hi is None:
                    continue
                samples.append(hi if var == "high" else lo)
                sweights.append(vw.get(lab, 1.0))
            if not samples:
                continue
            actual_label = bin_for_temp(act)

            off, gap_std = _offset_for(var, day)
            sigma = math.hypot(sigma_base, gap_std) if gap_std else sigma_base
            corrected = [s - bias.get(var, 0.0) + off for s in samples]
            probs = _bin_probabilities(corrected, sigma, sweights)
            _wsum = sum(sweights) or 1.0
            mu = sum(w * s for w, s in zip(sweights, corrected)) / _wsum
```

(This step assumes Task A4's `_offset_for` helper and `sigma_base` rename are already in place; if executing B6 before A4, also apply A4's offset/sigma edits.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_weighting.py
git commit -m "backtest: weighted consensus on the accuracy panel when calibrated"
```

---

### Task B7: regenerate calibration + full verification

**Files:**
- Modify: `calibration.json` (regenerated artifact)

- [ ] **Step 1: Regenerate calibration with the new fields**

Run: `.venv/bin/python -c "import calibration; calibration.compute_and_save(); print('ok')"`
Expected: `ok`, and `calibration.json` now contains `weights` and the (possibly bucketed) `settlement_offset`.

- [ ] **Step 2: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 3: Smoke-test both dashboard snapshots**

Run:
```bash
.venv/bin/python -c "
import calibration, model
c = calibration.get(refresh=True)
rh = model.snapshot(c)
k = model.snapshot(c, settle_offset=(c or {}).get('settlement_offset'))
print('robinhood low today consensus:', rh['today']['low']['consensus'])
print('kalshi   low today consensus:', k['today']['low']['consensus'])
"
```
Expected: both print without error; Kalshi low differs from Robinhood by the (now possibly bucket-dependent) offset.

- [ ] **Step 4: Backtest report sanity**

Run: `.venv/bin/python backtest.py`
Expected: prints HIGH/LOW metrics with no exception.

- [ ] **Step 5: Commit**

```bash
git add calibration.json
git commit -m "calibration: regenerate with weights + conditional offset"
```

---

## Final verification checklist

- [ ] `.venv/bin/python -m pytest -q` — entire suite green.
- [ ] `calibration.json` has `weights` (per-variable, per-system) and `settlement_offset` (flat or bucketed).
- [ ] Robinhood snapshot (`model.snapshot(calib)`) unchanged in shape; consensus reflects weights only if the gate kept them.
- [ ] Kalshi snapshot applies the bucketed offset based on tonight's forecast conditions.
- [ ] No network calls in tests (all fetches monkeypatched).

## Notes for the implementer

- **Backward compatibility is the safety net.** Every new shape is optional: no `weights` / uniform weights and a flat `settlement_offset` must reproduce today's numbers exactly. The existing `tests/test_cli_basis.py` is your regression guard — never edit it to make a change pass.
- **Degrade, don't crash.** Ensemble-history and night-condition fetches are wrapped so failures fall back to deterministic-only weighting / the 'other' bucket.
- **Order:** Phase A and Phase B are independent except that Task B6 reuses `_offset_for`/`sigma_base` from Task A4. Execute A before B (recommended), or apply A4's backtest edits as part of B6.
