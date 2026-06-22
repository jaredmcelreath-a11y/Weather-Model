# Design: Skill-weighted consensus + conditional settlement offset

Date: 2026-06-22
Status: Approved (pending written-spec review)

## Goal

Improve KDFW low-temperature accuracy, especially on the Kalshi (NWS CLI) basis,
via two low-risk, data-driven changes — each gated behind an out-of-sample (OOS)
check so neither can regress accuracy:

1. **Skill-weighted consensus (group rebalancing)** — replace the equal-weight
   mean over the pooled forecast samples with a weight per *source system*,
   derived from trailing skill. This stops the ensemble's large member count from
   drowning out a more skillful deterministic model (GFS is markedly better on the
   low in recent history).
2. **Conditional settlement offset (two-bucket)** — split the CLI−hourly gap that
   the Kalshi page applies into clear+calm vs other nights, since the gap roughly
   doubles on radiational nights.

## Motivation / empirical findings (last 45–60 days, KDFW)

- **CLI−hourly gap is real and stable**: high +1.00°F, low −0.47°F (matches the
  calibrated offsets). The high offset is well validated; the flat low offset is
  near-neutral on point error.
- **Conditional low gap**: clear+calm nights −0.79°F vs other −0.43°F. Splitting
  trims the unexplained gap 0.57 → 0.53°F (modest but real).
- **Per-model low MAE**: GFS systems ~0.73°F vs ECMWF/ICON/GEM 1.32–1.68°F.
- **Walk-forward (OOS) weighting**: low consensus MAE 0.93 → 0.88 with inverse-MAE
  weighting; **high 0.88 → 0.98 (weighting hurts)** because equal-weight already
  beats every single model via error cancellation. ⇒ weighting must be applied
  **per variable** and only where it wins OOS.
- **Basis mismatch discovered**: live consensus is ensemble-dominated by count
  (~30–50 ensemble members + 5 deterministic + NWS), but the deterministic-only
  backtest can't validate that. **Resolved**: the Open-Meteo *ensemble archive is
  reachable* (past-dated requests return all members), so the historical
  ensemble-mean can be reconstructed and the rebalancing is fully OOS-validatable.
- **NWS** still has no free archive ⇒ cannot be skill-weighted; gets a neutral
  weight.

Realistic payoff: ~0.05°F (weighting) + ~0.04°F (conditional offset) ≈ ~0.1°F on
the Kalshi low. Small but validated, and strictly non-regressing by construction.

## Decisions

- **Ensemble granularity**: one combined `ensemble_mean` estimator (not per-EPS).
- **Shrinkage**: strong/conservative — weights stay close to equal; only large,
  stable skill gaps move them.
- Everything gated behind an OOS check that **falls back to the current behavior**
  (equal weights / flat offset) when the change does not win.

## Estimators

The pooled forecast samples collapse to **7 systems**:

| System | Historical skill available? | Weight source |
|---|---|---|
| `ensemble_mean` (mean of all ensemble members) | yes (ensemble archive) | inverse-MAE, shrunk |
| `det_gfs_seamless` | yes (det archive) | inverse-MAE, shrunk |
| `det_ecmwf_ifs025` | yes | inverse-MAE, shrunk |
| `det_icon_seamless` | yes | inverse-MAE, shrunk |
| `det_gem_seamless` | yes | inverse-MAE, shrunk |
| `det_gfs_hrrr` | yes | inverse-MAE, shrunk |
| `nws` | **no** | neutral = mean of the other systems' weights |

Weights are computed **per variable** (`high`, `low`) and normalized to sum to 1
across the 7 systems.

## Component design

### 1. Data layer — `sources/open_meteo_ensemble.py`

Add `fetch_historical(start, end, ttl=24*3600)` mirroring
`open_meteo_models.fetch_historical`: hit the ensemble endpoint with
`start_date`/`end_date`, parse members into `{label: (times, temps)}`, cached.
Wrapped so a failure degrades gracefully (callers treat ensemble history as
absent → ensemble simply isn't skill-weighted).

### 2. Calibration — `calibration.py`

- **`_system_extremes(start, end)`**: build `{day: {system: value}}` daily
  extremes per variable, where `ensemble_mean` is the mean of all ensemble member
  extremes for that day, plus each deterministic model and (live only) NWS. For
  history, NWS is absent.
- **`_system_weights(...)`**: walk-forward over the window. For each variable:
  - trailing per-system MAE → raw inverse-MAE weights;
  - apply **strong shrinkage toward equal**: `w_i ∝ (1−λ)·equal + λ·invMAE_norm_i`
    where `equal = 1/7`, `invMAE_norm` is the inverse-MAE weights normalized to sum
    1, and λ is the shrinkage strength. Start **λ = 0.25** (strong/conservative —
    weights stay near equal); λ is a module constant, and the OOS gate is the real
    safety net regardless of its exact value;
  - NWS weight = mean of the other systems' weights (neutral);
  - renormalize to sum 1.
- **OOS gate**: compare equal-weight vs shrunk-weighted consensus MAE on held-out
  days (walk-forward, trailing ~30-day train). Emit shrunk weights for a variable
  only if weighted MAE is lower by a margin **≥ 0.02°F** (guards against noise);
  else emit equal weights. Output under `weights: {high:{system:w}, low:{system:w}}`.
- **`_settlement_offset` → conditional**: split CLI−hourly gap by overnight
  conditions into `clear_calm` / `other` buckets (per variable), each with mean +
  std. Gate: emit the two-bucket split only if it beats the flat offset OOS *and*
  the `clear_calm` bucket has ≥5 nights; else emit the flat offset under the same
  schema (both buckets equal). Output:
  ```json
  "settlement_offset": {
    "high": {"clear_calm": x, "other": y, "clear_calm_std": .., "other_std": ..},
    "low":  {"clear_calm": -0.79, "other": -0.43, "clear_calm_std": .., "other_std": ..},
    "n_days": N
  }
  ```
  Backward-compatible read: model accepts either the old flat shape or the new
  bucketed shape.

### 3. Model — `model.py`

- **`_collect_samples` returns `(value, weight)` pairs**. Weight = the system
  weight from calibration; ensemble members each get `w_ensemble / M_ensemble`
  (members still present → preserve distribution shape/spread, but system total
  mass = its skill weight). Missing weights default to equal (no calibration yet).
- **Weighted consensus**: `mean = Σ w·v / Σ w`.
- **`_bin_probabilities` weighted**: kernels weighted by sample weight instead of
  uniform `1/n`; variance-matching (`alpha`) and bandwidth logic unchanged, using
  the weighted mean/variance. When all weights equal, results are identical to
  today (covered by a test).
- **Conditional offset selection in `predict_variable`**: pick the
  `clear_calm`/`other` bucket using the **existing** `night_conditions(day)` call
  already made for cooling (no extra fetch). Apply the bucket's mean as the shift
  and its std in quadrature (as today). Anchoring/nowcast unaffected.
- Sigma, hard bound, cooling: unchanged.

### 4. Backtest — `backtest.py`

- Move from deterministic-only to the **same 7-system basis** (also fetch
  historical ensemble). Apply the calibrated weights and the conditional offset so
  the accuracy panel reflects what ships.
- Keep the no-calibration baseline for comparison.
- Extend the existing `cli`/`settle_offset` path to the bucketed offset.

## Interfaces / data flow

```
calibration.compute()
  ├─ _system_extremes (det archive + ensemble archive)
  ├─ _system_weights  → weights{high,low}  (shrunk, OOS-gated)
  └─ _settlement_offset → bucketed offset  (OOS-gated)  → calibration.json

model.snapshot(calib, settle_offset=calib["settlement_offset"])
  └─ predict_variable
       ├─ _collect_samples → (value, weight) using calib["weights"][var]
       ├─ weighted consensus + _bin_probabilities(weighted)
       └─ conditional offset bucket via night_conditions(day)
```

## Testing (TDD)

- Weighted mean & weighted bins reduce to current results when weights uniform.
- Strong-shrinkage weight computation: monotonic in skill, bounded near equal.
- OOS gate: when weighting doesn't beat equal OOS, calibration emits equal weights
  (model unchanged); when it does, weighted.
- Conditional-offset bucket selection picks the right bucket for given
  cloud/wind; falls back to flat when split not emitted.
- Graceful degrade: ensemble-history fetch failure ⇒ deterministic-only weighting,
  no crash.
- Backtest run stays green and reports per-variable metrics.

## Risks & degradation

- New ensemble-history network dependency → try/except, degrade to
  deterministic-only weighting; gate then simply won't emit ensemble weight.
- Larger calibration compute (walk-forward + ensemble history) → retained daily
  cache; walk-forward only across the window.
- Overfit from too many weights → mitigated by single combined ensemble estimator,
  strong shrinkage, and the OOS gate.

## Out of scope

- New predictors / data sources beyond historical coverage of existing ones.
- Per-ensemble-system weighting (deferred; single combined estimator chosen).
- Changes to sigma/lead-bucket logic, the cooling model, or market/UI code beyond
  consuming the new calibration fields.
- NWS skill weighting (no archive).
