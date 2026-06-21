# Kalshi CLI Settlement Basis — Part A (Trading Basis) — Design

**Date:** 2026-06-21
**Status:** Approved (design)
**Sibling:** Part B (self-scoring basis) — separate spec/plan, built after A.

## Goal

Put the **Kalshi** dashboard page's probabilities/consensus on Kalshi's actual
settlement basis — the **NWS CLI daily max/min** (continuous 1-minute ASOS) —
instead of the hourly basis the model currently uses for both pages. Robinhood
(hourly / WU settlement) stays **byte-for-byte identical**.

## Background (verified 2026-06-21)

See [[kalshi-cli-settlement-basis]]. Over 12 settled days the CLI daily max ran
**hotter than our hourly basis on 9/12 days (mean +0.83°F)**, crossing a Kalshi 2°
bucket on 4/12 days; CLI lows ran **colder on 3/12 days (mean −0.25°F)**. The free
live METAR/SPECI feed does NOT capture the 1-minute CLI peak (raw max ≈ hourly max
in the IEM archive), so a calibrated **offset** is the reliable mechanism — not
sub-hourly live obs.

The hourly assumption lives in `sources/common.to_hourly`, applied in
`nws_observations.fetch` (live obs) and `station_history.fetch_actual` (truth).
`model.predict_variable` builds bias-corrected forecast samples, blends the
realized extreme as a hard floor/ceiling for *today*, and bins them.

## Decisions (user-approved)

- **Isolation:** "Same output, default-off code." Shared files (`model.py`,
  `calibration.py`) may gain Kalshi-only additions that are inert for Robinhood
  (an optional `settle_offset` param defaulting to `None`; an extra
  `calibration.json` key Robinhood never reads). One pipeline, no duplication.
- **CLI truth source:** IEM daily summary (`max_temp_f`/`min_temp_f`) as the CLI
  proxy. Proven, free. (Exact NWS CLIDFW text product is a future refinement.)
- **Offset semantics:** shift the forecast distribution, NOT the hard observed
  bound (see below).

## Architecture

A single per-variable additive **settlement offset**
`{"high": +Δh, "low": +Δl}` (Δl negative), calibrated as the mean
`CLI_actual − hourly_actual` over the calibration window. The Kalshi snapshot
applies it; Robinhood passes nothing.

### 1. `sources/station_history.py`
Add `fetch_actual_cli(start, end) -> dict[date, tuple[float, float]]` — the IEM
daily-summary endpoint (`https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py`,
params `network=TX_ASOS, stations=DFW, year1/month1/day1..year2/month2/day2,
format=comma`), parsing columns `max_temp_f`/`min_temp_f` per `day`. Rows with
missing values skipped. The existing hourly `fetch_actual` is untouched.

### 2. `calibration.py`
- Factor a pure helper
  `_settlement_offset(cli: dict, hourly: dict) -> dict` returning
  `{"high": round(mean(cli_hi-hr_hi),2), "low": round(mean(cli_lo-hr_lo),2),
  "n_days": N}` over days present in both; `{"high":0.0,"low":0.0,"n_days":0}`
  when there is no overlap.
- In `compute()`, after `actual = station_history.fetch_actual(...)`, also
  `cli = station_history.fetch_actual_cli(start, end)` (wrapped in try/except →
  `{}` on failure) and add `"settlement_offset": _settlement_offset(cli, actual)`
  to the returned dict. On fetch failure the helper yields a zero offset.

### 3. `model.py`
- `predict_variable(series, obs_series, day, variable, now, calib,
  settle_offset=None)`. When `settle_offset` is truthy, compute
  `off = settle_offset.get(variable, 0.0)` and add it to the forecast sample
  lists only:
  - `fullday = [s + off for s in fullday]`
  - `samples = [s + off for s in samples]`
  applied after `_collect_samples` (and after the existing cooling adjustment;
  order is irrelevant for an additive constant).
- Do **NOT** add `off` to `observed`, `_apply_hard_bound`, or the returned
  `observed_so_far`. The offset is an average gap, not a guaranteed floor;
  shifting the hard bound would wrongly zero still-possible bins.
- A constant shift leaves `_std(samples)`, `_std(fullday)`, `locked_ratio`, and
  `sigma` unchanged — only `consensus` (the sample mean) and the binned
  distribution location move.
- Thread the param (default `None`) through `_predict_from`, `predict`, and
  `snapshot` so callers can opt in.

### 4. `app.py`
- Keep `load_snapshot()` (hourly) exactly as is — Robinhood's loader.
- Add `load_snapshot_kalshi()` (`@st.cache_data(ttl=120)`) →
  `model.snapshot(calib, settle_offset=calib.get("settlement_offset"))`.
- `_page(adapter, snapshot_loader)` takes the loader; `robinhood_page` passes
  `load_snapshot`, `kalshi_page` passes `load_snapshot_kalshi`.

### 5. `markets.py` / `market_view.py`
- Add `MarketAdapter.basis_note: str | None`. Kalshi:
  "Values on the NWS CLI settlement basis (Kalshi). Robinhood: `None`.
- `market_view.render_variable` renders `adapter.basis_note` as a caption under
  the market heading only when set → Robinhood unchanged.

## Data flow

`kalshi_page` → `load_snapshot_kalshi()` →
`model.snapshot(calib, settle_offset=calib["settlement_offset"])` →
`predict_variable(..., settle_offset=...)` shifts the high/low distributions to
CLI basis → `market_view.render_page` renders them through the existing
Kalshi adapter (`prob_for_strike`, 2° buckets). Robinhood path is unchanged.

Worked example (today, hourly high locked ~91): samples ≈ 91 → +0.9 → ~91.9,
tight σ → mass in the 92–93 bucket, matching the market's ~86%. The hard bound
still only forbids < 91, so the 91 bin stays possible.

## Error handling

- CLI fetch failure in `compute()` → zero offset → Kalshi == hourly (today's
  behavior). Missing `settlement_offset` in a stale `calibration.json` →
  `.get(...)` returns `None` → `predict_variable` no-ops the shift.
- No new failure modes on the Robinhood path.

## Testing

- `_settlement_offset()` — pure unit test on synthetic CLI/hourly dicts incl. the
  no-overlap → zero case.
- `fetch_actual_cli()` — parse test over a sample IEM `daily.py` CSV string
  (skips missing rows; maps day → (max,min)).
- `predict_variable` offset behavior — on a small synthetic series:
  `settle_offset={"high":1.0}` shifts `consensus` by +1.0 and moves the
  distribution up; `settle_offset=None` equals `settle_offset={"high":0,"low":0}`
  and equals the pre-change output (**Robinhood-unchanged guard**); `sigma` and
  `locked_ratio` unchanged by the shift.
- All existing tests stay green.

## What stays identical (Robinhood)

`load_snapshot()`, hourly `fetch_actual`, `to_hourly`, and every Robinhood call
(no `settle_offset`) → provably identical numbers. All shared additions are
optional and default-off.

## Out of scope (Part B / YAGNI)

- Self-scoring / backtest / forward-log on the CLI basis (Part B).
- Per-source panel and "current temp" stay hourly (raw model inputs / live obs).
- Exact NWS CLIDFW text-product parser.
- Recalibrating σ on the CLI basis (the gap is a near-constant shift; σ is
  basis-independent to first order).
