# MOS/NBM Skill-Weighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make IEM MOS (LAV + NBS) two first-class skill-weighted systems in the day-ahead consensus, measured from the IEM run archive and protected by the existing walk-forward OOS gate.

**Architecture:** MOS rides the *existing* weighting machinery — `_system_extremes` → `_system_weights` (inverse-MAE, shrunk to equal λ=0.25) → `_weights_beat_equal` (walk-forward gate). A new `iem_mos.historical_extremes` returns per-day MOS extremes from each target day's **prior-day 12Z run**; `_system_extremes` folds them in as `mos_lav`/`mos_nbs` systems (the systems list is auto-derived from its keys, so they reach the weights and gate with no extra wiring). One `_sample_weights` branch change lets those weights carry into the consensus. A `forecast_log` change starts logging MOS per-model for later live refinement.

**Tech Stack:** Python 3.9, pytest, IEM `mos.json` API (via `sources/common.get_json`), Open-Meteo historical archive.

## Global Constraints

- Run Python via `.venv/bin/python` and `.venv/bin/pytest` — system python lacks `requests`.
- **MOS bias stays 0** in v1. Measure it in the validation task; do not add a bias knob.
- **No per-lead system weighting** — weighting stays per-variable, as today.
- MOS is measured at a **day-ahead lead** (prior-day 12Z run); the NWP archive stays short-lead. This deliberately *under*-weights MOS (conservative); the walk-forward gate guarantees no regression. Do not add a fixed-lead NWP re-archive.
- Every new fetch is best-effort: a missing/short/failed MOS run means that system is simply **absent** for that day/var — never a crash. The weighting already skips a system with no data on a day.
- Follow house style: local `from settlement import day_high_low` inside the function (as `model.per_source_extremes` does), `monkeypatch.setattr(module, "get_json", ...)` in tests.

---

### Task 1: `iem_mos.historical_extremes` — the archive backfill

**Files:**
- Modify: `sources/iem_mos.py` (add `historical_extremes`; add `from datetime import date, timedelta` as needed)
- Test: `tests/test_iem_mos.py`

**Interfaces:**
- Consumes: `sources.common.get_json`, `iem_mos._parse`, `settlement.day_high_low`, `iem_mos.MODELS` (`["LAV", "NBS"]`), `config.STATION_ID`, `iem_mos.URL`.
- Produces: `historical_extremes(start: date, end: date, ttl: int = 24*3600) -> dict[date, dict[str, tuple[float|None, float|None]]]` — `{target_day: {"mos_lav": (high, low), "mos_nbs": (high, low)}}`. A model with no usable run for a day is omitted from that day's inner dict; a day with no models at all is omitted entirely.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iem_mos.py — append

from datetime import date, timedelta


def test_historical_extremes_uses_prior_day_12z_run(monkeypatch):
    calls = []

    def fake_get_json(url, params=None, **kwargs):
        calls.append(params)
        # A 12Z run: covers the target day's afternoon high and morning low.
        target = "2026-06-03"
        return {"data": [
            _row(f"{target}T11:00:00.000", 72),   # ~6am CDT low
            _row(f"{target}T20:00:00.000", 95),   # ~3pm CDT high
        ]}

    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))

    assert set(out[date(2026, 6, 3)]) == {"mos_lav", "mos_nbs"}
    assert out[date(2026, 6, 3)]["mos_nbs"] == (95.0, 72.0)
    # runtime must be the PRIOR day at 12Z, once per model (2 models).
    assert all(p["runtime"] == "2026-06-02T12:00Z" for p in calls)
    assert {p["model"] for p in calls} == {"LAV", "NBS"}


def test_historical_extremes_skips_a_model_with_no_run(monkeypatch):
    def fake_get_json(url, params=None, **kwargs):
        if params["model"] == "LAV":
            raise RuntimeError("no archived run")
        return {"data": [
            _row("2026-06-03T11:00:00.000", 72),
            _row("2026-06-03T20:00:00.000", 95),
        ]}

    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))
    assert set(out[date(2026, 6, 3)]) == {"mos_nbs"}


def test_historical_extremes_omits_day_with_no_data(monkeypatch):
    monkeypatch.setattr(iem_mos, "get_json",
                        lambda url, params=None, **kw: {"data": []})
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))
    assert out == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_iem_mos.py -k historical_extremes -v`
Expected: FAIL — `AttributeError: module 'sources.iem_mos' has no attribute 'historical_extremes'`

- [ ] **Step 3: Write minimal implementation**

Add to `sources/iem_mos.py` (import `date, timedelta` at the top if not present):

```python
def historical_extremes(start, end, ttl: int = 24 * 3600):
    """{target_day: {'mos_lav'/'mos_nbs': (high, low)}} from each day's
    prior-day 12Z run — a genuine ~24-38h day-ahead lead.

    Each target day is forecast from a DIFFERENT run (the 12Z cycle issued the
    day before), so unlike the NWP fetchers this returns per-day extremes rather
    than one continuous series. A model whose run is missing/short for a day is
    omitted; a day with no usable model is omitted entirely. Best-effort: any
    per-call failure skips that model, never raises.
    """
    from settlement import day_high_low  # local import: avoid load-time cycle
    out: dict = {}
    day = start
    while day <= end:
        runtime = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                   - timedelta(days=1)).replace(hour=12).strftime("%Y-%m-%dT%H:%MZ")
        systems: dict = {}
        for m in MODELS:
            try:
                data = get_json(URL, {"station": STATION_ID, "model": m,
                                      "runtime": runtime}, ttl=ttl)
            except Exception:
                continue
            times, temps = _parse(data)
            if not times:
                continue
            hi, lo = day_high_low(times, temps, day)
            if hi is not None or lo is not None:
                systems[f"mos_{m.lower()}"] = (hi, lo)
        if systems:
            out[day] = systems
        day += timedelta(days=1)
    return out
```

Also add `from sources.common import get_json` reference — `iem_mos` already imports `get_json` at module level (used by `fetch`), and tests monkeypatch `iem_mos.get_json`, so reference it unqualified as `get_json` (matching `fetch`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_iem_mos.py -k historical_extremes -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full MOS test file to confirm no regression**

Run: `.venv/bin/pytest tests/test_iem_mos.py -v`
Expected: PASS (all existing + 3 new)

- [ ] **Step 6: Commit**

```bash
git add sources/iem_mos.py tests/test_iem_mos.py
git commit -m "feat: iem_mos.historical_extremes — day-ahead MOS extremes from prior-day 12Z runs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Fold MOS into `_system_extremes`

**Files:**
- Modify: `calibration.py:287-319` (`_system_extremes`)
- Test: `tests/test_weighting.py`

**Interfaces:**
- Consumes: `iem_mos.historical_extremes` (Task 1) → `{date: {"mos_lav"/"mos_nbs": (high, low)}}`.
- Produces: `_system_extremes` now yields `{day: {system: {"high", "low"}}}` where `system` may include `mos_lav`/`mos_nbs`. `calibration.py:578` (`systems = sorted({s for day in ext.values() for s in day})`) auto-derives them into `_system_weights` and `_weights_beat_equal` — no further wiring.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_weighting.py — append. Imports already present: calibration,
# open_meteo_models, open_meteo_ensemble, date, timedelta. Add: from sources import iem_mos

def test_system_extremes_includes_mos_systems(monkeypatch):
    from sources import iem_mos
    d = date(2026, 6, 3)

    def det(s, e):  # one continuous series per det label
        base = datetime(d.year, d.month, d.day, tzinfo=_TZ)
        times = [base + timedelta(hours=h) for h in range(24)]
        return {"det_gfs_seamless": (times, [90 - abs(h - 15) for h in range(24)])}

    monkeypatch.setattr(open_meteo_models, "fetch_historical", det)
    monkeypatch.setattr(open_meteo_ensemble, "fetch_historical", lambda s, e: {})
    monkeypatch.setattr(iem_mos, "historical_extremes",
                        lambda s, e: {d: {"mos_lav": (91.0, 71.0),
                                          "mos_nbs": (92.0, 72.0)}})
    ext = calibration._system_extremes(d, d)
    assert ext[d]["mos_nbs"] == {"high": 92.0, "low": 72.0}
    assert ext[d]["mos_lav"] == {"high": 91.0, "low": 71.0}
    assert "det_gfs_seamless" in ext[d]


def test_system_extremes_survives_mos_fetch_failure(monkeypatch):
    from sources import iem_mos
    d = date(2026, 6, 3)

    def det(s, e):
        base = datetime(d.year, d.month, d.day, tzinfo=_TZ)
        times = [base + timedelta(hours=h) for h in range(24)]
        return {"det_gfs_seamless": (times, [90 - abs(h - 15) for h in range(24)])}

    monkeypatch.setattr(open_meteo_models, "fetch_historical", det)
    monkeypatch.setattr(open_meteo_ensemble, "fetch_historical", lambda s, e: {})
    monkeypatch.setattr(iem_mos, "historical_extremes",
                        lambda s, e: (_ for _ in ()).throw(RuntimeError("iem down")))
    ext = calibration._system_extremes(d, d)
    assert "det_gfs_seamless" in ext[d]           # NWP unaffected
    assert not any(s.startswith("mos_") for s in ext[d])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_weighting.py -k system_extremes -v`
Expected: FAIL — `test_system_extremes_includes_mos_systems` KeyErrors on `"mos_nbs"` (the second test may error too until the guard exists).

- [ ] **Step 3: Write minimal implementation**

In `calibration.py`, add the import near the top (line 30 group):

```python
from sources import iem_mos, open_meteo_ensemble, open_meteo_models, station_history
```

In `_system_extremes`, after the ensemble block (`ens = ...`) fetch MOS best-effort, and merge per-day inside the `while` loop right before the `if systems:` check:

```python
def _system_extremes(start, end):
    """{day: {system: {'high':v, 'low':v}}} over [start, end].

    Systems = one combined 'ensemble_mean' + each deterministic model by label +
    'mos_lav'/'mos_nbs' (day-ahead MOS from prior-day 12Z runs). NWS has no
    archive, so it is absent. Degrades gracefully if any archive can't be fetched.
    """
    det = open_meteo_models.fetch_historical(start, end)
    try:
        ens = open_meteo_ensemble.fetch_historical(start, end)
    except Exception:
        ens = {}
    try:
        mos = iem_mos.historical_extremes(start, end)
    except Exception:
        mos = {}
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
        for label, (hi, lo) in mos.get(day, {}).items():
            if hi is not None:
                systems[label] = {"high": hi, "low": lo}
        if systems:
            out[day] = systems
        day += timedelta(days=1)
    return out
```

(Note: `_system_weights`/`_consensus_mae` index `ext[d][s]["high"]`/`["low"]`; a MOS system stored only when `hi is not None` keeps `low` possibly None — but `day_high_low` returns both or neither non-None for a run that covers the day, and the weighting reads `var`-specific values, skipping days where the value is absent. Storing on `hi is not None` matches the deterministic branch exactly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_weighting.py -k system_extremes -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full weighting suite for no regression**

Run: `.venv/bin/pytest tests/test_weighting.py -v`
Expected: PASS (all existing + 2 new)

- [ ] **Step 6: Commit**

```bash
git add calibration.py tests/test_weighting.py
git commit -m "feat: fold day-ahead MOS (lav/nbs) into _system_extremes as skill-weighted systems

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Route `mos_*` to its own weight in `_sample_weights`

**Files:**
- Modify: `model.py:294-301` (`_sample_weights` loop)
- Test: `tests/test_weighting.py`

**Interfaces:**
- Consumes: system weights dict from calibration (now containing `mos_lav`/`mos_nbs` keys, Task 2).
- Produces: `_sample_weights` maps a live `mos_lav`/`mos_nbs` series label to its own system weight (falling back to the average system weight when absent), instead of the neutral `nws` weight.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_weighting.py — append

def test_sample_weights_routes_mos_to_own_weight():
    series = {"ens_a": None, "det_gfs_seamless": None,
              "mos_lav": None, "mos_nbs": None, "nws_x": None}
    weights = {"ensemble_mean": 0.2, "det_gfs_seamless": 0.2,
               "mos_lav": 0.1, "mos_nbs": 0.4, "nws": 0.1}
    w = model._sample_weights(series, weights)
    assert abs(w["mos_nbs"] - 0.4) < 1e-9      # its own skill weight, not nws
    assert abs(w["mos_lav"] - 0.1) < 1e-9
    assert abs(w["nws_x"] - 0.1) < 1e-9        # nws still keys 'nws'


def test_sample_weights_mos_falls_back_to_avg_when_absent():
    series = {"det_gfs_seamless": None, "mos_nbs": None}
    weights = {"det_gfs_seamless": 0.5, "nws": 0.5}   # no mos_nbs key
    w = model._sample_weights(series, weights)
    avg = sum(weights.values()) / len(weights)        # 0.5
    assert abs(w["mos_nbs"] - avg) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_weighting.py -k "routes_mos or mos_falls_back" -v`
Expected: FAIL — `test_sample_weights_routes_mos_to_own_weight` gets `0.1` (the `nws` weight) for `mos_nbs`, not `0.4`.

- [ ] **Step 3: Write minimal implementation**

In `model.py`, change the `_sample_weights` loop (lines 294-301):

```python
    for label in series:
        if label.startswith("ens_"):
            out[label] = w_ens / m
        elif label.startswith("det_"):
            out[label] = weights.get(label, avg)
        elif label.startswith("mos_"):
            out[label] = weights.get(label, avg)
        else:
            out[label] = weights.get("nws", avg)
    return out
```

Update the docstring line to note MOS: `... each deterministic model keys by its own label; each MOS model (mos_lav/mos_nbs) keys by its own label; NWS keys by 'nws'.`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_weighting.py -k "routes_mos or mos_falls_back" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full weighting suite for no regression**

Run: `.venv/bin/pytest tests/test_weighting.py -v`
Expected: PASS — in particular `test_sample_weights_split_ensemble_mass_across_members` (the `nws_x` case) still passes.

- [ ] **Step 6: Commit**

```bash
git add model.py tests/test_weighting.py
git commit -m "feat: _sample_weights routes mos_* to its own skill weight, not the neutral nws weight

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Forward-log MOS per model

**Files:**
- Modify: `forecast_log.py:97-107` (`_source_means`)
- Test: `tests/test_iem_mos.py` (or `tests/test_observations.py` — use a new small test module `tests/test_forecast_log_sources.py`)

**Interfaces:**
- Consumes: `snapshot["sources"][which]` = `{group: {label: (high, low)}}` from `model.per_source_extremes`, where MOS labels sit under the `"guidance"` group.
- Produces: `_source_means` emits each MOS model (`mos_lav`, `mos_nbs`) as its own key in the returned dict (so the forward log stores them per-model at true live day-ahead lead), while non-MOS groups still collapse to a single group mean.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forecast_log_sources.py — new file
import forecast_log


def test_source_means_splits_mos_per_model():
    per_source = {
        "guidance": {"mos_lav": (95.0, 72.0), "mos_nbs": (96.0, 73.0)},
        "ensemble": {"ens_a": (90.0, 70.0), "ens_b": (92.0, 72.0)},
    }
    out = forecast_log._source_means(per_source, "high")
    assert out["mos_lav"] == 95.0
    assert out["mos_nbs"] == 96.0
    assert out["ensemble"] == 91.0            # non-MOS group still collapses to mean


def test_source_means_low_variable_and_missing_values():
    per_source = {"guidance": {"mos_nbs": (96.0, 73.0), "mos_lav": (None, None)}}
    out = forecast_log._source_means(per_source, "low")
    assert out["mos_nbs"] == 73.0
    assert "mos_lav" not in out               # no usable value -> omitted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_forecast_log_sources.py -v`
Expected: FAIL — `out` has key `"guidance"` (collapsed mean 95.5), not `"mos_lav"`/`"mos_nbs"`.

- [ ] **Step 3: Write minimal implementation**

In `forecast_log.py`, change `_source_means` to split the guidance (MOS) group per-model:

```python
def _source_means(per_source: dict, variable: str) -> dict:
    """Collapse {group: {label: (high, low)}} to {key: mean extreme} for one
    variable — the per-source predicted value we later difference against the
    settlement to learn each group's own bias. MOS models (the 'guidance' group)
    are emitted PER MODEL (mos_lav/mos_nbs) rather than collapsed, so live
    day-ahead skill-weighting can distinguish them; every other group collapses
    to its mean as before."""
    idx = 0 if variable == "high" else 1
    out = {}
    for group, labels in (per_source or {}).items():
        if group == "guidance":
            for label, v in labels.items():
                if v and v[idx] is not None:
                    out[label] = round(v[idx], 2)
            continue
        vals = [v[idx] for v in labels.values() if v and v[idx] is not None]
        if vals:
            out[group] = round(sum(vals) / len(vals), 2)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_forecast_log_sources.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run related suites for no regression**

Run: `.venv/bin/pytest tests/test_iem_mos.py tests/test_observations.py -v`
Expected: PASS (no forecast_log consumer asserts a `"guidance"` key)

- [ ] **Step 6: Commit**

```bash
git add forecast_log.py tests/test_forecast_log_sources.py
git commit -m "feat: forward-log MOS per model (mos_lav/mos_nbs) instead of a collapsed guidance mean

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Validation harness — measure the day-ahead win (ship gate)

**Files:**
- Create: `scripts/validate_mos_weighting.py`
- Output: `docs/benchmarks/2026-07-17-mos-weighting/ASSESSMENT.md` (numbers + decision)

**Interfaces:**
- Consumes: `calibration._system_extremes`, `_system_weights`, `_weights_beat_equal`, `_consensus_mae`; `station_history.fetch_actual`; `config.CALIBRATION_WINDOW_DAYS`.
- Produces: printed day-ahead consensus MAE (high + low) **with** vs **without** MOS systems, the gate's assigned `mos_nbs`/`mos_lav` weights, and the day-ahead high bias with/without MOS. This is the measure-first deliverable that decides whether the change ships.

- [ ] **Step 1: Write the harness**

```python
# scripts/validate_mos_weighting.py
"""Walk-forward OOS check: does adding MOS (lav/nbs) as skill-weighted systems
lower day-ahead consensus MAE vs the current equal-systems baseline?

Run: .venv/bin/python scripts/validate_mos_weighting.py
"""
from datetime import date, timedelta

import calibration
from config import CALIBRATION_WINDOW_DAYS
from sources import station_history

end = date.today() - timedelta(days=1)
start = end - timedelta(days=CALIBRATION_WINDOW_DAYS)

ext = calibration._system_extremes(start, end)
actual = {}
d = start
while d <= end:
    hi, lo = station_history.fetch_actual(d)      # (high, low) or (None, None)
    if hi is not None:
        actual[d] = (hi, lo)
    d += timedelta(days=1)

all_sys = sorted({s for day in ext.values() for s in day})
no_mos = [s for s in all_sys if not s.startswith("mos_")]
print(f"days={len(actual)}  systems_with_mos={all_sys}")

for label, systems in (("with-MOS", all_sys), ("no-MOS", no_mos)):
    # restrict ext to these systems
    sub = {day: {s: v for s, v in sy.items() if s in systems}
           for day, sy in ext.items()}
    w = calibration._system_weights(sub, actual, systems)
    print(f"\n=== {label} ===")
    for var in ("high", "low"):
        gate = calibration._weights_beat_equal(sub, actual, systems, var)
        wmap = w[var] if gate else {s: 1.0 / len(systems) for s in systems}
        mae = calibration._consensus_mae(sub, actual, systems, var, wmap)
        mos_w = {s: round(wmap[s], 3) for s in systems if s.startswith("mos_")}
        print(f"  {var}: gate={'PASS' if gate else 'fail'}  "
              f"consensus_MAE={mae:.3f}  mos_weights={mos_w}")
```

- [ ] **Step 2: Run the harness against live data**

Run: `.venv/bin/python scripts/validate_mos_weighting.py`
Expected: prints day counts, systems, and per-variable gate/MAE/mos-weights for both with-MOS and no-MOS. (If `requests`/archive is unavailable locally, run in the deploy env or note the blocker — see `local-test-env-gaps`.)

- [ ] **Step 3: Record the result and decide**

Create `docs/benchmarks/2026-07-17-mos-weighting/ASSESSMENT.md` with: the printed numbers, the day-ahead HIGH MAE delta (with-MOS minus no-MOS; **negative = improvement**), the assigned NBS/LAV weights, the measured MOS high bias (mean of `ext[d]["mos_nbs"]["high"] - actual_high`), and a one-line ship/hold decision:
- **Ship** if with-MOS day-ahead HIGH consensus MAE ≤ no-MOS (the gate already guarantees no OOS regression; a flat result is acceptable since the change also enables the forward-log refinement).
- **Hold** and investigate only if with-MOS is *worse* than no-MOS at the shipped weights — which should be impossible given the gate, so a worse number signals a wiring bug (revisit Tasks 2-3).

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_mos_weighting.py docs/benchmarks/2026-07-17-mos-weighting/ASSESSMENT.md
git commit -m "test: walk-forward validation harness + assessment for MOS skill-weighting

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. If the local env can't collect some suites (streamlit/cryptography missing per `local-test-env-gaps`), run the subset: `.venv/bin/pytest tests/test_iem_mos.py tests/test_weighting.py tests/test_forecast_log_sources.py tests/test_observations.py -q` and note which suites were skipped and why.

- [ ] **Step 2: Confirm the shipped calibration surfaces MOS**

Run: `.venv/bin/python -c "import calibration, json; c=calibration.get(refresh=True); print(json.dumps(c.get('weights'), indent=2))"`
Expected: `weights.high`/`weights.low` include `mos_lav` and `mos_nbs` keys (values reflect the gate outcome). Confirms the end-to-end path from archive → calibration.json.

- [ ] **Step 3: Invoke the finishing-a-development-branch skill** to decide merge/PR/cleanup.

---

## Self-Review

**Spec coverage:**
- Spec §Components 1 (`iem_mos.fetch_historical`) → Task 1 (named `historical_extremes`, per-day shape — a deliberate refinement over the spec's continuous-series sketch, because each target day comes from a different overlapping run; noted in Task 1).
- Spec §Components 2 (`_system_extremes`) → Task 2.
- Spec §Components 3 (`_sample_weights`) → Task 3.
- Spec §Components 4 (pass MOS to the gate) → covered *automatically* by Task 2 (systems auto-derived at `calibration.py:578`); called out in Task 2 Interfaces.
- Spec §Components 5 (forward-log MOS per-model) → Task 4.
- Spec §Testing + §Goal validation → Task 5 (+ Task 6 regression).
- Spec §Non-goals (MOS bias 0; no per-lead; conservative lead) → Global Constraints; MOS bias measured in Task 5 Step 3.

**Placeholder scan:** none — every code step shows full code; every run step shows the command + expected output.

**Type consistency:** `historical_extremes` returns `{date: {str: (float|None, float|None)}}` in Task 1; consumed identically in Task 2 (`for label, (hi, lo) in mos.get(day, {}).items()`). System keys `mos_lav`/`mos_nbs` match live labels from `iem_mos.fetch` (`mos_{m.lower()}`) so `_sample_weights` `weights.get(label)` in Task 3 hits the same keys the calibration wrote in Task 2. `_source_means` (Task 4) keys the same `mos_lav`/`mos_nbs` labels.
