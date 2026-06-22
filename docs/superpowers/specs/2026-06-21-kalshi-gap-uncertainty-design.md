# Kalshi Gap-Uncertainty σ Inflation — Design

**Date:** 2026-06-21
**Status:** Approved (design)
**Builds on:** Part A (settlement offset) + Part B (CLI self-scoring), both shipped.
See [[kalshi-cli-settlement-basis]].

## Goal

Stop the Kalshi page from showing **false confidence on locked days**. The CLI
settlement offset is a historical *average* (high gap mean +1.02, **std 0.71**),
but the model currently adds it as a fixed shift with no added uncertainty — so on
a locked day σ collapses to ~0.7 and the model prices e.g. 74% on 93-94 / ~0% on
92, while the high is actually locked at 92 (today's real gap was 0). Fix: carry
the gap's std in calibration and add it in quadrature to σ whenever the offset is
applied. The +1.02 center is unchanged (it's right on ~78% of locked days); only
the uncertainty becomes honest.

## Background (verified 2026-06-21)

- Over 45 settled/locked days the high CLI−hourly gap is mean **+1.02**, std
  **0.71**, distribution {0:22%, 1:56%, 2:20%, 3:2%}. So gap≥1 on 78% of days —
  the offset center is correct on average; a literal "taper toward 0 when locked"
  would under-predict the 78% majority and was rejected.
- The real flaw today: locked high = 92 (hourly AND sub-hourly both 91.9, gap 0),
  market prices "92 or below" at 96%, but the model adds +1.02 with σ collapsed to
  0.7 → ~0% on 92, a false confident edge.
- The 1-minute ASOS feed (which could resolve the gap intraday) lags ~18h, so it
  cannot be used live; this is a future calibration-truth upgrade, not part of this.

## Decision (user-approved)

Approach **B**: widen σ by the calibrated gap std (in quadrature), applied on
**both** the pure-forecast and nowcast paths whenever `settle_offset` is set
(independent variances add). Kalshi-only; Robinhood (no offset) untouched.

## Components

### 1. `calibration.py`
`_settlement_offset(cli, hourly)` additionally computes the population std of the
per-variable gap and returns it as flat keys `high_std` / `low_std` next to the
existing `high` / `low` / `n_days`. Flat keys preserve Part A's
`settle_offset.get("high")` numeric reads. Std uses the same overlapping days as
the mean; zero std when 0/1 overlapping days.

### 2. `model.py`
In `predict_variable`, after the final
`sigma = max(sigma_base * locked_ratio, _SIGMA_FLOOR)` line, when `settle_offset`
is truthy:
```python
gap_std = settle_offset.get(f"{variable}_std", 0.0)
if gap_std:
    sigma = math.hypot(sigma, gap_std)
```
(`math` already imported.) This is in addition to the Part A sample shift. The σ
inflation applies on both paths: on a pure forecast σ is already wide (~1.8) so
hypot(1.8, 0.71)≈1.93 (minor); on a locked day 0.7→hypot(0.7,0.71)≈1.0
(meaningful). Independent of `locked_ratio` because the gap is unobservable in all
states. `consensus` (sample mean) is unchanged — only the spread widens.

### 3. `backtest.py`
`run(days=60, cli=False, settle_offset=None)`: when `cli`, apply the same σ
inflation so the Kalshi backtest panel validates the same widened model the live
page shows. After `sigma = max(sigma_cfg.get(var) or 3.0, _MIN_SIGMA)` (and the
existing `off` line), add:
```python
if cli:
    sigma = math.hypot(sigma, (settle_offset or {}).get(f"{var}_std", 0.0))
```
`cli=False` path is unchanged.

### No changes needed elsewhere
`app.load_snapshot_kalshi` and `scheduled_log` already pass
`calib["settlement_offset"]` (now carrying the std keys) into `model.snapshot` /
`backtest.run`, so the widening flows through existing plumbing. `market_view`,
`app.py` page wiring, and `forecast_log` are untouched.

## Data flow

`calibration.compute()` → `settlement_offset = {high, low, high_std, low_std,
n_days}` → `load_snapshot_kalshi` / `scheduled_log` / `load_accuracy_kalshi` pass
it to `model.snapshot(..., settle_offset=)` and `backtest.run(cli=True,
settle_offset=)` → `predict_variable` / `run` shift the center by the mean and
widen σ by the std. Robinhood passes no offset → identical behavior.

## Effect (today's locked example)

Consensus 93.0 (unchanged); σ 0.7 → ~1.0. P(92 bucket) ≈ 28% (was ~0%), P(93-94)
≈ 62% (was 74%). The model still leans 93 but no longer fires a false confident
"BUY 93-94" against a market at 96% ≤92.

## Error handling / back-compat

- Stale `calibration.json` lacking `*_std` → `.get(..., 0.0)` → no inflation
  (degrades to Part A behavior; recompute restores it).
- Robinhood / any caller passing no `settle_offset` → σ unchanged.

## Testing

- `_settlement_offset`: returns correct mean AND population std per variable
  (update the two Part A assertions to the new keys; e.g. low gaps [0, −2] →
  mean −1.0, std 1.0; no-overlap → all zeros incl. stds).
- `predict_variable`: a non-zero `*_std` increases `sigma_used` while leaving
  `consensus` unchanged; `*_std` 0 / `settle_offset=None` reproduces Part A
  (Robinhood guard); a larger std yields a larger σ.
- `backtest.run(cli=True, settle_offset={...,*_std})`: σ-driven metrics differ
  from the no-std case; `cli=False` reproduces the existing backtest.
- Full suite stays green; hourly/Robinhood unchanged.

## Out of scope (future / YAGNI)

- 1-minute ASOS calibration-truth upgrade (lags ~18h; next-day-complete) — a
  separate follow-up to sharpen the offset/std estimate.
- No change to the offset center, the trading logic, or Robinhood.
