# Warm-night low bias correction

**Date:** 2026-07-02
**Status:** approved, pending implementation
**Area:** `calibration.py`, `model.py`, `config.py`, tests

## Problem

The model's daily **low** forecast has a state-dependent bias that the flat
calibration hides. Measured over the live 45-day window (consensus low forecast
vs realized low, `+` = model warm, `−` = model cold):

| regime (realized low) | mean error | std  | n  |
|-----------------------|-----------|------|----|
| overall               | −0.09     | 1.23 | 45 |
| warm (≥76)            | **−0.72** | 0.61 | 19 |
| cool (<76)            | +0.37     | 1.37 | 26 |

The warm and cool leans cancel to −0.09, so the flat bias (`−0.08`) corrects
nothing. The warm-night cold lean is the exploitable one: it is ~5σ, has a tight
spread, and covers exactly the July KDFW trading regime (lows 76–81). Physically,
on warm humid nights the boundary layer stays mixed/moist and the temperature does
not radiate down as far as the smooth NWP fields predict.

Conditioned on the **forecast** low (usable at forecast time; weaker than the
realized-low split due to regression to the mean, but still solid):

| forecast low ≥ | mean error | SE   | n  |
|----------------|-----------|------|----|
| 75             | −0.45     | 0.23 | 20 |
| 76             | −0.44     | 0.27 | 17 |
| 77             | −0.62     | 0.19 | 13 |

This motivated a review after the 2026-07-02 low miss (model 77–78 all morning
vs realized/Kalshi 79). The settlement-offset gate fix
([[2026-07-02-low-settlement-offset-gate-design]]) was the minor contributor;
this cold lean was the larger one.

The clear/calm split is a **null** here (−0.09 both buckets) — the driver is airmass
warmth, not sky/wind — so this does not overlap the cooling offset.

## Goal

Add a gated, warm-bucket low-bias correction that lifts the forecast low on warm
nights, activates only when the data supports it, and self-disables if the
warm/cool split turns out to be May–June seasonality. Do not touch the high, the
cool-night side, or the existing lead-time loop.

## Non-goals / out of scope

- **Dewpoint-depression predictor.** Physically the true driver, but dewpoint is
  not fetched by any source today; plumbing it into every historical + live fetch
  is a separate project. Use forecast-low **level** now; note dewpoint as a future
  refinement.
- Cool-night warm-lean correction (smaller, noisier, off-regime).
- High-side regime bias.
- Any change to the lead-time `by_lead` loop or the cooling offset.

## Design

### Form and predictor

A single warm-bucket correction on the **low only**, keyed off the model's own
forecast low. When the forecast low ≥ `WARM_LOW_THRESHOLD`, subtract the measured
(signed, negative) warm-night lean from the sample pool — a constant upward shift.

New config constant: `config.WARM_LOW_THRESHOLD = 76`. Chosen to cover the July
regime while giving the gate n=17. Higher = cleaner signal, narrower coverage;
this is a tunable knob, not load-bearing.

### Where it is measured — `calibration.py`

Computed from the **45-day calibration window** (same source as the flat bias,
cooling, and settlement offset), NOT the forward log — it needs the fuller sample,
and `compute()` already has `fcst` and `actual` in hand.

New helper:

```
_warm_low_bias(fcst, actual, overall_low_bias, threshold=WARM_LOW_THRESHOLD)
    -> {"threshold": int, "bias": float}  |  {}
```

Algorithm:
1. For each day with both a forecast and an actual, residual
   `r = consensus_low − actual_low` (consensus = mean of member lows, identical to
   the flat-bias computation).
2. Warm nights = those whose **forecast** low `consensus_low ≥ threshold`.
3. `warm_extra = mean(warm residuals) − overall_low_bias`. Subtracting the overall
   bias makes the correction the *leftover* warm-night lean beyond what the flat
   bias already removes — orthogonal to the flat bias by construction, so the two
   never double-count.
4. Gate (reuses the existing self-correction constants from `scoring.py` via a
   lazy import — single source of truth): require `n_warm ≥ MIN_LEAD_DAYS (10)`;
   significance `|warm_extra| > SIG_Z (1.0) · σ_warm / √n_warm` where `σ_warm` is
   the population std of the warm residuals (reuse `calibration._mean_std`); else
   return `{}`.
5. Emit the **shrunk** value `warm_extra · n_warm / (n_warm + SHRINK_K (8))`,
   rounded 2 dp, as `{"threshold": threshold, "bias": <value>}`. Sign is negative
   (model cold on warm nights); the model subtracts it, adding warmth.

`compute()` wires it in: after `bias` is computed, call `_warm_low_bias(fcst,
actual, bias["low"])` and merge the result into the `bias_correction` dict already
built by `_bias_correction()`:

```
bias_correction = _bias_correction()                 # {"by_lead": {...}}
wl = _warm_low_bias(fcst, actual, bias["low"])
if wl:
    bias_correction["warm_low"] = wl
```

So `calibration.json` gains `bias_correction.warm_low = {"threshold": 76,
"bias": -0.4x}` when the gate passes, and omits it otherwise.

### Orthogonality with the lead-time loop

The `by_lead` loop is measured on the **forward log** (`scoring.score`), a
different dataset on a different axis (lead time, not airmass). The warm-low term
is measured net of the flat bias, not net of the lead bias, because they live in
different datasets and cannot be per-day differenced. Both are individually
shrunk and gated, and the lead loop is currently dormant. The residual overlap
(a warm night that is also, say, day-ahead) is second-order; if the two ever
visibly fight once the lead loop reactivates, revisit then. Documented, accepted.

### Where it is applied — `model.py`

In `predict_variable`, **pure-forecast path only** (`obs_now is None`), **low
only** — identical guard to the cooling offset and the lead correction. Once
observations anchor the day, the realized low supersedes any bias term, so this
cannot fight the lock.

The regime is judged on the consensus captured **before** the CLI settle-shift
block (`model.py:418`), so the largest and most systematic basis gap (the ~0.36°F
low settle-shift) does not blur the threshold:

```
# capture just before the settle-shift block at model.py:418
regime_low = (sum(samples) / len(samples)) if samples else None
```

(`model.py` imports `math`, not `statistics`, and has no `_mean` helper — use the
inline mean.) At this point `samples` already carry the model's per-source flat
de-bias and, on clear/calm nights, the cooling offset. The calibration threshold
was measured on the raw member-consensus low, so the two bases differ by the flat
low bias (~−0.08) plus, on clear/calm nights, the cooling offset (−0.15) — ≤~0.15°F
total, immaterial against a coarse, tunable 1°F threshold where the lean is ~flat
across 76–79. Excluding the settle-shift is the one gap worth removing explicitly.

Then, alongside the existing lead-bias block (after σ is finalized, so the shift
is constant and leaves σ / locked_ratio unchanged, and before probabilities/mean
are formed so point + bins move together):

```
wl = (calib or {}).get("bias_correction", {}).get("warm_low") or {}
if (wl and variable == "low" and obs_now is None
        and regime_low is not None and regime_low >= wl["threshold"]):
    samples = [s - wl["bias"] for s in samples]      # bias<0 -> warms the low
```

### Interaction with the cooling offset

The cooling offset (−0.15, clear/calm nights) nudges the low **down**; the warm-low
correction nudges it **up** on warm nights. Different axes (sky/wind vs warmth),
and the clear/calm regime split was null, so they do not conflict. On a night that
is both warm and clear/calm the two apply independently and the net shift is their
algebraic sum — locked by a test.

### Dashboard

`calibration.active_corrections` already lists `bias_correction.by_lead` knobs.
Extend it to surface `warm_low` as one line (e.g. `"warm low (≥76°F) +0.4°F"`) so
the trader can see when it is live. Sign shown as applied (warming = `+`).

## Testing

**`calibration._warm_low_bias` (unit):**
- Passes on a synthetic warm-cold-lean window: warm nights (fc ≥ 76) run cold,
  cool nights neutral → emits `{"threshold": 76, "bias": <negative, shrunk>}`.
- `warm_extra` is measured net of `overall_low_bias` (orthogonality): a window
  with a large flat bias but no *extra* warm lean emits `{}`.
- Returns `{}` when fewer than 10 warm nights.
- Returns `{}` when the warm lean is statistically insignificant (large σ_warm).
- Applies shrinkage `n/(n+8)` to the emitted value.

**`model.predict_variable` (unit, monkeypatched calib):**
- Warm night (forecast low ≥ threshold, pure-forecast) subtracts `bias` → low
  consensus rises by `|bias|`.
- Cool night (forecast low < threshold) → unchanged.
- Obs-anchored path (`obs_now` set) → correction skipped.
- Regime judged pre-settle-shift: a forecast low that sits above the threshold
  before the settle-shift but would fall below it after still fires.
- Combined with the cooling offset on a warm + clear/calm night → net shift is the
  algebraic sum of the two.
- High variable → never touched.

**Integration:** a synthetic warm-cold-lean archive flows `compute()` →
`calibration.json` → `predict_variable` and lifts the warm-night low; the high is
unchanged.

**Verification:**
1. New tests red → green; full `pytest` green.
2. Regenerate `calibration.json`; confirm `bias_correction.warm_low` is present
   with a negative `bias` (~−0.4) and `threshold` 76, and `active_corrections`
   lists it.
3. Spot-check: today's warm-night forecast low rises by ~|bias| vs pre-change.
