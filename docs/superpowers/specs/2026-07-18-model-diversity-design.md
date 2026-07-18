# Model Diversity via Shadow Consensus — Design

**Date:** 2026-07-18
**Status:** Approved (brainstorming), pending implementation plan
**Related:** [[mos-day-ahead-weighting]], [[model-weighting-conditional-offset]], `docs/benchmarks/2026-07-17-mos-weighting/ASSESSMENT.md`

## Goal

Broaden the model set feeding the consensus by adding new global and AI weather
models as **candidates**, without touching the production consensus number that
works well today. The candidate consensus runs as a *shadow* next to the live
one so we can compare them in real conditions before promoting anything.

## Motivation

The current mix pulls four ensemble systems (GEFS, ICON-EPS, ECMWF-EPS, GEPS)
and five deterministic anchors (GFS, ECMWF-IFS, ICON, GEM, HRRR). It is missing
several free Open-Meteo models — the AI models (ECMWF-AIFS, GraphCast), which
lead on 2 m temperature, and mature physics globals (UKMO, JMA, Météo-France
ARPEGE). Because the consensus already uses **guarded, group-rebalanced
skill-weighting** (`_system_weights` shrunk to equal at `lam=0.25`, gated by
`_weights_beat_equal` walk-forward), adding a diverse-but-biased model is
structurally low-risk: a weak model is demoted out-of-sample and cannot
meaningfully harm the consensus. Adding models is also nearly free at runtime —
all deterministic models arrive in a single bundled Open-Meteo call, same for
ensembles.

## Non-goals

- Not changing the production consensus number until the user manually promotes.
- Not adding new bias groups. New deterministic models share the existing
  `deterministic` group bias; new ensemble members share `ensemble`.
- Not skill-weighting MOS (separate, already-tracked follow-up).
- No auto-promotion. Promotion is a deliberate, reversible human decision.

## Architecture

### Two model sets

`config.py` gains candidate lists alongside the untouched production lists:

- `DETERMINISTIC_MODELS` / `ENSEMBLE_MODELS` — **production**, unchanged. This is
  the live consensus.
- `CANDIDATE_DETERMINISTIC_MODELS` / `CANDIDATE_ENSEMBLE_MODELS` — the expanded
  supersets (production + verified new models).

### Isolation by construction

`gather_series` (and the `predict` path that calls it) take **optional model-list
overrides that default to the production lists**:

```
gather_series(..., det_models=None, ens_models=None)
    det_models = det_models or DETERMINISTIC_MODELS
    ens_models = ens_models or ENSEMBLE_MODELS
```

- Production callers pass nothing → identical code path and identical output to
  today. This is pinned by a production-invariance test.
- The comparison feature passes the candidate lists. It is a separate, cached
  fetch + predict, fully isolated: the candidate path cannot perturb the
  production number.

### Verified model IDs only (implementation step 1)

Before any config edit, a probe script queries the Open-Meteo forecast, ensemble,
and historical-forecast APIs for each candidate ID at KDFW and records:

1. Does it return a live temperature series?
2. Archive depth over the calibration window (for skill-weight + bias).

Only IDs that return live data ship into the candidate lists. Availability
expectations (to be confirmed by the probe, not assumed):

- **Deterministic (all five candidates):** ECMWF-AIFS, GraphCast, UKMO, JMA,
  Météo-France ARPEGE.
- **Ensemble (only those with real member expansion):** UKMO ensemble, possibly
  BOM ensemble. JMA and ARPEGE effectively have no Open-Meteo ensemble.

The probe output is saved under `docs/benchmarks/2026-07-18-model-diversity/`.

### Robustness guard

New/newer models occasionally return null or partial columns inside an otherwise
successful bundled response. Both `_parse` functions
(`open_meteo_models._parse`, `open_meteo_ensemble._parse`) filter `None`
temperatures so a flaky candidate model cannot inject `None` into the consensus
samples. The existing per-fetch drop in `gather_series` already covers a whole-
API failure; this covers per-*model* junk within a success.

### Grouping / weighting (no change needed)

- `_group_of` already maps `det_*` → `deterministic` and `ens_*` → `ensemble`
  by prefix, so new labels are grouped correctly with no code change.
- `_system_extremes` already fetches *all* deterministic archive rows and folds
  all ensemble members into `ensemble_mean`, so candidate models auto-join
  calibration where archive exists.
- A candidate model with short/absent archive still flows into the live candidate
  consensus at flat equal weight with no bias correction (graceful degradation,
  same as MOS today), and is skill-rewarded once archive accumulates.

### Display

A small **candidate-consensus comparison row on the Forecast page**: production
consensus, candidate consensus, and the gap between them (high and low). Same
bins, same pipeline — only the model set differs. Placement is adjustable.

## Validation

Head-to-head, champion vs challenger:

1. **Backtest.** Reuse `backtest.py` to score both model sets over the same
   trailing archive window: MAE, Brier, CRPS, interval coverage. **Do-no-harm
   bar:** the candidate must not degrade these versus production. Results saved
   as `ASSESSMENT.md` under `docs/benchmarks/2026-07-18-model-diversity/`.
   Caveat carried forward from the MOS work: the historical-forecast archive is
   near-analysis, so backtest numbers are a same-basis regression check, not a
   true day-ahead proxy.
2. **Forward-log both.** The candidate consensus is logged alongside production
   each run, accumulating a real day-ahead head-to-head score over the following
   days — the honest judge the archive can't be.

## Promotion

Manual and reversible. Once the shadow comparison and forward-log convince the
user, promotion is a one-line change: move the candidate models into the
production lists (or point the production lists at the candidate set). Rollback
is the reverse.

## Testing

- **Production-invariance test (load-bearing):** production `predict` with
  default lists equals the pre-change result on a fixed fixture — guarantees the
  live number did not move.
- **Parser tests:** new model columns parse; null/partial columns are filtered
  (the robustness guard).
- **Override test:** `gather_series` with candidate overrides returns the
  superset of series; `_group_of` maps the new labels to the right groups.
- **Backtest harness:** the champion-vs-challenger comparison runs and emits both
  metric sets.

## Risks

- **Archive depth for AI models.** New models may have shallow archives; handled
  by graceful flat-weight degradation until archive accrues. No blocker.
- **Backtest basis.** Near-analysis archive can flatter absolute skill; mitigated
  by treating backtest as do-no-harm regression only and relying on the forward
  log for the day-ahead verdict.
- **Extra API call for the shadow.** One additional bundled Open-Meteo call on
  pages showing the comparison, cached like the rest. Acceptable.
