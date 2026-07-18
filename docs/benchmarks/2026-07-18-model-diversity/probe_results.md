# Candidate model probe — 2026-07-18

Source: `scripts/probe_candidate_models.py` (run manually, hits Open-Meteo).
`nonnull` is the fraction of returned `temperature_2m` values that are not null;
`cols` is the number of temperature series returned (ensemble members for the
ensemble API). Archive window = 45 days ending 2 days ago.

## Raw output

```
=== DETERMINISTIC candidates (forecast API) ===
ecmwf_aifs025_single {'ok': True, 'cols': 1, 'nonnull': 1.0} {'archive_hours': 1104, 'nonnull': 1.0}
gfs_graphcast025     {'ok': True, 'cols': 1, 'nonnull': 0.0} {'archive_hours': 1104, 'nonnull': 0.0}
ukmo_seamless        {'ok': True, 'cols': 1, 'nonnull': 1.0} {'archive_hours': 1104, 'nonnull': 1.0}
jma_seamless         {'ok': True, 'cols': 1, 'nonnull': 1.0} {'archive_hours': 1104, 'nonnull': 1.0}
meteofrance_seamless {'ok': True, 'cols': 1, 'nonnull': 1.0} {'archive_hours': 1104, 'nonnull': 1.0}

=== ENSEMBLE candidates (ensemble API) ===
ukmo_global_ensemble_20km   {'ok': True, 'cols': 18, 'nonnull': 1.0}
bom_access_global_ensemble  {'ok': True, 'cols': 18, 'nonnull': 0.0}
```

## Verdicts

| Model | Type | Live | Archive (h) | Verdict |
|---|---|---|---|---|
| `ecmwf_aifs025_single` | deterministic (AI) | ✅ 1.0 | 1104 | **INCLUDE** |
| `gfs_graphcast025` | deterministic (AI) | ⚠️ all-null | 1104 (all-null) | **DROP** — no 2m temperature at KDFW |
| `ukmo_seamless` | deterministic | ✅ 1.0 | 1104 | **INCLUDE** |
| `jma_seamless` | deterministic | ✅ 1.0 | 1104 | **INCLUDE** |
| `meteofrance_seamless` | deterministic | ✅ 1.0 | 1104 | **INCLUDE** |
| `ukmo_global_ensemble_20km` | ensemble (18 members) | ✅ 1.0 | — | **INCLUDE** |
| `bom_access_global_ensemble` | ensemble (18 members) | ⚠️ all-null | — | **DROP** — no 2m temperature at KDFW |

## Confirmed candidate additions (Task 2)

**Deterministic:** `ecmwf_aifs025_single`, `ukmo_seamless`, `jma_seamless`,
`meteofrance_seamless` (4 new — GraphCast dropped, all-null temperature).

**Ensemble:** `ukmo_global_ensemble_20km` (1 new, ~18 members — BOM dropped,
all-null temperature).

All four deterministic additions have full 45-day archive depth, so they join
calibration's skill-weighting immediately (no flat-weight warm-up needed). The
UKMO ensemble has no backtest archive path (backtest uses deterministic archive
only) and is judged by the forward log.

Note: the all-null models motivate Task 3's per-series null-filter guard as a
belt-and-suspenders defense even though we exclude them from the config.
