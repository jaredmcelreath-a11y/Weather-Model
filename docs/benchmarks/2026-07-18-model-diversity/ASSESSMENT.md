# Model-diversity do-no-harm assessment — 2026-07-18

Champion (production 5-model set) vs challenger (candidate set) over a 45-day
historical-forecast window at KDFW. Command:

```
PYTHONPATH=. .venv/bin/python -c "
import backtest, config
prod = backtest.run(days=45)
cand = backtest.run(days=45, det_models=config.CANDIDATE_DETERMINISTIC_MODELS)
..."
```

## Headline: do-no-harm FAILS in the backtest. Do NOT promote on this evidence.

| Set | HIGH mae | HIGH crps | HIGH exact | LOW mae | LOW crps | LOW exact |
|---|---|---|---|---|---|---|
| production (5) | **0.83** | **0.595** | 30% | **0.79** | **0.56** | **37%** |
| candidate all (9) | 1.30 | 0.95 | 26% | 1.20 | 0.938 | 35% |
| candidate no-JMA (8) | 1.22 | 0.87 | 28% | 1.06 | 0.833 | 33% |
| production + AIFS only (6) | 1.08 | 0.813 | **35%** | 0.90 | 0.663 | 26% |

Every candidate variant degrades consensus MAE and CRPS versus production. The
lone bright spot: AIFS-only *improves* HIGH exact-peak (30% → 35%).

## Why — per-model MAE (45-day archive)

```
det_gfs_hrrr             high=0.76  low=0.78     (production)
det_gfs_seamless         high=0.76  low=0.78     (production)
det_icon_seamless        high=1.11  low=1.35     (production)
det_gem_seamless         high=1.67  low=1.33     (production)
det_ecmwf_ifs025         high=2.48  low=0.93     (production)
det_ecmwf_aifs025_single high=2.26  low=1.15     *NEW
det_ukmo_seamless        high=1.87  low=1.72     *NEW
det_meteofrance_seamless high=1.98  low=1.57     *NEW
det_jma_seamless         high=4.11  low=1.87     *NEW  <-- outlier, poisons the blend
```

- **JMA is unusable at KDFW** (high MAE 4.11°F). It should not be promoted under
  any circumstance.
- **AIFS, UKMO, ARPEGE** land in the range of the *weaker* production members
  (ECMWF-IFS 2.48, GEM 1.67) — not obviously worse than models already in the
  mix, but not additive at equal weight either.

## Two caveats that limit this backtest as a verdict

1. **Equal weight, no skill-weighting.** `backtest.run` blends every model at
   weight 1.0 with a single shared deterministic-group bias. It does NOT apply
   the live guarded skill-weighting (`calibration._system_weights` +
   `_weights_beat_equal`), which would demote JMA hard and down-weight the
   others. So this table is the *naive* addition case — a floor, not the live
   behavior.
2. **Near-analysis archive.** The historical-forecast archive flatters the
   GFS-family incumbents (gfs_hrrr/gfs_seamless at 0.76°F is impossibly good for
   a genuine day-ahead forecast). New models that aren't as close to the archive
   analysis look relatively worse here than they would at true day-ahead lead.

## Verdict

- **FLAG — do not promote any candidate model to production on this evidence.**
- The shadow infrastructure is **safe to keep** — it is fully isolated and never
  touches the production consensus. Leaving it running costs one extra cached API
  call and lets the **forward log** (Task 7) accumulate a head-to-head at true
  day-ahead lead, where the live skill-weighting applies and the near-analysis
  flattering does not. That is the fair judge this backtest cannot be.
- **JMA recommendation:** drop `jma_seamless` from `CANDIDATE_DETERMINISTIC_MODELS`
  even for the shadow — a 4.11°F model tells the forward-log comparison nothing
  useful. (Pending user call; the shadow is harmless either way.)

This is the measure-first design working as intended: the caution surfaced
before anything touched the live number.
