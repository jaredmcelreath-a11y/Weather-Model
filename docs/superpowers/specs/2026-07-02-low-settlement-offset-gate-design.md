# Tighten the low settlement-offset split gate

**Date:** 2026-07-02
**Status:** approved, pending implementation
**Area:** `calibration.py` (`_var_bucket`), `tests/test_conditional_offset.py`

## Problem

The two-bucket conditional settlement offset (`_conditional_settlement_offset` →
`_var_bucket`) splits the CLI−hourly daily-extreme gap into a `clear_calm` bucket
and an `other` bucket, and applies each as a distribution shift to convert the
model's hourly-basis forecast onto Kalshi's continuous-CLI settlement basis.

On 2026-07-02 the model showed KDFW's daily **low** at 77–78°F all morning while
Kalshi (and the realized value) sat at 79–80. The realized continuous low was
78.8°F (rounds to 79). Three things stacked to make the model cold, but the one
this spec addresses is the settlement offset: the live calibration applied a
**−0.75°F** `clear_calm` low shift, dragging the displayed low further below the
market on exactly the kind of warm-clear night where the raw forecast already
leaned cold.

### Root cause

The `clear_calm` low offset is overfit to rounding noise on a tiny sample.

The CLI and hourly daily lows are both stored as **rounded whole degrees**, so
the per-day gap is quantized to `{0, −1}` — there is no sub-degree signal. Over
the 45-day calibration window (verified against the live sources):

| bucket      | low gaps                         | mean   | n  |
|-------------|----------------------------------|--------|----|
| clear_calm  | `[-1,-1,-1,-1,-1, 0, 0]`         | −0.71  | 7  |
| other       | 10× −1, 28× 0                    | −0.26  | 38 |
| flat (all)  | —                                | −0.33  | 45 |

The emitted `clear_calm` = −0.75 rests on **7 quantized samples where 5 happened
to round to −1**. Flipping one day moves the mean by 0.14. The *physical*
continuous-vs-hourly gap on 2026-07-02 was ~0.2°F (79.0 hourly vs 78.8
continuous) — it only ever surfaces as a whole −1 when the trough straddles a
rounding boundary.

The gate in `_var_bucket` passed this split because all three of its current
conditions cleared on thin data:

- `n_cc = 7 ≥ min_nights (5)` ✓
- `|cc_raw − ot_raw| = 0.45 ≥ min_sep (0.25)` ✓
- `resid_cond ≤ resid_flat − margin (0.02)` ✓ — but splitting *always* reduces
  in-sample residual; that is the overfit, not evidence.

The physical **sign** is plausible (clear/calm nights fall faster → sharper,
briefer trough that the top-of-hour METAR misses → continuous digs lower), so the
mechanism should not be deleted — only prevented from trusting noise.

The **high** bucket already falls back to flat in the live calibration
(0.911/0.911), so the conditional split currently affects only the low.

## Goal

Harden `_var_bucket` so a thin, quantized low split falls back to the flat offset,
while a genuinely well-supported, condition-dependent gap can still earn its way
in as clean data accumulates. Do not change the high behavior (already flat).

## Design

Two changes to `_var_bucket` in `calibration.py`.

### Change 1 — raise the count floor

`min_nights` default **5 → 12** (the caller `_conditional_settlement_offset`
passes `min_nights=5` today; bump the default and the call site together).

Rationale: pinning a Bernoulli-ish `{0, −1}` mean to ±0.15 at one standard error
with p≈0.3 needs n ≈ 9–10; rounding up for the quantization prior lands at 12. A
regime too rare to muster 12 clean nights should not get its own confidently
applied offset. Today's `clear_calm` n=7 fails this floor → low falls back to the
flat −0.33 (removes ~0.42°F of spurious downward pull on warm-clear nights).

### Change 2 — replace the fixed separation floor with a noise-aware one

Replace the current fixed `min_sep` test (`|cc_raw − ot_raw| ≥ 0.25`) with a
significance test:

```
keep the split only if  |cc_mean − ot_mean| ≥ z · SE_diff
```

where

```
SE_bucket = hypot(bucket_std, Q) / sqrt(n_bucket)      # per bucket
SE_diff   = hypot(SE_cc, SE_ot)
z         = 2.0
Q         = 1 / sqrt(12) ≈ 0.289                       # quantization prior
```

`Q` is a per-sample noise floor added in quadrature so a lucky zero-variance
bucket (e.g. all −1) cannot drive `SE→0` and sail through on a degenerate sample.
This gate is inherently quantization-aware: the `{0, −1}` Bernoulli variance
inflates `SE_diff`, so noise-driven separations are rejected even once counts are
high.

The existing residual-margin check (`resid_cond ≤ resid_flat − margin`) **stays**
as a third guard. Bucket means/stds continue to use `_mean_std` (rounded) for the
emitted offset; the gate math continues to use raw (unrounded) bucket means, as
today.

### Resulting gate

`passed` is True only when **all** hold:

1. `n_cc ≥ 12` (count floor — Change 1)
2. `|cc_mean − ot_mean| ≥ 2.0 · SE_diff` (significance — Change 2)
3. `resid_cond ≤ resid_flat − margin` (residual margin — unchanged)

Not-passed still returns the flat mean/flat-std fallback for both buckets exactly
as today (preserving the CLI-basis spread so the model is not overconfident).

### Signature / parameters

`_var_bucket(gaps_cc, gaps_ot, min_nights, margin, min_sep)` →
`_var_bucket(gaps_cc, gaps_ot, min_nights, margin, sep_z)`. The `min_sep`
parameter (fixed-degree separation) is replaced by `sep_z` (SE multiplier,
default 2.0); `Q` is a module-level constant. `_conditional_settlement_offset`'s
`min_sep=0.25` default becomes `sep_z=2.0`, and its `min_nights` default becomes
12.

## Blast radius

- **High offset:** unchanged — already flat; tightening cannot make it pass.
- **Live model:** next calibration run emits a flat low offset (−0.33) instead of
  the −0.75/−0.3 buckets. `calibration.json` must be regenerated after merge (or
  will update on its next scheduled compute) for the deployed model to pick it up.
  This is an explicit implementation step, not an automatic side effect of the
  code change.
- **Tests:** five tests in `tests/test_conditional_offset.py` use 8 nights/bucket
  and assume `min_nights=5`; they are updated to ≥12 nights/bucket so each still
  exercises its intended gate. The model/backtest tests pass the offset dict
  directly and are unaffected.

## Testing

**New regression test (write first, must fail before the change):**
Reproduce the 2026-07-02 data — 7 `clear_calm` nights (5× −1, 2× 0) and 38
`other` nights (10× −1, 28× 0) — and assert `_conditional_settlement_offset`
returns `None` (flat fallback). Locks in the fix against the exact scenario.

**Updated existing tests** (bump to ≥12 nights/bucket, intent preserved):

- `test_emits_buckets_when_low_gap_differs_and_enough_nights` — split still emits
  when well-supported and clearly separated (use 12 clear + 12 other, gaps with
  enough separation to clear `2·SE_diff`).
- `test_unsplit_variable_keeps_flat_gap_std` — unsplit high keeps flat std.
- `test_returns_none_when_too_few_clear_calm_nights` — now `< 12`.
- `test_returns_none_when_buckets_too_similar` — separation below `2·SE_diff`.
- `test_returns_none_when_split_fails_margin_gate` — a high within-bucket-noise
  split is still rejected. Note: under the new gate this case is now also caught
  by the significance test (large noise inflates `SE_diff`), so the residual
  margin becomes a redundant belt-and-suspenders guard rather than the sole
  decider. The test asserts the split is rejected (returns `None`); it no longer
  isolates the margin guard specifically. Keep the margin check — it is harmless
  and documents intent — unless implementation shows it is fully unreachable, in
  which case removing it is acceptable and should be noted.

**Verification:**
1. New regression test red → green.
2. Full `pytest` green.
3. Regenerate `calibration.json`; confirm `settlement_offset.low.clear_calm ==
   settlement_offset.low.other` (flat, ~−0.33) and no `clear_calm`/`other`
   divergence.

## Out of scope

- Changing the raw low forecast bias (the larger contributor to today's miss).
- Changing the peak/low lock threshold (`PEAK_LOCK_DROP`) — a separate, known
  source of the morning lag.
- Recomputing the offset from unrounded/continuous historical extremes (would
  remove the quantization entirely but requires historical sub-hourly data the
  archive does not retain).
- Any change to `CLEAR_CLOUD_MAX` / `CALM_WIND_MAX` (shared with the cooling
  offset).
