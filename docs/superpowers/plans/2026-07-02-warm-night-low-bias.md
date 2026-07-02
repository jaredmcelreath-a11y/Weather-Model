# Warm-night Low Bias Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated warm-bucket low-bias correction that lifts the forecast low on warm nights, where the model runs systematically ~0.7°F cold.

**Architecture:** Measure the extra warm-night cold lean from the 45-day calibration window, net of the flat low bias (so it's orthogonal). Gate it with the existing self-correction constants. Emit `bias_correction.warm_low` into `calibration.json`. Apply it in `model.predict_variable`'s pure-forecast low path only, judged on the pre-settle-shift consensus. Surface it on the dashboard.

**Tech Stack:** Python 3, pytest, project-local `.venv`.

## Global Constraints

- Correction applies to the **low only**, **pure-forecast path only** (`obs_now is None`) — once observations anchor the day the realized low supersedes it.
- Measured from the **45-day calibration window** (`_forecast_daily_extremes` + `station_history.fetch_actual`), NOT the forward log.
- Orthogonal to the flat bias by construction: `warm_extra = mean(warm residuals) − overall_low_bias`.
- Gate reuses `scoring.MIN_LEAD_DAYS (10)`, `SHRINK_K (8)`, `SIG_Z (1.0)` — single source of truth, lazy-imported.
- Bias is stored signed (negative = model cold); the model **subtracts** it, warming the low.
- Do not touch the high, the cool-night side, the `by_lead` loop, or the cooling offset.
- Run pytest via `.venv/bin/python -m pytest`.

---

### Task 1: Measure the warm-low bias in calibration

**Files:**
- Modify: `config.py` — add `WARM_LOW_THRESHOLD = 76`.
- Modify: `calibration.py` — add `_warm_low_bias(...)`; wire it into `compute()`.
- Create: `tests/test_warm_low_bias.py` — unit tests for `_warm_low_bias`.

**Interfaces:**
- Consumes: `calibration._mean_std(xs) -> (mean2dp, std2dp)`; `scoring.MIN_LEAD_DAYS`, `scoring.SHRINK_K`, `scoring.SIG_Z`.
- Produces: `_warm_low_bias(fcst: dict, actual: dict, overall_low_bias: float, threshold: int = WARM_LOW_THRESHOLD) -> dict` returning `{"threshold": int, "bias": float}` (bias < 0) or `{}`. `fcst` is `{day: {"high": [..], "low": [..]}}`; `actual` is `{day: (high, low)}`. Emitted into `calibration.json` at `bias_correction.warm_low`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warm_low_bias.py`:

```python
"""Warm-night low bias: gated measurement + fallbacks."""
from datetime import date, timedelta

from calibration import _warm_low_bias


def _mk(pairs):
    """Build (fcst, actual) over consecutive days from (consensus_low, actual_low)."""
    fcst, actual = {}, {}
    d = date(2026, 5, 1)
    for cons, act in pairs:
        fcst[d] = {"high": [95.0], "low": [cons]}
        actual[d] = (95.0, act)
        d += timedelta(days=1)
    return fcst, actual


def test_emits_warm_low_bias_when_warm_nights_run_cold():
    # 12 warm nights (fc 78) verifying ~1.0 warmer (cold lean ~-1.0, small noise)
    # + 15 neutral cool nights. overall low bias = -12/27 = -0.444.
    pairs = [(78.0, 79.2), (78.0, 78.8)] * 6 + [(70.0, 70.0)] * 15
    fcst, actual = _mk(pairs)
    out = _warm_low_bias(fcst, actual, -0.444, threshold=76)
    assert out["threshold"] == 76
    # warm mean residual -1.0; warm_extra = -1.0 - (-0.444) = -0.556;
    # shrink *12/(12+8) -> -0.3336 -> round -0.33
    assert out["bias"] == -0.33


def test_none_when_too_few_warm_nights():
    pairs = [(78.0, 79.0)] * 9 + [(70.0, 70.0)] * 15      # only 9 warm (< 10)
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, -0.2, threshold=76) == {}


def test_none_when_no_extra_lean_beyond_flat_bias():
    # every night runs -0.5; warm mean residual == overall -> warm_extra 0 -> {}.
    pairs = [(78.0, 78.5)] * 12 + [(70.0, 70.5)] * 15
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, -0.5, threshold=76) == {}


def test_none_when_warm_lean_insignificant():
    # warm residuals -3.2 / +3.0 alternating: mean -0.1, sigma ~3.1 -> fails sig.
    pairs = [(78.0, 81.2), (78.0, 75.0)] * 6 + [(70.0, 70.0)] * 15
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, 0.0, threshold=76) == {}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -q`
Expected: FAIL — `ImportError: cannot import name '_warm_low_bias'`.

- [ ] **Step 3: Add the config constant**

In `config.py`, after `CALM_WIND_MAX = 10 ...` (line ~107):

```python
WARM_LOW_THRESHOLD = 76   # forecast low (°F) at/above which the warm-night low-bias correction applies
```

- [ ] **Step 4: Add `_warm_low_bias` and its import**

In `calibration.py`, add `WARM_LOW_THRESHOLD` to the `from config import ...` line. Then add, after `_mean_std` (near the other private helpers):

```python
def _warm_low_bias(fcst: dict, actual: dict, overall_low_bias: float,
                   threshold: int = WARM_LOW_THRESHOLD) -> dict:
    """Extra cold lean on warm nights, beyond the flat low bias.

    On warm nights (consensus forecast low >= threshold) the low runs cold; warm
    and cool leans cancel so the flat bias misses it. Measured over the
    calibration window as (mean warm-night residual) - overall_low_bias, so it is
    orthogonal to the flat bias the model already removes. Gated with the same
    constants as the lead-time loop: >= MIN_LEAD_DAYS warm nights, significance
    |x| > SIG_Z*sigma/sqrt(n), shrinkage n/(n+SHRINK_K). Returns
    {"threshold": t, "bias": v} with v < 0 (model cold => model subtracts it,
    warming the low), or {} when the gate fails.
    """
    from scoring import MIN_LEAD_DAYS, SHRINK_K, SIG_Z
    warm = []
    for day, ext in fcst.items():
        if day not in actual:
            continue
        consensus = sum(ext["low"]) / len(ext["low"])
        if consensus >= threshold:
            warm.append(consensus - actual[day][1])
    n = len(warm)
    if n < MIN_LEAD_DAYS:
        return {}
    _, sigma = _mean_std(warm)
    warm_extra = sum(warm) / n - overall_low_bias
    if abs(warm_extra) <= SIG_Z * sigma / math.sqrt(n):
        return {}
    return {"threshold": threshold,
            "bias": round(warm_extra * n / (n + SHRINK_K), 2)}
```

- [ ] **Step 5: Run the unit tests to green**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Wire it into `compute()`**

In `calibration.py` `compute()`, find the return dict's `"bias_correction": _bias_correction(),` line. Just above the `return {` statement, add:

```python
    bias_correction = _bias_correction()
    _wl = _warm_low_bias(fcst, actual, bias.get("low", 0.0))
    if _wl:
        bias_correction["warm_low"] = _wl
```

and change the return line to `"bias_correction": bias_correction,`.

- [ ] **Step 7: Confirm the suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing + 4 new).

- [ ] **Step 8: Commit**

```bash
git add config.py calibration.py tests/test_warm_low_bias.py
git commit -m "feat: measure warm-night low bias in calibration

Gated warm-bucket low-bias knob measured from the 45-day window net of the
flat bias (orthogonal). Emits bias_correction.warm_low when >=10 warm nights
clear the significance + shrinkage gate.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Apply the correction in the model

**Files:**
- Modify: `model.py` — capture `regime_low`; apply `warm_low` in the pure-forecast low path.
- Modify: `tests/test_warm_low_bias.py` — add model-level tests.

**Interfaces:**
- Consumes: `calibration.json` shape `bias_correction.warm_low = {"threshold": int, "bias": float}` from Task 1; `model.predict_variable(series, obs_series, day, variable, now, calib, settle_offset=None, live=False)`.
- Produces: a warmed low `consensus` when the pre-settle-shift consensus ≥ threshold on the pure-forecast low path.

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_warm_low_bias.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

_CALIB_WARM = {
    "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
    "sigma": {"high": 2.0, "low": 2.0},
    "bias_correction": {"warm_low": {"threshold": 76, "bias": -0.5}},
}


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series(day, peaks=(92.0, 94.0)):
    return {f"det_{i}": _member(day, p) for i, p in enumerate(peaks)}


def test_model_warms_low_on_warm_night():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, _CALIB_WARM)
    # lows 77,79 -> consensus 78 >= 76 -> subtract -0.5 -> +0.5 -> 78.5
    assert out["consensus"] == 78.5


def test_model_leaves_cool_night_low():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day, peaks=(88.0, 90.0)), {"obs": ([], [])},
                                 day, "low", None, _CALIB_WARM)
    # lows 73,75 -> consensus 74 < 76 -> no correction
    assert out["consensus"] == 74.0


def test_model_never_touches_high():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "high",
                                 None, _CALIB_WARM)
    assert out["consensus"] == 93.0            # mean(92,94), untouched


def test_model_skips_warm_low_when_obs_anchored():
    day = date.today()
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    now = base + timedelta(hours=8)
    obs_times = [base + timedelta(hours=h) for h in range(9)]
    obs_temps = [82.0 - h * 0.4 for h in range(9)]      # warm morning, min ~78.8
    obs = {"obs": (obs_times, obs_temps)}
    warm = model.predict_variable(_series(day), obs, day, "low", now, _CALIB_WARM)
    plain_calib = dict(_CALIB_WARM, bias_correction={})
    plain = model.predict_variable(_series(day), obs, day, "low", now, plain_calib)
    # obs anchor the day -> correction skipped -> identical to no-knob run
    assert warm["consensus"] == plain["consensus"]


def test_model_warm_low_and_cooling_stack(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (10.0, 5.0))          # clear + calm
    calib = {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0},
        "cooling": {"cloud_thresh": 30, "wind_thresh": 10, "low_offset": 0.2},
        "bias_correction": {"warm_low": {"threshold": 76, "bias": -0.5}},
    }
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, calib)
    # cooling -0.2 then warm_low +0.5: 78 - 0.2 + 0.5 = 78.3
    assert out["consensus"] == 78.3
```

- [ ] **Step 2: Run them and confirm the warm/stack ones fail**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -q`
Expected: FAIL on `test_model_warms_low_on_warm_night` (78.0 ≠ 78.5) and `test_model_warm_low_and_cooling_stack` (78.1 ≠ 78.3); the cool/high/obs-anchored tests already pass (no correction yet).

- [ ] **Step 3: Capture `regime_low` after the cooling block**

In `model.predict_variable`, immediately after the cooling block (after the line `cooling_applied = True` / before the `# Kalshi settlement basis:` comment, ~line 400), insert:

```python
    # Warm-night regime for the low bias correction, judged on the forecast
    # consensus BEFORE the CLI settle-shift so the shift can't blur the threshold.
    # Uses the same weighted mean the reported consensus uses.
    regime_low = (sum(w * s for w, s in zip(weights, samples)) / (sum(weights) or 1.0)) \
        if samples else None
```

- [ ] **Step 4: Apply the correction after the lead-bias block**

In `model.predict_variable`, immediately after the lead-bias block (`if bc and obs_now is None: samples = [s - bc for s in samples]`, ~line 448), insert:

```python
    # Warm-night low de-bias: on warm forecast nights the consensus runs cold in
    # a way the flat bias misses (warm/cool leans cancel). Add it back. Pure-
    # forecast low path only; regime judged pre-settle-shift (see regime_low).
    wl = (calib or {}).get("bias_correction", {}).get("warm_low") or {}
    if (wl and variable == "low" and obs_now is None
            and regime_low is not None and regime_low >= wl["threshold"]):
        samples = [s - wl["bias"] for s in samples]
```

- [ ] **Step 5: Run the model tests to green**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -q`
Expected: PASS (all 9 tests).

- [ ] **Step 6: Confirm the whole suite passes**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add model.py tests/test_warm_low_bias.py
git commit -m "feat: apply warm-night low bias correction in the model

Pure-forecast low path only, judged on the pre-settle-shift consensus; stacks
cleanly with the flat bias, lead correction, and cooling offset (measured net
of the flat bias). Skipped once obs anchor the day.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Surface it on the dashboard

**Files:**
- Modify: `calibration.py` — extend `active_corrections`.
- Modify: `tests/test_warm_low_bias.py` — add a test.

**Interfaces:**
- Consumes: `calibration.active_corrections(calib) -> list[str]`; the `bias_correction.warm_low` shape from Task 1.
- Produces: one human-readable line for the warm-low knob when live.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_warm_low_bias.py`:

```python
import calibration


def test_active_corrections_lists_warm_low():
    calib = {"bias_correction": {"warm_low": {"threshold": 76, "bias": -0.4}}}
    lines = calibration.active_corrections(calib)
    # shown as applied (warming): -(-0.4) = +0.4
    assert any("warm low" in s and "76" in s and "+0.4" in s for s in lines)


def test_active_corrections_omits_absent_warm_low():
    assert calibration.active_corrections({"bias_correction": {}}) == []
```

- [ ] **Step 2: Run and confirm the first fails**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -k active_corrections -q`
Expected: FAIL on `test_active_corrections_lists_warm_low`.

- [ ] **Step 3: Extend `active_corrections`**

In `calibration.py` `active_corrections`, before `return out`, add:

```python
    wl = ((calib or {}).get("bias_correction") or {}).get("warm_low") or {}
    if wl:
        out.append(f"warm low (>={wl['threshold']}°F) {-wl['bias']:+.1f}°F")
```

- [ ] **Step 4: Run the tests to green**

Run: `.venv/bin/python -m pytest tests/test_warm_low_bias.py -k active_corrections -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_warm_low_bias.py
git commit -m "feat: surface the warm-low correction on the dashboard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: End-to-end verification

**Files:** none (verification only — `calibration.json` is gitignored).

- [ ] **Step 1: Full suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 2: Regenerate calibration.json and inspect the knob**

Run:

```bash
.venv/bin/python -c "
import json, calibration
c = calibration.compute_and_save()
print('warm_low:', json.dumps(c['bias_correction'].get('warm_low')))
print('active:', calibration.active_corrections(c))
"
```

Expected: `warm_low` present with `threshold` 76 and a negative `bias` (~−0.4 to −0.6 on the current window), and the `active` list contains the `warm low (>=76°F) +0.x°F` line. (If the gate does not pass on the live window, `warm_low` is `None` — that is a valid safe outcome; note it and stop.)

- [ ] **Step 3: No commit needed**

`calibration.json` is gitignored (runtime cache, refreshed daily on deploy); the code change alone ships the behavior.

---

## Self-Review

**Spec coverage:**
- Form/predictor (warm-only, forecast low ≥ 76, low only) → Task 1 (`_warm_low_bias`) + `WARM_LOW_THRESHOLD`.
- Measured from calibration window, net of flat bias (orthogonality) → Task 1 Step 4 (`warm_extra = ... - overall_low_bias`).
- Gate (n≥10, significance, shrinkage, reuse scoring constants) → Task 1 Step 4.
- Emit into calibration.json → Task 1 Step 6.
- Applied in model pure-forecast low path, pre-settle-shift regime, after σ final → Task 2 Steps 3–4.
- Interaction with cooling offset (algebraic sum) → Task 2 `test_model_warm_low_and_cooling_stack`.
- obs-anchored skip / high untouched → Task 2 tests.
- Dashboard line → Task 3.
- Regenerate + verify → Task 4.
- Non-goals (dewpoint, cool-night, high, lead loop) → untouched by every task.

**Placeholder scan:** none. Every code step shows complete code; the one conditional (Task 4 "if the gate does not pass") is an explicit expected-outcome branch, not a TODO.

**Type consistency:** `_warm_low_bias` returns `{"threshold": int, "bias": float}` or `{}` in every task that reads it (model reads `wl["threshold"]`/`wl["bias"]`; dashboard reads the same). `bias` is signed negative and **subtracted** in the model, **negated** for display — consistent across Tasks 1–3. `regime_low` uses the weighted-mean formula matching the reported `consensus`.
