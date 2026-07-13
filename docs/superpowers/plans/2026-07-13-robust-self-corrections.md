# Storm-Proof Self-Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop storm-night outliers from steering the self-correction estimators: median-based `per_lead_bias`, flag-excluded + 45-day-windowed residual pools, regime flags logged into forecast/betting logs, and a dashboard note showing exclusions.

**Architecture:** A new `scoring._correction_residuals` builds the estimators' residual pool (windowed to `CALIBRATION_WINDOW_DAYS`, storm/front-flagged records dropped); `per_lead_bias` switches to `statistics.median` with a median-SE significance gate; `per_lead_sigma` becomes an honest std over the same pool. `forecast_log.record` and `betting_log._row` stamp `convective_widened`/`front_widened` from the prediction dicts. The accuracy scoreboard (`scoring.score`) is untouched and stays all-time. Spec: `docs/superpowers/specs/2026-07-13-robust-self-corrections-design.md`.

**Tech Stack:** Pure Python stdlib (`statistics.median`), pytest via repo venv, existing scoring/logging/dashboard modules.

## Global Constraints

- Python 3.9 venv: run everything with `.venv/bin/python` from the repo root (`/Users/jared/Desktop/Weather Model`); no new dependencies.
- Branch: `robust-self-corrections` (already created; spec committed).
- `scoring.score()` — the scoreboard (exact-bin, Brier, reliability, `by_lead` display stats) and `market_accuracy` — must be byte-identical before/after. Only `per_lead_bias`, `per_lead_sigma` (and the new helpers) change behavior.
- `calibration.py` must not change — same `per_lead_bias`/`per_lead_sigma` call interface.
- Window constant: reuse `CALIBRATION_WINDOW_DAYS` (45) from config — do NOT add a new constant.
- Median SE factor: exactly `1.2533` (`SE = 1.2533 × sd/√n`). Shrinkage (`n/(n+SHRINK_K)`), `SIG_Z`, and `MIN_LEAD_DAYS` keep their existing values and roles.
- Flags in forecast_log records are written ONLY when true (calm rows byte-identical, old rows unflagged via `.get()`); betting_log rows always carry both flags explicitly.
- Comment style: prose comments explaining why, matching each file's existing density.

---

### Task 1: Regime flags into forecast_log and betting_log

**Files:**
- Modify: `forecast_log.py` (the per-variable `rec` build inside `record()`, ~line 150)
- Modify: `betting_log.py` (`_row()`, ~line 88)
- Test: `tests/test_accuracy.py` (add one test near the other forecast-log tests), `tests/test_betting_log.py` (add two tests)

**Interfaces:**
- Consumes: prediction dicts already carry `convective_widened` (since June) and `front_widened` (since 2026-07-13) booleans.
- Produces: forecast_log records may carry `"convective_widened": true` / `"front_widened": true` (key absent when falsy). betting_log rows always carry both keys as booleans. Task 2's `_flagged()` reads the forecast_log keys via `.get()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_accuracy.py` (after `test_forecast_log_persists_market_block`):

```python
def test_forecast_log_stamps_regime_flags(tmp_path):
    p = str(tmp_path / "log.jsonl")
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    snap = _snapshot(now)
    snap["today"]["low"]["convective_widened"] = True
    snap["today"]["low"]["front_widened"] = False
    snap["tomorrow"]["high"]["front_widened"] = True
    forecast_log.record(snap, path=p)
    rows = {(r["target_date"], r["variable"]): r for r in forecast_log.load(p)}
    assert rows[(TODAY.isoformat(), "low")]["convective_widened"] is True
    # falsy or absent flags are omitted entirely (calm rows stay byte-identical)
    assert "front_widened" not in rows[(TODAY.isoformat(), "low")]
    assert "convective_widened" not in rows[(TODAY.isoformat(), "high")]
    tom = (TODAY + timedelta(days=1)).isoformat()
    assert rows[(tom, "high")]["front_widened"] is True
```

Add to `tests/test_betting_log.py`:

```python
def test_row_carries_regime_flags():
    cli_var = {"consensus": 78.0, "probabilities": {"78": 1.0},
               "observed_so_far": 78.0, "observed_continuous": None,
               "peak_locked": True, "sigma_used": 0.7,
               "convective_widened": True, "front_widened": False}
    rec = betting_log._row("2026-07-13", "low", "15:00", cli_var, {}, None,
                           -0.36, "2026-07-13T15:00:00-05:00")
    assert rec["convective_widened"] is True
    assert rec["front_widened"] is False


def test_row_flags_default_false_when_absent():
    # A prediction dict from before the flags existed must not crash and must
    # read as un-flagged (explicit False in betting rows, for the join analysis).
    cli_var = {"consensus": 97.0, "probabilities": {"97": 1.0},
               "observed_so_far": None, "observed_continuous": None,
               "peak_locked": False, "sigma_used": 1.0}
    rec = betting_log._row("2026-07-13", "high", "15:00", cli_var, {}, None,
                           0.91, "2026-07-13T15:00:00-05:00")
    assert rec["convective_widened"] is False
    assert rec["front_widened"] is False
```

(If `tests/test_betting_log.py` doesn't already import `betting_log` at module level, add `import betting_log` with the other imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py::test_forecast_log_stamps_regime_flags tests/test_betting_log.py::test_row_carries_regime_flags tests/test_betting_log.py::test_row_flags_default_false_when_absent -v`
Expected: FAIL — `KeyError`/missing keys (records don't carry the flags yet).

- [ ] **Step 3: Implement**

3a. In `forecast_log.py`, inside `record()`'s per-variable loop, directly after the `rec = {...}` literal is built (before the `sources`/`market` attach blocks), add:

```python
            # Storm/front regime stamps — attribution for the correction
            # estimators (scoring drops flagged records from its residual
            # pool). Written only when set, so calm-day rows are unchanged
            # and historical rows read as unflagged via .get().
            for flag in ("convective_widened", "front_widened"):
                if d.get(flag):
                    rec[flag] = True
```

3b. In `betting_log.py`, inside `_row()`'s `rec = {...}` literal, after the `"sigma_used"` entry, add:

```python
        "convective_widened": bool(cli_var.get("convective_widened")),
        "front_widened": bool(cli_var.get("front_widened")),
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py tests/test_betting_log.py -q`
Expected: all pass.
Run: `.venv/bin/python -m pytest -q`
Expected: 283 pre-existing + 3 new, all pass.

- [ ] **Step 5: Commit**

```bash
git add forecast_log.py betting_log.py tests/test_accuracy.py tests/test_betting_log.py
git commit -m "feat: stamp convective/front regime flags into forecast_log and betting_log"
```

---

### Task 2: Correction residual pool + median bias + pooled sigma

**Files:**
- Modify: `scoring.py` (imports ~line 13; replace `per_lead_sigma` lines ~181–195 and `per_lead_bias` lines ~198–222; add `_flagged` + `_correction_residuals` above them)
- Test: `tests/test_accuracy.py` (rewrite the 5 tests in the "self-correction: lead-time bias" section that monkeypatch `scoring.score`; add 4 new tests)

**Interfaces:**
- Consumes: forecast_log records with optional `convective_widened`/`front_widened` keys (Task 1); existing `_settled_records`, `_actuals_for`, `MIN_LEAD_DAYS`, `SHRINK_K`, `SIG_Z`.
- Produces: `_flagged(rec: dict) -> bool`; `_correction_residuals(today: date | None = None, basis: str = "hourly") -> dict[tuple[int, str], list[float]]` keyed `(lead_bucket, variable)`; `per_lead_bias`/`per_lead_sigma` with UNCHANGED signatures and return shapes (`{bucket: {variable: float}}`) — `calibration.py` keeps working untouched. Task 3 consumes `_flagged` and the same window logic.

- [ ] **Step 1: Rewrite/add the tests**

In `tests/test_accuracy.py`, section `# --- self-correction: lead-time bias ---`:

DELETE `test_per_lead_bias_uses_residual_count` (the hit-count vs residual-count distinction disappears: the pool IS the residuals, `n == len(errs)`).

REPLACE `test_per_lead_bias_shrinks_and_gates`, `test_per_lead_bias_empty_when_no_data`, `test_per_lead_bias_forwards_basis_and_today`, and `test_per_lead_sigma_forwards_basis` with:

```python
def test_per_lead_bias_shrinks_and_gates(monkeypatch):
    fake = {(24, "high"): [1.5] * 10,        # unanimous 1.5 -> significant
            (24, "low"): [0.1, -0.1] * 5,    # median 0 -> gate fails
            (0, "high"): [2.0] * 5}          # below MIN_LEAD_DAYS -> dropped
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": fake)
    out = scoring.per_lead_bias()
    # high@24: median 1.5, sd 0 -> SE 0, passes; shrink 1.5 * 10/(10+8) = 0.83
    assert out[24]["high"] == 0.83
    assert "low" not in out.get(24, {})
    assert 0 not in out


def test_per_lead_bias_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {})
    assert scoring.per_lead_bias() == {}


def test_per_lead_estimators_forward_basis_and_today(monkeypatch):
    seen = []
    def fake(today=None, basis="hourly"):
        seen.append((today, basis))
        return {}
    monkeypatch.setattr(scoring, "_correction_residuals", fake)
    scoring.per_lead_bias(today=date(2026, 6, 20), basis="cli")
    scoring.per_lead_sigma(basis="cli")
    assert seen[0] == (date(2026, 6, 20), "cli")
    assert seen[1][1] == "cli"
```

ADD four new tests after those:

```python
def test_median_immune_to_storm_outliers(monkeypatch):
    # 18 calm nights (median ~0) + the three June-style storm misses. The
    # median-based estimator must emit nothing; the sanity block shows the old
    # mean-based estimator WOULD have cleared its own gate on the same pool.
    errs = [0.1, -0.1, 0.2, -0.2, 0.0, 0.1, -0.1, 0.0, 0.2, -0.2,
            0.0, 0.1, -0.1, 0.0, 0.2, -0.2, 0.0, 0.1] + [3.7, 2.7, 3.6]
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(0, "low"): errs})
    assert scoring.per_lead_bias() == {}
    mean = sum(errs) / len(errs)
    sd = (sum((e - mean) ** 2 for e in errs) / len(errs)) ** 0.5
    assert abs(mean) > sd / len(errs) ** 0.5   # the outlier-driven mean was "significant"


def test_consistent_bias_survives_median(monkeypatch):
    # A genuine persistent warm bias (like the day-ahead high) must still emit.
    errs = [1.0, 1.1, 0.9, 1.0, 1.2, 0.8, 1.0, 1.1, 0.9, 1.0,
            1.1, 0.9, 1.0, 1.2, 0.8, 1.0, 1.1, 0.9, 1.0, 1.0]
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(24, "high"): errs})
    out = scoring.per_lead_bias()
    assert out[24]["high"] == round(1.0 * 20 / 28, 2)   # median 1.0, shrunk


def test_per_lead_sigma_std_over_pool(monkeypatch):
    errs = [1.0, -1.0] * 5                    # mean 0, population sd exactly 1.0
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(24, "low"): errs})
    assert scoring.per_lead_sigma() == {24: {"low": 1.0}}


def test_correction_pool_windows_and_excludes_flags(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    today = date(2026, 6, 18)
    old_day = today - timedelta(days=60)
    rows = [
        # in-window but storm-flagged -> excluded from the pool
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 0,
         "consensus": 81.7, "probabilities": {"81": 1.0}, "convective_widened": True},
        # in-window, clean -> kept
        {"target_date": TODAY.isoformat(), "variable": "high", "lead_bucket": 0,
         "consensus": 95.0, "probabilities": {"95": 1.0}},
        # clean but 60 days old -> outside the 45-day window
        {"target_date": old_day.isoformat(), "variable": "high", "lead_bucket": 0,
         "consensus": 90.0, "probabilities": {"90": 1.0}},
    ]
    forecast_log._write(rows, p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {TODAY: (96, 78), old_day: (91, 70)})
    pool = scoring._correction_residuals(today=today)
    assert (0, "low") not in pool              # flagged record excluded
    assert pool[(0, "high")] == [-1.0]         # only the windowed clean record
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py -q`
Expected: the rewritten/new tests FAIL with `AttributeError: ... has no attribute '_correction_residuals'`; untouched tests still pass.

- [ ] **Step 3: Implement in `scoring.py`**

3a. Imports — extend the datetime import and add two more:

```python
import math
import statistics
from datetime import date, timedelta

import forecast_log
from backtest import contract_points, reliability_bins, _brier, LABELS
from config import CALIBRATION_WINDOW_DAYS
from settlement import bin_for_temp
from sources import station_history
```

3b. Add below the `SHRINK_K`/`SIG_Z` constants:

```python
# SE(median) ≈ 1.2533 × sd/√n under approximate normality. The bias gate uses
# it because the estimator is now a median: keeping the mean's SE would make
# the significance test quietly easier to pass, the wrong direction.
MEDIAN_SE_FACTOR = 1.2533


def _flagged(rec: dict) -> bool:
    """True when the record was captured under a live storm/front regime — the
    convective floor or front guard was active, so its residual belongs to a
    conditional regime the live model already widens for, not calm-day skill."""
    return bool(rec.get("convective_widened") or rec.get("front_widened"))


def _correction_residuals(today: date | None = None, basis: str = "hourly"
                          ) -> dict[tuple, list[float]]:
    """{(lead_bucket, variable): [signed consensus errors]} for the correction
    estimators.

    Unlike the all-time scoreboard (score()), this pool is windowed to the last
    CALIBRATION_WINDOW_DAYS — so both calibration loops age at the same rate and
    stale regimes/outliers fall out on their own — and drops storm/front-flagged
    records (see _flagged). Records without a consensus contribute nothing.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=CALIBRATION_WINDOW_DAYS)
    records = [r for r in _settled_records(today)
               if r.get("basis", "hourly") == basis
               and date.fromisoformat(r["target_date"]) >= cutoff
               and not _flagged(r)
               and r.get("consensus") is not None]
    if not records:
        return {}
    actual = _actuals_for(records, basis)
    out: dict[tuple, list[float]] = {}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        act = actual[d][0] if r["variable"] == "high" else actual[d][1]
        out.setdefault((r["lead_bucket"], r["variable"]), []).append(r["consensus"] - act)
    return out
```

3c. REPLACE the bodies of `per_lead_sigma` and `per_lead_bias` (keep names, signatures, and defaults exactly):

```python
def per_lead_sigma(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                   basis: str = "hourly") -> dict:
    """{lead_bucket: {variable: sigma}} for buckets with enough settled days.

    An honest std over the correction pool (_correction_residuals: windowed to
    CALIBRATION_WINDOW_DAYS, storm/front-flagged records dropped). Deliberately
    NOT a robust scale estimator: a day-ahead miss on a day that *turned out*
    stormy is legitimate lead-time uncertainty and stays in — the flags only
    ever mark same-day locked records, so this falls out naturally. Buckets
    below `min_days` are omitted, so the model keeps falling back to the static
    inflation there. `basis` selects the settlement cohort (the live site is CLI).
    """
    out: dict[int, dict[str, float]] = {}
    for (bucket, var), errs in _correction_residuals(today, basis).items():
        if len(errs) < min_days:
            continue
        m = sum(errs) / len(errs)
        sigma = math.sqrt(sum((e - m) ** 2 for e in errs) / len(errs))
        out.setdefault(int(bucket), {})[var] = round(sigma, 2)
    return out


def per_lead_bias(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                  basis: str = "hourly") -> dict[int, dict[str, float]]:
    """{lead_bucket: {variable: correction}} signed bias to SUBTRACT from the
    consensus, for buckets the data can speak to.

    The point estimate is the MEDIAN of the correction pool (windowed +
    flag-excluded, see _correction_residuals), not the mean: three storm-night
    outliers once manufactured a lead-0 low correction the median correctly
    reads as zero, and the median also damps any regime day the flags missed.
    The guards keep their shape: >= min_days pool records, significance
    |median| > SIG_Z * MEDIAN_SE_FACTOR * sd/sqrt(n) (the median's own standard
    error), and shrinkage toward zero by n/(n+SHRINK_K). Omitted buckets =>
    the model applies no correction there.
    """
    out: dict[int, dict[str, float]] = {}
    for (bucket, var), errs in _correction_residuals(today, basis).items():
        n = len(errs)
        if n < min_days:
            continue
        med = statistics.median(errs)
        m = sum(errs) / n
        sd = math.sqrt(sum((e - m) ** 2 for e in errs) / n)
        se = MEDIAN_SE_FACTOR * sd / math.sqrt(n)
        if abs(med) <= SIG_Z * se:
            continue  # statistically indistinguishable from zero
        out.setdefault(int(bucket), {})[var] = round(med * n / (n + SHRINK_K), 2)
    return out
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py -q`
Expected: all pass (including `test_bias_correction_block_wraps_scoring`, which monkeypatches `per_lead_bias` itself and is unaffected).
Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scoring.py tests/test_accuracy.py
git commit -m "feat: correction estimators go robust — median bias + flag-excluded, 45d-windowed pool"
```

---

### Task 3: Exclusion count + dashboard note

**Files:**
- Modify: `scoring.py` (add `correction_exclusions` after `per_lead_bias`)
- Modify: `market_view.py` (config import ~line 23; new cached helper + `exclusion_note` near `_render_accuracy` ~line 1153; two lines inside `_render_accuracy`)
- Test: `tests/test_accuracy.py` (one test), `tests/test_exclusion_note.py` (new)

**Interfaces:**
- Consumes: Task 2's `_flagged`, `_settled_records`, `CALIBRATION_WINDOW_DAYS`.
- Produces: `scoring.correction_exclusions(today: date | None = None, basis: str = "cli") -> int`; `market_view.exclusion_note(n: int) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_accuracy.py` (after `test_correction_pool_windows_and_excludes_flags`):

```python
def test_correction_exclusions_counts_windowed_flags(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    today = date(2026, 6, 18)
    old_day = today - timedelta(days=60)
    rows = [
        # flagged, in window, right basis -> counted
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 0,
         "basis": "cli", "consensus": 81.7, "probabilities": {"81": 1.0},
         "front_widened": True},
        # flagged but stale -> not counted
        {"target_date": old_day.isoformat(), "variable": "low", "lead_bucket": 0,
         "basis": "cli", "consensus": 70.0, "probabilities": {"70": 1.0},
         "convective_widened": True},
        # clean, in window -> not counted
        {"target_date": TODAY.isoformat(), "variable": "high", "lead_bucket": 0,
         "basis": "cli", "consensus": 95.0, "probabilities": {"95": 1.0}},
        # flagged, in window, WRONG basis -> not counted
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 24,
         "basis": "hourly", "consensus": 80.0, "probabilities": {"80": 1.0},
         "convective_widened": True},
    ]
    forecast_log._write(rows, p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    assert scoring.correction_exclusions(today=today, basis="cli") == 1
```

Create `tests/test_exclusion_note.py`:

```python
"""The accuracy panel's exclusion note: visible only when the correction
estimators actually dropped flagged records, so a changed correction is
explainable instead of a silent mystery."""

from config import CALIBRATION_WINDOW_DAYS
from market_view import exclusion_note


def test_note_hidden_when_nothing_excluded():
    assert exclusion_note(0) is None


def test_note_names_count_and_window():
    note = exclusion_note(3)
    assert "3" in note and str(CALIBRATION_WINDOW_DAYS) in note
    assert "storm/front-flagged" in note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py::test_correction_exclusions_counts_windowed_flags tests/test_exclusion_note.py -v`
Expected: FAIL — `correction_exclusions` / `exclusion_note` not defined.

- [ ] **Step 3: Implement**

3a. `scoring.py`, after `per_lead_bias`:

```python
def correction_exclusions(today: date | None = None, basis: str = "cli") -> int:
    """How many settled records inside the correction window were dropped for a
    storm/front flag — the dashboard shows this next to the active corrections
    so an exclusion is visible instead of a silent mystery. Counts candidates
    (no settlement join needed): a flagged record is excluded either way."""
    today = today or date.today()
    cutoff = today - timedelta(days=CALIBRATION_WINDOW_DAYS)
    return sum(1 for r in _settled_records(today)
               if r.get("basis", "hourly") == basis
               and date.fromisoformat(r["target_date"]) >= cutoff
               and _flagged(r))
```

3b. `market_view.py` — extend the config import:

```python
from config import CALIBRATION_WINDOW_DAYS, STATION_ID, TIMEZONE
```

3c. `market_view.py` — add directly above `_render_accuracy`:

```python
def exclusion_note(n):
    """Caption text for the accuracy panel when the correction estimators
    dropped `n` storm/front-flagged records; None when nothing was excluded."""
    if not n:
        return None
    return (f"Correction estimators exclude {n} storm/front-flagged record(s) "
            f"from the last {CALIBRATION_WINDOW_DAYS} days.")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _correction_exclusions():
    """Cached flag-exclusion count (reads the remote forecast log — must not
    refetch on every 60s page refresh). Best-effort: 0 on any failure."""
    import scoring
    try:
        return scoring.correction_exclusions(basis="cli")
    except Exception:
        return 0
```

3d. In `_render_accuracy`, directly after the `if corr:` block that renders "Active self-corrections", add:

```python
    note = exclusion_note(_correction_exclusions())
    if note:
        st.caption(note)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py tests/test_exclusion_note.py -q`
Expected: all pass.
Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scoring.py market_view.py tests/test_accuracy.py tests/test_exclusion_note.py
git commit -m "feat: show how many storm/front-flagged records the corrections excluded"
```

---

### Task 4: Offline before/after validation on the live logs

**Files:**
- Create: `docs/benchmarks/2026-07-13/robust-corrections/validate.py`
- Create (generated by running it): `docs/benchmarks/2026-07-13/robust-corrections/RESULTS.md`

**Interfaces:**
- Consumes: `scoring.per_lead_bias`, `scoring.per_lead_sigma`, `scoring._settled_records`, `scoring._actuals_for` (patched), `forecast_log._PATH`, `settlements._PATH`, `settlements.as_map`; the repo's `origin/data` branch (network: one `git fetch`).
- Produces: a committed RESULTS.md; no shipped-code changes.

- [ ] **Step 1: Write the validation script**

```python
"""Before/after validation of the storm-proof correction estimators on the
REAL data-branch logs.

OLD = the retired estimator (all-time, unfiltered mean / std) recomputed inline.
NEW = the shipped per_lead_bias / per_lead_sigma (windowed, flag-excluded, median).

Gates (from the spec):
  1. NEW lead-0 low bias correction is ABSENT (the old mean-based path emitted
     ~-0.33 purely from the June 26-28 storm nights; the median reads ~0).
  2. NEW lead-24 high correction SURVIVES (the day-ahead warm bias is
     consistent across days, not outlier-driven).
  3. Rerun with today=2026-08-15 (June 26-28 aged out of the 45-day window):
     lead-0 low sigma DROPS vs its value today (~1.25 -> calm-night level),
     proving the contamination self-heals via the window.

Run from the repo root:
  .venv/bin/python docs/benchmarks/2026-07-13/robust-corrections/validate.py
"""
import math
import os
import statistics
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import forecast_log
import scoring
import settlements
from scoring import MIN_LEAD_DAYS, SHRINK_K, SIG_Z

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "RESULTS.md")


def _fetch_data_branch():
    subprocess.run(["git", "fetch", "origin", "data"], check=True, capture_output=True)
    for name in ("forecast_log.jsonl", "settlements.jsonl"):
        text = subprocess.run(["git", "show", f"origin/data:{name}"], check=True,
                              capture_output=True, text=True).stdout
        with open(os.path.join(HERE, name), "w") as fh:
            fh.write(text)
    forecast_log._PATH = os.path.join(HERE, "forecast_log.jsonl")
    settlements._PATH = os.path.join(HERE, "settlements.jsonl")


def _patch_actuals():
    cli = settlements.as_map("cli")
    hourly = settlements.as_map("hourly")
    scoring._actuals_for = lambda records, basis="hourly": cli if basis == "cli" else hourly


def _old_estimators(today, basis="cli"):
    """The retired behavior: all-time, unfiltered mean bias + std sigma."""
    records = [r for r in scoring._settled_records(today)
               if r.get("basis", "hourly") == basis and r.get("consensus") is not None]
    actual = scoring._actuals_for(records, basis)
    resid = {}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        act = actual[d][0] if r["variable"] == "high" else actual[d][1]
        resid.setdefault((r["lead_bucket"], r["variable"]), []).append(r["consensus"] - act)
    bias, sigma = {}, {}
    for (bucket, var), errs in resid.items():
        n = len(errs)
        if n < MIN_LEAD_DAYS:
            continue
        m = sum(errs) / n
        sd = math.sqrt(sum((e - m) ** 2 for e in errs) / n)
        sigma.setdefault(bucket, {})[var] = round(sd, 2)
        if abs(m) > SIG_Z * sd / math.sqrt(n):
            bias.setdefault(bucket, {})[var] = round(m * n / (n + SHRINK_K), 2)
    return bias, sigma


def main():
    _fetch_data_branch()
    _patch_actuals()
    today = date.today()
    old_bias, old_sigma = _old_estimators(today)
    new_bias = scoring.per_lead_bias(basis="cli", today=today)
    new_sigma = scoring.per_lead_sigma(basis="cli", today=today)
    aug_sigma = scoring.per_lead_sigma(basis="cli", today=date(2026, 8, 15))

    g1 = "low" not in new_bias.get(0, {})
    g2 = "high" in new_bias.get(24, {})
    s_now = new_sigma.get(0, {}).get("low")
    s_aug = aug_sigma.get(0, {}).get("low")
    g3 = s_now is not None and s_aug is not None and s_aug < s_now

    lines = [
        "# Storm-proof corrections — before/after on the live data-branch logs",
        f"\nRun date: {today}\n",
        "| estimator | OLD (all-time mean/std) | NEW (windowed, flagged-out, median) |",
        "|---|---|---|",
        f"| bias | {old_bias} | {new_bias} |",
        f"| sigma | {old_sigma} | {new_sigma} |",
        f"| sigma @ today=2026-08-15 | — | {aug_sigma} |",
        "",
        f"- Gate 1 (lead-0 low phantom correction gone): {'PASS' if g1 else 'FAIL'} "
        f"(old: {old_bias.get(0, {}).get('low')})",
        f"- Gate 2 (lead-24 high correction survives): {'PASS' if g2 else 'FAIL'} "
        f"(old: {old_bias.get(24, {}).get('high')}, new: {new_bias.get(24, {}).get('high')})",
        f"- Gate 3 (lead-0 low sigma self-heals by 2026-08-15): {'PASS' if g3 else 'FAIL'} "
        f"(now: {s_now}, aug: {s_aug})",
    ]
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not (g1 and g2 and g3):
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python docs/benchmarks/2026-07-13/robust-corrections/validate.py`
Expected: prints the table with all three gates PASS and exits 0. Notes:
- Gate 2's exact NEW value depends on the live log's day-ahead median — report whatever it is; the gate is only that the correction *exists*.
- Gate 3 needs ≥`MIN_LEAD_DAYS` (10) lead-0 low CLI records dated after 2026-07-01 in the live log; the Action logs daily since June, so this holds. If a gate FAILS, STOP and report the numbers — do not commit a failing validation.
- Do NOT commit the fetched `forecast_log.jsonl`/`settlements.jsonl` copies (add nothing but the script + RESULTS.md).

- [ ] **Step 3: Commit script + results**

```bash
git add docs/benchmarks/2026-07-13/robust-corrections/validate.py docs/benchmarks/2026-07-13/robust-corrections/RESULTS.md
git commit -m "test: before/after validation of the storm-proof correction estimators on live logs"
```

---

### Task 5: Final verification

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (283 pre-existing + ~10 new/rewritten, minus 1 deleted).

- [ ] **Step 2: Scoreboard-unchanged spot check**

Run: `.venv/bin/python -c "import inspect, scoring; src = inspect.getsource(scoring.score); assert '_correction_residuals' not in src and 'median' not in src; print('score() untouched by the correction changes')"`
Expected: prints the confirmation (the scoreboard path has no dependency on the new pool).

- [ ] **Step 3: Use superpowers:finishing-a-development-branch to merge/PR `robust-self-corrections`**
