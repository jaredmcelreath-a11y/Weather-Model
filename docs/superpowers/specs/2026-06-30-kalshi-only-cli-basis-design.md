# Kalshi-only: retire the Robinhood/hourly path from the live site

**Date:** 2026-06-30
**Status:** Approved

## Problem

The dashboard ships two pages: Kalshi (CLI / continuous daily-max basis) and
Robinhood (hourly-observation basis). Kalshi is the page that matters. Keeping
the hourly path live has two costs:

1. The scheduled GitHub Action logs an hourly snapshot every run, growing a
   forward-log cohort the site no longer needs.
2. **The real bug:** `calibration.py` derives the empirical per-lead **sigma**
   and the lead-time **bias correction** from `scoring` with its default
   `basis="hourly"`. Those values are written into `calibration.json` and then
   applied to the **CLI-basis Kalshi forecast**. The spread and the
   soon-to-activate bias auto-correction shaping the Kalshi page are therefore
   computed on the wrong basis — and would poison the bias correction the moment
   it crosses the 10-day activation gate.

Production scoring already filters by basis (`scoring.score`), so the live
Kalshi forecast is not itself mixing bases; the leak is specifically calibration
reading hourly-basis self-scoring stats.

## Goal

Make the live site Kalshi/CLI-only, and route calibration's empirical stats to
the CLI basis — without deleting the Robinhood/hourly code (one-line revert to
bring it back).

## Changes

### 1. `app.py` — Robinhood off the nav
Remove the `st.Page(robinhood_page, …)` entry from `st.navigation`, leaving the
single Kalshi page. Keep `robinhood_page()`, `load_snapshot`, `load_accuracy`,
and the `ROBINHOOD` import in place, unreferenced.

### 2. `scheduled_log.py` — stop logging hourly
Drop the hourly `forecast_log.record(hourly_snap)` and
`consensus_log.record(hourly_snap)` calls. Log the CLI snapshot only. The
`model.snapshot(calib)` hourly call and all hourly machinery in `model.py` stay.

### 3. `calibration.py` — empirical stats on the CLI basis
- Add a `basis` parameter to `scoring.per_lead_sigma` (it currently lacks one;
  `per_lead_bias` already has it, defaulting to `"hourly"`).
- In `calibration`, call `per_lead_sigma(basis="cli")` and
  `per_lead_bias(basis="cli")` so per-lead spread and bias auto-correction come
  from CLI self-scoring.

## Explicitly out of scope / left alone

- Legacy `hourly` and `None`-basis rows already in `forecast_log.jsonl`: CLI
  scoring ignores them; no deletion.
- Robinhood/hourly code paths: retained in the repo, just unwired from the site.
- The CLI cohort is younger than the hourly one, so per-lead sigma/bias may stay
  dormant until it matures past `MIN_LEAD_DAYS`. That is correct, not a
  regression.

## Verification

- Tests pass (`pytest`), including `tests/test_cli_basis.py`, `test_accuracy.py`,
  `test_weighting.py`.
- `app.py` navigation renders only the Kalshi page.
- `scheduled_log.main()` writes only CLI rows (no new `basis="hourly"` rows).
- `calibration.get(refresh=True)` runs; `per_lead_sigma`/`bias_correction` are
  sourced from CLI scoring (verify via a basis-tagged unit check).
