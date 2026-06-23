# Gated Self-Correction Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the model automatically remove the persistent lead-time bias it measures in its own settled forecasts, behind a sample-size + significance gate, and surface which self-corrections are live.

**Architecture:** Generalize the existing `sigma.by_lead` feedback path. `scoring.py` gains `per_lead_bias()` (shrinkage + significance gated). `calibration.compute()` writes it into `calibration.json` as `bias_correction.by_lead`. `model.predict_variable` subtracts the correction from the forecast samples on the pure-forecast path. `market_view` shows an "Active self-corrections" line. Loops 2–4 (weights / sharpen / market) are documented extension points that slot into the same knob pattern but are not built here.

**Tech Stack:** Python 3.9, pytest, Streamlit. No new dependencies. All new logic is synthetic-testable (no network).

---

## File Structure

- `scoring.py` — add `per_lead_bias()` + two tuning constants. Sibling of the existing `per_lead_sigma()`.
- `calibration.py` — add `_bias_correction()` helper + `active_corrections()` display helper; emit `bias_correction` from `compute()`.
- `model.py` — apply the correction inside `predict_variable` (pure-forecast path only).
- `market_view.py` — render the "Active self-corrections" line; thread `calib` into `_render_accuracy`.
- `tests/test_accuracy.py` — all new tests live here, alongside the existing accuracy tests.

Sign convention (consistent across every task): **bias = mean(consensus − actual); positive = forecast too warm; the model SUBTRACTS the correction.**

---

## Task 1: `scoring.per_lead_bias()` — gated, shrunk bias

**Files:**
- Modify: `scoring.py` (add constants after line 21 `MIN_LEAD_DAYS = 10`; add function after `per_lead_sigma`, end of file)
- Test: `tests/test_accuracy.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_accuracy.py`:

```python
# --- self-correction: lead-time bias ---

def test_per_lead_bias_shrinks_and_gates(monkeypatch):
    fake = {"by_lead": {
        24: {"high": {"n": 10, "bias": 1.5, "sigma": 1.0},   # strong + significant
             "low":  {"n": 10, "bias": 0.1, "sigma": 2.0}},   # tiny -> insignificant
        0:  {"high": {"n": 5,  "bias": 2.0, "sigma": 0.5}},   # below min_days -> dropped
    }}
    monkeypatch.setattr(scoring, "score", lambda today=None, basis="hourly": fake)
    out = scoring.per_lead_bias()
    # high@24: shrink 1.5 * 10/(10+8) = 0.833 -> 0.83
    assert out[24]["high"] == 0.83
    # low@24: |0.1| <= Z*sigma/sqrt(n) = 1.0*2/sqrt(10) = 0.632 -> not significant
    assert "low" not in out.get(24, {})
    # bucket 0 has only 5 days (< MIN_LEAD_DAYS) -> absent entirely
    assert 0 not in out


def test_per_lead_bias_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(scoring, "score", lambda today=None, basis="hourly": {"by_lead": {}})
    assert scoring.per_lead_bias() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_per_lead_bias_shrinks_and_gates tests/test_accuracy.py::test_per_lead_bias_empty_when_no_data -v`
Expected: FAIL with `AttributeError: module 'scoring' has no attribute 'per_lead_bias'`

- [ ] **Step 3: Add the constants**

In `scoring.py`, immediately after the existing line `MIN_LEAD_DAYS = 10`:

```python
# Self-correction tuning. Shrink a measured bias toward zero by n/(n+SHRINK_K)
# so a noisy short sample is damped and a persistent bias strengthens with data;
# only correct when the bias clears SIG_Z standard errors (distinguishable from 0).
SHRINK_K = 8
SIG_Z = 1.0
```

- [ ] **Step 4: Implement `per_lead_bias`**

Append to `scoring.py` (after `per_lead_sigma`):

```python
def per_lead_bias(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                  basis: str = "hourly") -> dict:
    """{lead_bucket: {variable: correction}} signed bias to SUBTRACT from the
    consensus, for buckets the data can speak to.

    Built from score()'s by_lead bias (= mean(consensus - actual); positive =
    forecast ran warm). Two guards keep auto-correction safe on small samples: a
    >= min_days gate, plus shrinkage toward zero by n/(n+SHRINK_K) combined with a
    significance test (|bias| must exceed SIG_Z * sigma/sqrt(n)). A noisy bias is
    shrunk away; a persistent one survives and grows as days accumulate. Omitted
    buckets => the model applies no correction there.
    """
    out: dict[int, dict[str, float]] = {}
    for bucket, vars_ in score(today, basis=basis).get("by_lead", {}).items():
        for var, stats in vars_.items():
            n = stats.get("n", 0)
            bias = stats.get("bias")
            sigma = stats.get("sigma")
            if n < min_days or bias is None or sigma is None:
                continue
            stderr = sigma / math.sqrt(n) if n else float("inf")
            if abs(bias) <= SIG_Z * stderr:
                continue  # statistically indistinguishable from zero
            out.setdefault(int(bucket), {})[var] = round(bias * n / (n + SHRINK_K), 2)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_per_lead_bias_shrinks_and_gates tests/test_accuracy.py::test_per_lead_bias_empty_when_no_data -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add scoring.py tests/test_accuracy.py
git commit -m "feat: per_lead_bias — gated, shrunk lead-time bias from the log

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Emit `bias_correction` from calibration

**Files:**
- Modify: `calibration.py` (new `_bias_correction()` helper; add key to `compute()` return at lines 502–517)
- Test: `tests/test_accuracy.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_accuracy.py`:

```python
def test_bias_correction_block_wraps_scoring(monkeypatch):
    import calibration
    monkeypatch.setattr(scoring, "per_lead_bias", lambda: {24: {"high": -1.1}})
    assert calibration._bias_correction() == {"by_lead": {24: {"high": -1.1}}}
    # scoring failure must degrade to an empty (no-op) block, never raise
    def boom():
        raise RuntimeError("scoring down")
    monkeypatch.setattr(scoring, "per_lead_bias", boom)
    assert calibration._bias_correction() == {"by_lead": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_bias_correction_block_wraps_scoring -v`
Expected: FAIL with `AttributeError: module 'calibration' has no attribute '_bias_correction'`

- [ ] **Step 3: Add the helper**

In `calibration.py`, add above `def compute() -> dict:` (line 397):

```python
def _bias_correction() -> dict:
    """The lead-time bias-correction knob for calibration.json, behind the same
    lazy scoring import as per-lead sigma. Best-effort: any failure yields an
    empty (no-op) block so recalibration never breaks. Loops 2-4 from the design
    (live group re-weighting, reliability sharpening, market blend) slot in here
    as sibling knobs once their data matures — same gated pattern, no new wiring.
    """
    try:
        import scoring
        return {"by_lead": scoring.per_lead_bias()}
    except Exception:
        return {"by_lead": {}}
```

- [ ] **Step 4: Wire it into `compute()`'s return dict**

In `calibration.py`, in the dict returned by `compute()` (currently lines 502–517), add the `bias_correction` key right after `"settlement_offset": settlement_offset,`:

```python
        "settlement_offset": settlement_offset,
        "bias_correction": _bias_correction(),
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_bias_correction_block_wraps_scoring -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add calibration.py tests/test_accuracy.py
git commit -m "feat: emit bias_correction.by_lead from calibration.compute

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Apply the correction in `model.predict_variable`

**Files:**
- Modify: `model.py` (insert after the per-lead sigma block, lines 388–399; before `probs = _bin_probabilities(...)` at line 407)
- Test: `tests/test_accuracy.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_accuracy.py`:

```python
def test_lead_bias_correction_shifts_consensus():
    s, obs = _diurnal_series(), {"obs": ([], [])}
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 22, tzinfo=TZ)
    tom = TODAY + timedelta(days=1)          # bucket 24, pure forecast (no obs)
    base = model.predict_variable(s, obs, tom, "high", now, {})
    calib = {"bias_correction": {"by_lead": {"24": {"high": 1.5}}}}
    corr = model.predict_variable(s, obs, tom, "high", now, calib)
    # forecast measured 1.5 warm at day-ahead -> consensus drops by 1.5
    assert round(base["consensus"] - corr["consensus"], 1) == 1.5


def test_lead_bias_skipped_when_observed():
    day = TODAY
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 16, tzinfo=TZ)
    ot, ov = _intraday_obs(day, peak_hour=14, peak=95, now_hour=16, drop_after=0.5)
    s = _diurnal_series()
    calib = {"bias_correction": {"by_lead": {"0": {"high": 2.0}}}}
    out = model.predict_variable(s, {"obs": (ot, ov)}, day, "high", now, calib)
    out0 = model.predict_variable(s, {"obs": (ot, ov)}, day, "high", now, {})
    # obs are anchoring the day -> forecast de-bias must NOT apply
    assert out["consensus"] == out0["consensus"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_lead_bias_correction_shifts_consensus tests/test_accuracy.py::test_lead_bias_skipped_when_observed -v`
Expected: `test_lead_bias_correction_shifts_consensus` FAILS (consensus unchanged, diff 0.0 != 1.5); `test_lead_bias_skipped_when_observed` passes incidentally (no correction applied yet).

- [ ] **Step 3: Implement the correction**

In `model.py`, insert immediately after the per-lead sigma block (after line 399 `sigma = max(sigma_base * locked_ratio, _SIGMA_FLOOR)`) and before the settle-gap widening:

```python
    # Lead-time residual de-bias (self-correction layer): the forward log measures
    # a persistent signed error for this (lead, variable). Subtract it from the
    # forecast samples so both the consensus and the bin mass shift together.
    # Pure-forecast path only (obs_now None) — once obs anchor the day the realized
    # extreme supersedes a forecast bias, exactly like the cooling offset.
    bias_corr = (calib or {}).get("bias_correction", {}).get("by_lead", {})
    bc = (bias_corr.get(str(bucket)) or bias_corr.get(bucket) or {}).get(variable)
    if bc and obs_now is None:
        samples = [s - bc for s in samples]
```

Note: this sits after `sigma` is finalized (a constant shift of `samples` leaves `_std(samples)` and `locked_ratio` unchanged, so spread logic is unaffected) and before `probs`/`mean` are computed at lines 407/412, so both pick up the shift.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_lead_bias_correction_shifts_consensus tests/test_accuracy.py::test_lead_bias_skipped_when_observed -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_accuracy.py
git commit -m "feat: apply lead-time bias correction in predict_variable

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: "Active self-corrections" visibility

**Files:**
- Modify: `calibration.py` (add `active_corrections()` near `_bias_correction()`)
- Modify: `market_view.py` (add `import calibration`; thread `calib` into `_render_accuracy`; render the line)
- Test: `tests/test_accuracy.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_accuracy.py`:

```python
def test_active_corrections_lists_live_knobs():
    import calibration
    calib = {"bias_correction": {"by_lead": {"24": {"high": -1.2}}},
             "sigma": {"by_lead": {"24": {"low": 1.8}}}}
    out = calibration.active_corrections(calib)
    assert "day-ahead high -1.2°F bias" in out
    assert "day-ahead low σ=1.8" in out
    # nothing live -> empty list (dormant)
    assert calibration.active_corrections(None) == []
    assert calibration.active_corrections({}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_active_corrections_lists_live_knobs -v`
Expected: FAIL with `AttributeError: module 'calibration' has no attribute 'active_corrections'`

- [ ] **Step 3: Implement `active_corrections`**

In `calibration.py`, add directly below `_bias_correction()`:

```python
def active_corrections(calib: dict | None) -> list[str]:
    """Human-readable list of self-correction knobs currently live in `calib`,
    for the dashboard's 'Active self-corrections' line. Empty when nothing has
    cleared its data gate yet. Handles both int and JSON-string bucket keys."""
    names = {"0": "same-day", "24": "day-ahead", "36": "2-day"}
    out: list[str] = []
    bc = ((calib or {}).get("bias_correction") or {}).get("by_lead") or {}
    for bucket in sorted(bc, key=lambda b: int(b)):
        label = names.get(str(bucket), f"{bucket}h")
        for var, v in bc[bucket].items():
            out.append(f"{label} {var} {v:+.1f}°F bias")
    sl = ((calib or {}).get("sigma") or {}).get("by_lead") or {}
    for bucket in sorted(sl, key=lambda b: int(b)):
        label = names.get(str(bucket), f"{bucket}h")
        for var, v in sl[bucket].items():
            out.append(f"{label} {var} σ={v:.1f}")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_accuracy.py::test_active_corrections_lists_live_knobs -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Render it in the dashboard**

In `market_view.py`, add the import after the existing `import model` (line 16):

```python
import calibration
import model
```

Change the `_render_accuracy` signature (line 420) from:

```python
def _render_accuracy(load_accuracy):
```

to:

```python
def _render_accuracy(load_accuracy, calib=None):
```

Then immediately after `bt, live = load_accuracy()` (line 423), insert:

```python
    corr = calibration.active_corrections(calib)
    if corr:
        st.markdown("**Active self-corrections** — adjustments the model has "
                    "learned from its own settled forecasts and is applying now: "
                    + "; ".join(corr) + ".")
```

Finally, update the call site in `render_page` (line 587) from:

```python
        _render_accuracy(load_accuracy)
```

to:

```python
        _render_accuracy(load_accuracy, calib)
```

- [ ] **Step 6: Verify the app imports cleanly**

Run: `.venv/bin/python -c "import market_view, calibration; print('import ok')"`
Expected: `import ok`

- [ ] **Step 7: Commit**

```bash
git add calibration.py market_view.py tests/test_accuracy.py
git commit -m "feat: show Active self-corrections in the accuracy panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Full-suite regression + live smoke check

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass, including the 6 new ones (`test_per_lead_bias_*`, `test_bias_correction_block_wraps_scoring`, `test_lead_bias_correction_shifts_consensus`, `test_lead_bias_skipped_when_observed`, `test_active_corrections_lists_live_knobs`). No failures, no errors.

- [ ] **Step 2: Smoke-check the live feedback on the real log**

Run:
```bash
.venv/bin/python -c "import scoring; print('per_lead_bias:', scoring.per_lead_bias())"
```
Expected: a dict (likely `{}` today, since day-ahead has < MIN_LEAD_DAYS=10 settled days — confirms the gate holds and the loop is correctly dormant, not erroring). If it returns a bucket, eyeball that the sign is negative for the warm day-ahead high (a downward correction).

- [ ] **Step 3: Confirm `model.snapshot` still runs end-to-end**

Run:
```bash
.venv/bin/python -c "import calibration, model; c=calibration.get(refresh=True); s=model.snapshot(c); print('snapshot ok, keys:', sorted(s.keys()))"
```
Expected: prints `snapshot ok, keys: [...]` with no traceback (proves the new `bias_correction` knob round-trips through calibration.json and model without breaking the live pipeline).

- [ ] **Step 4: Commit any incidental cleanup** (only if Steps 1–3 surfaced a fix; otherwise skip)

```bash
git add -A
git commit -m "test: verify self-correction layer end-to-end

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes on scope (loops 2–4)

Per the design, only the framework + Loop 1 (lead-time bias) are built live. Loops 2–4 — live group re-weighting, reliability sharpening, market blend — are **not** implemented as inert code (that would be dead weight). Instead the machine is left ready for them: each is a sibling knob that slots into `_bias_correction()`'s pattern (compute behind the lazy scoring import, gate on settled-day count), gets surfaced automatically once added to `active_corrections()`, and is applied in `predict_variable` in the documented order (re-weight → de-bias → market blend → sigma → sharpen). When their data matures (Loop 4 needs settled CLI market rows; today `market_accuracy` is n=0), add each as its own brainstorm → spec → plan cycle.
