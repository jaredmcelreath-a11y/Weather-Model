# Storm-proof self-corrections — design

**Date:** 2026-07-13
**Status:** approved design, pending implementation plan

## Problem

The self-correction estimators in `scoring.py` are plain means over every settled
forward-log record ever, so a few storm-night outliers steer them:

- `per_lead_bias` currently emits a lead-0 low correction of **−0.33°F that exists
  only because of three convective nights** (June 26–28, errors +3.7/+2.7/+3.6);
  the median error of the same 21 records is 0.0.
- The same outliers inflate the empirical lead-0 low sigma (1.25 vs a calm-night
  value well under 1).
- There is **no window**: a record from June counts forever, so unflagged outliers
  never age out and a summer-calibrated bias would keep steering winter forecasts.
- Storm/front regime days are already handled *live* by the convective sigma floor
  and the front guard; averaging their aftermath into calm-day corrections
  double-counts a regime the model already treats conditionally.

## Decision summary

| Question | Decision |
|---|---|
| Estimator fix | **Hybrid** — bias uses the median; sigma stays an honest std but over a flag-excluded pool |
| Aging | **45-day rolling window** for the correction estimators (reuse `CALIBRATION_WINDOW_DAYS`); the accuracy scoreboard stays all-time |
| Flag scope | `forecast_log` records **and** `betting_log` rows; dashboard shows the exclusion count |

## Mechanism

### 1. Flag logging (attribution layer)

- `forecast_log.record`: each per-variable record gains `"convective_widened": true`
  and/or `"front_widened": true`, copied from the prediction dict — **written only
  when true**, so calm-day rows are byte-identical to today and historical rows
  read as unflagged via `.get()`. No schema migration.
- `betting_log._row`: gains the same two fields from the CLI prediction dict
  (always written there — betting rows are few and the join analysis wants explicit
  values). This enables the front-guard margin recalibration in 2–4 weeks: join
  flag fires against whether the evening actually undercut the morning min.

### 2. Correction residual pool (`scoring.py`)

New helper `_correction_residuals(today, basis) -> dict[(lead_bucket, variable), list[float]]`:
signed errors `consensus − actual` over settled records that are

- within the last `CALIBRATION_WINDOW_DAYS` (45) of `today` (by `target_date`), and
- **not flagged** (`rec.get("convective_widened") or rec.get("front_widened")` is falsy).

The pool is used by both estimators below. It is separate from `score()`'s
scoreboard path on purpose.

### 3. `per_lead_bias` — median + adjusted gate

- Point estimate: **`statistics.median`** of the pool (was: mean). The median plus
  the flag exclusion is belt-and-suspenders: flags catch attributed regime days,
  the median catches any the flags missed — including the unflagged June 26–28
  history, which fixes the −0.33 phantom immediately.
- Shrinkage unchanged: `median × n/(n + SHRINK_K)`.
- Significance gate keeps its form but uses the **median's standard error**:
  `SE = 1.2533 × sd/√n` (sd = std of the pool). Using the mean's SE with a median
  estimator would make the gate easier to pass — the wrong direction.
- `MIN_LEAD_DAYS` (≥10) now counts pool records (windowed, unflagged).

### 4. `per_lead_sigma` — std over the pool

- **Std, not a robust scale estimator**, computed over the windowed + flag-excluded
  pool, centered on the pool mean. Day-ahead misses on days that *turned out*
  stormy are legitimate lead-time uncertainty and stay in (the flags only ever mark
  same-day locked records, so this falls out naturally).
- `MIN_LEAD_DAYS` gate as above.

### 5. Scoreboard untouched

`score()` — exact-bin %, within-1, Brier, reliability, `by_lead` display stats,
and `market_accuracy` — remains **all-time and unfiltered**. Its `by_lead`
`bias`/`sigma` fields stay as raw diagnostics; the correction estimators simply no
longer read them. `calibration.py` is unchanged (same `per_lead_bias`/
`per_lead_sigma` interface).

### 6. Dashboard exclusion note

New accessor `scoring.correction_exclusions(today=None, basis="cli") -> int`:
the number of distinct flagged settled records inside the window (the ones the
pool dropped). `market_view._render_accuracy` shows, next to the Active
self-corrections line and only when N > 0:
"Correction estimators exclude N storm/front-flagged record(s) from the last 45 days."

## Interactions verified

- **Front guard / convective floor:** unchanged. This design consumes their flags;
  it never feeds back into them.
- **Warm-low and cooling offsets** (`calibration.py`, archive-based means over the
  45-day archive window): out of scope — they already age via the rolling archive
  window; robustifying them is a possible follow-up, not part of this change.
- **Feedback loop note:** logged records reflect whatever corrections were active
  at capture time; recording flags does not resolve that (roadmap item 6's
  "record active corrections per row" remains separate).

## Expected effect on live data (validation gate)

Re-running the new estimators offline against the data-branch logs must show:

1. Lead-0 low correction **disappears** (median 0.0; the old mean-based path gave
   −0.33).
2. Lead-24 high correction **survives** (the day-ahead warm bias is consistent
   across days, not outlier-driven; its median stays near +1 and passes the gate).
3. Lead-0 low sigma is **unchanged today** (the June outliers are unflagged and
   still inside the 45-day window as of 2026-07-13) — the validation instead
   asserts the window mechanism directly: recomputing with `today` set to
   2026-08-15 (June 26–28 aged out) must show the lead-0 low sigma dropping from
   ~1.25 toward its calm-night value, proving the contamination self-heals.

## Testing

Unit tests (`tests/test_correction_robustness.py` or extend `tests/test_accuracy.py`):

1. Median immunity — 18 near-zero + 3 large positive residuals → no correction
   emitted (gate fails / value ~0); the same pool through a mean would emit one.
2. Consistent bias survives — 20 residuals clustered near +1 → correction ≈
   +1 × 20/28, gate passes with the median SE.
3. Window — a record older than 45 days is not in the pool; a newer one is.
4. Flag exclusion — records with `convective_widened`/`front_widened` true are
   dropped from the pool (bias and sigma); unflagged records with the key absent
   are kept.
5. `forecast_log.record` writes the flags only when true; calm rows have no key.
6. `betting_log._row` carries both fields.
7. `correction_exclusions` counts only flagged records inside the window.
8. Dashboard note renders only when the count is positive (mirror the existing
   accuracy-view test idiom).

Offline validation script (like the front-guard replay, artifacts to
`docs/benchmarks/<date>/robust-corrections/`): before/after `per_lead_bias` /
`per_lead_sigma` on the real data-branch logs, checked against the three expected
effects above.

## Out of scope

- Robustifying the archive-based warm-low/cooling estimators.
- Recording *active corrections* per log row (roadmap item 6).
- Any change to the convective floor or front guard themselves.
