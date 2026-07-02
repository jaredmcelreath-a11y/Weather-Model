# Low Settlement-Offset Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the conditional low settlement offset from trusting a noisy, tiny-sample `clear_calm` bucket by hardening the `_var_bucket` gate.

**Architecture:** Two changes to `_var_bucket` in `calibration.py`: raise the clear/calm count floor (`min_nights` 5→12) and replace the fixed 0.25°F separation floor with a quantization-aware significance test (`|Δmean| ≥ 2·SE_diff`, each bucket's SE floored by a `1/√12` per-sample prior). The `other`/high paths and the flat-fallback contract are unchanged.

**Tech Stack:** Python 3, pytest, project-local `.venv`.

## Global Constraints

- Settlements (CLI and hourly daily extremes) are stored as **rounded whole degrees**; the per-day gap is quantized to `{0, −1}`. The gate must not trust sub-degree structure that rounding cannot express.
- Gate math uses **raw (unrounded)** bucket means/stds; the *emitted* offset keeps using `_mean_std` (2 dp rounded), matching existing convention.
- Not-passed must return the flat mean + flat std for both buckets (preserve CLI-basis spread; never zero it).
- Do not change high behavior (already flat in live calibration), `CLEAR_CLOUD_MAX`/`CALM_WIND_MAX`, `PEAK_LOCK_DROP`, or the raw low bias.
- Run pytest via `.venv/bin/python -m pytest`.

---

### Task 1: Harden the gate (TDD)

**Files:**
- Modify: `calibration.py` — add `_QUANT_PRIOR` constant + `_sep_se` helper; change `_var_bucket` signature and gate; update `_conditional_settlement_offset` defaults.
- Test: `tests/test_conditional_offset.py` — add one regression test; update five existing tests to the new thresholds.

**Interfaces:**
- Consumes: `_mean_std(xs) -> (mean2dp, std2dp)` (existing).
- Produces:
  - `_QUANT_PRIOR: float = 1.0 / math.sqrt(12)` (module constant).
  - `_sep_se(gaps_cc: list[float], gaps_ot: list[float]) -> float` — SE of the difference of bucket means; each bucket SE = `hypot(raw_pop_std, _QUANT_PRIOR)/sqrt(n)`; empty bucket → `math.inf`.
  - `_var_bucket(gaps_cc, gaps_ot, min_nights, margin, sep_z) -> (cc_mean, ot_mean, cc_std, ot_std, passed)` — `min_sep` param replaced by `sep_z` (SE multiplier).
  - `_conditional_settlement_offset(cli, hourly, cond, min_nights=12, margin=0.02, sep_z=2.0) -> dict | None`.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_conditional_offset.py`:

```python
def test_thin_quantized_low_split_falls_back_to_flat():
    # Reproduces 2026-07-02: 7 clear/calm nights (5x -1, 2x 0) and 38 other
    # nights (10x -1, 28x 0). The -0.75 clear_calm mean is overfit to rounding
    # noise on a tiny sample, so the split must NOT be emitted -> flat fallback.
    cc_lows = [-1.0] * 5 + [0.0] * 2                 # 7 clear/calm
    ot_lows = [-1.0] * 10 + [0.0] * 28               # 38 other
    cli, hourly, cond = {}, {}, {}
    day = date(2026, 5, 1)
    for gap in cc_lows:
        hourly[day] = (90.0, 70.0)
        cli[day] = (91.0, 70.0 + gap)                # high gap +1 (flat)
        cond[day] = (10.0, 5.0)                       # clear + calm
        day += timedelta(days=1)
    for gap in ot_lows:
        hourly[day] = (90.0, 70.0)
        cli[day] = (91.0, 70.0 + gap)
        cond[day] = (80.0, 20.0)                      # cloudy + windy
        day += timedelta(days=1)
    # Split rejected on the count floor (7 < 12) -> None -> caller uses flat.
    assert _conditional_settlement_offset(cli, hourly, cond) is None
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py::test_thin_quantized_low_split_falls_back_to_flat -v`
Expected: FAIL — current code returns a bucketed dict (n_cc=7 ≥ old min_nights 5), not `None`.

- [ ] **Step 3: Add the constant and SE helper**

In `calibration.py`, near the other private helpers (above `_var_bucket`, after `_mean_std`), add:

```python
# Whole-degree settlements quantize each daily gap to {0, -1}; a bucket's true
# per-sample noise is at least the rounding noise (uniform over 1 degree ->
# std 1/sqrt(12)). Floor each bucket's SE by it so a lucky zero-variance bucket
# cannot drive the separation test's denominator to zero.
_QUANT_PRIOR = 1.0 / math.sqrt(12)


def _sep_se(gaps_cc: list[float], gaps_ot: list[float]) -> float:
    """Standard error of the difference in bucket means, each bucket's SE
    floored by the quantization prior. An empty bucket has infinite SE (no
    separation can be established), which the caller reads as 'do not split'."""
    def se(g: list[float]) -> float:
        if not g:
            return math.inf
        m = sum(g) / len(g)
        sd = (sum((x - m) ** 2 for x in g) / len(g)) ** 0.5
        return math.hypot(sd, _QUANT_PRIOR) / math.sqrt(len(g))
    return math.hypot(se(gaps_cc), se(gaps_ot))
```

(`math` is already imported in `calibration.py`.)

- [ ] **Step 4: Change the `_var_bucket` signature and gate**

Replace the `min_sep` parameter with `sep_z` and swap the fixed separation test for the SE-scaled one. In `_var_bucket`:

Signature line becomes:

```python
def _var_bucket(
    gaps_cc: list[float], gaps_ot: list[float],
    min_nights: int, margin: float, sep_z: float,
) -> tuple[float, float, float, float, bool]:
```

Replace the `passed = (...)` assignment with:

```python
    # Separation must exceed sampling noise, not a fixed degree floor: with
    # {0,-1}-quantized gaps a 0.45 gap between two small buckets is easily
    # produced by rounding. SE_diff carries the quantization prior, so noise-
    # driven splits are rejected even once counts are high.
    se_diff = _sep_se(gaps_cc, gaps_ot)
    passed = (n_cc >= min_nights
              and abs(cc_raw - ot_raw) >= sep_z * se_diff
              and resid_cond <= resid_flat - margin)
```

Update the `_var_bucket` docstring's gate description to: "`passed` is True only
when there are >= `min_nights` clear/calm nights, the two bucket means differ by
at least `sep_z` standard errors of their difference (SE floored by the
quantization prior), AND splitting reduces the mean absolute residual vs a single
flat mean by at least `margin`." Note the residual-margin check is now a
belt-and-suspenders guard largely subsumed by the significance test.

- [ ] **Step 5: Update `_conditional_settlement_offset` defaults and call**

Change its signature defaults and the `_var_bucket` call:

```python
def _conditional_settlement_offset(cli: dict, hourly: dict, cond: dict,
                                   min_nights: int = 12, margin: float = 0.02,
                                   sep_z: float = 2.0) -> dict | None:
```

and the loop body call:

```python
        cm, om, cs, os_, passed = _var_bucket(cc[var], ot[var], min_nights,
                                              margin, sep_z)
```

- [ ] **Step 6: Update the five existing tests to the new thresholds**

In `tests/test_conditional_offset.py`:

`_days(n)` already generates consecutive days from 2026-05-01; the emit/similar/
margin tests use `_days(16)` with `i < 8` as the clear/calm split. Bump those to
`_days(24)` with `i < 12` so each bucket has 12 nights (clears the new count
floor). Concretely:

- `test_emits_buckets_when_low_gap_differs_and_enough_nights`: change `days =
  _days(16)` → `_days(24)` and `clear = i < 8` → `clear = i < 12`. Gaps stay
  −0.8 (clear) / −0.2 (other); zero within-bucket noise → `SE_diff =
  hypot(_QUANT_PRIOR/√12, _QUANT_PRIOR/√12) ≈ 0.118`, `2·SE_diff ≈ 0.236 < 0.6`
  separation → still emits. Assertions unchanged.
- `test_unsplit_variable_keeps_flat_gap_std`: `_days(16)` → `_days(24)`, `i < 8`
  → `i < 12`. `high_gap` pattern `0.5 if i % 2 == 0 else 1.5` unchanged (mean 1.0
  both buckets, std 0.5). Assertions unchanged.
- `test_returns_none_when_too_few_clear_calm_nights`: change the comment to
  "only 3 clear/calm (< 12)"; logic already yields None. Leave `_days(10)`/`i<3`.
- `test_returns_none_when_buckets_too_similar`: `_days(16)` → `_days(24)`, `i < 8`
  → `i < 12`. Gaps −0.45 (clear) / −0.40 (other): separation 0.05 < `2·SE_diff`
  ≈ 0.236 → None. Assertion unchanged.
- `test_returns_none_when_split_fails_margin_gate`: `_days(16)` → `_days(24)`,
  `i < 8` → `i < 12`; extend the noise arrays to 12 entries each: `cc_gaps =
  [10.3, -9.7] * 6` (mean +0.3) and `ot_gaps = [9.7, -10.3] * 6` (mean −0.3);
  change `ot_gaps[i - 8]` → `ot_gaps[i - 12]`. Huge within-bucket noise inflates
  `SE_diff` so the split is rejected (now via the significance test as well as
  the margin). Assertion (`is None`) unchanged.

- [ ] **Step 7: Run the full conditional-offset test file**

Run: `.venv/bin/python -m pytest tests/test_conditional_offset.py -v`
Expected: PASS — all tests including the new regression test.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — no regressions elsewhere.

- [ ] **Step 9: Commit**

```bash
git add calibration.py tests/test_conditional_offset.py
git commit -m "fix: gate the low settlement split on evidence, not rounding noise

Raise min_nights 5->12 and replace the fixed 0.25 separation floor with a
quantization-aware significance test (|dmean| >= 2*SE_diff, each bucket SE
floored by a 1/sqrt(12) prior). Stops the clear_calm low offset trusting a
-0.75 shift built from 7 quantized {0,-1} samples.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Regenerate the live calibration

**Files:**
- Modify: `calibration.json` (regenerated artifact).

**Interfaces:**
- Consumes: the hardened `calibration` module from Task 1.

- [ ] **Step 1: Regenerate calibration.json**

Run:

```bash
.venv/bin/python -c "
import json, calibration
c = calibration.compute() if hasattr(calibration, 'compute') else calibration.get(refresh=True)
json.dump(c, open('calibration.json','w'), indent=2)
print(json.dumps(c['settlement_offset'], indent=2))
"
```

(Use whichever of `compute`/`get(refresh=True)` the module exposes — grep
`^def ` in `calibration.py` to confirm the public entry point before running.)

- [ ] **Step 2: Verify the low offset is now flat**

Confirm the printed `settlement_offset.low`: `clear_calm == other` (both ≈ −0.33)
and no divergence. High unchanged (≈ 0.91 flat).

- [ ] **Step 3: Commit**

```bash
git add calibration.json
git commit -m "chore: regenerate calibration.json (flat low settlement offset)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** Change 1 (min_nights 5→12) → Task 1 Steps 4–5. Change 2
(SE-scaled separation + quantization prior) → Task 1 Steps 3–4. High-unchanged /
flat-fallback contract → preserved (only the `passed` predicate changes).
Regression test → Task 1 Step 1. Existing-test updates → Task 1 Step 6.
calibration.json regeneration → Task 2. All spec sections covered.

**Placeholder scan:** none — all code and commands are concrete. The one
conditional ("`compute`/`get(refresh=True)`") is an explicit verify-before-run
instruction, not a TODO.

**Type consistency:** `_var_bucket` returns the same 5-tuple; only the 5th
positional param name changes (`min_sep`→`sep_z`). `_sep_se` takes two lists,
returns float. `_conditional_settlement_offset` still returns `dict | None`.
Consistent across tasks.
