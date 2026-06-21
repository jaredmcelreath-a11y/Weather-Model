# Kalshi CLI Settlement Basis — Part B (Self-Scoring Basis) — Design

**Date:** 2026-06-21
**Status:** Approved (design)
**Sibling:** Part A (trading basis) — shipped on main. See
`2026-06-21-kalshi-cli-basis-A-design.md` and [[kalshi-cli-settlement-basis]].

## Goal

Put the **Kalshi** dashboard accuracy panel — both the immediate **backtest** and
the live **self-scoring** — on the NWS CLI settlement basis, so the diagnostics
grade the Kalshi (offset-shifted) predictions against the CLI truth Kalshi resolves
on. Robinhood's accuracy panel stays on the hourly basis, unchanged.

## Background

Part A already added: `station_history.fetch_actual_cli` (CLI truth), the
`settlement_offset` in `calibration.json`, and `model.predict_variable(...,
settle_offset=...)` shifting the Kalshi forecast to the CLI basis. The Kalshi
*trading* numbers are correct. But the accuracy panel still grades on hourly:
- `backtest.run()` replays vs `station_history.fetch_actual` (hourly), no offset.
- `scoring.score()` grades the forward log vs hourly actuals.
- The forward log (`forecast_log.jsonl`, on the `data` branch, written by the
  scheduled Action `scheduled_log.py`) stores only hourly-basis probabilities.
  On the cloud the dashboard does NOT write the log (`forecast_log.record` is a
  no-op when `FORECAST_LOG_GH_*` is set); the Action is the sole writer.

## Decision (user-approved)

Full scope: backtest **and** live self-scoring on the CLI basis, via a per-basis
forward log. The scheduled Action logs both bases. Per-lead-sigma calibration
feedback stays on the hourly basis (spread is basis-independent).

## Components

### 1. `forecast_log.py` — basis dimension
- `record(snapshot, path=None, basis="hourly")`: each emitted record gains a
  `"basis": basis` field.
- `_key(rec)` becomes `(rec["target_date"], rec["variable"], rec["lead_bucket"],
  rec.get("basis", "hourly"))` — so legacy untagged records read as hourly and
  hourly/CLI records for the same day/var/lead are distinct keys (coexist).
- No migration: `load()` is unchanged; consumers default missing `basis` to
  `"hourly"` via `_key`/filters.

### 2. `scoring.py` — basis-aware scoring
- `score(today=None, basis="hourly")`: filter `_settled_records` to
  `r.get("basis", "hourly") == basis`; fetch actuals from
  `station_history.fetch_actual_cli` when `basis == "cli"`, else `fetch_actual`.
  Return structure unchanged.
- `per_lead_sigma(...)` calls `score(basis="hourly")` explicitly (calibration's
  empirical spread stays on the hourly basis).

### 3. `backtest.py` — CLI-basis replay
- `run(days=60, cli=False, settle_offset=None)`: when `cli`, truth =
  `station_history.fetch_actual_cli`; the bias-corrected samples are additionally
  shifted by `off = (settle_offset or {}).get(var, 0.0)` (mirroring the live
  model), and `mu`/probs/coverage/reliability follow. The baseline arm is left
  as-is (raw samples, wide sigma) — on CLI truth it shows the larger gap the
  offset closes. Defaults (`cli=False, settle_offset=None`) reproduce today's
  hourly backtest byte-for-byte.

### 4. `scheduled_log.py` — log both bases
After computing `calib` and the hourly `snap`:
```python
forecast_log.record(snap)                              # basis="hourly"
off = (calib or {}).get("settlement_offset")
forecast_log.record(model.snapshot(calib, settle_offset=off), basis="cli")
```
`log.yml` is unchanged (it restores/commits the same file).

### 5. `app.py` — per-page accuracy + logging
- Add `load_accuracy_kalshi()` (`@st.cache_data(ttl=6*3600)`):
  `calib = calibration.get(refresh=True) or {}`, `off = calib.get(
  "settlement_offset")`, returns `(backtest.run(cli=True, settle_offset=off),
  scoring.score(basis="cli"))`, each arm wrapped in try/except → None.
- `_page(adapter, snapshot_loader, accuracy_loader, record_basis)`:
  `snap, calib = snapshot_loader()`; `forecast_log.record(snap,
  basis=record_basis)` (try/except); `market_view.render_page(snap, calib,
  adapter, accuracy_loader)`.
- `robinhood_page()` → `_page(ROBINHOOD, load_snapshot, load_accuracy, "hourly")`.
- `kalshi_page()` → `_page(KALSHI, load_snapshot_kalshi, load_accuracy_kalshi,
  "cli")`.
  (Replaces Part A's `record_log` flag; recording is now always safe because the
  basis tag keeps hourly and CLI records in separate key namespaces.)

### 6. `market_view.py` — render the right loader
`render_page`/`_render_accuracy` already receive the accuracy loader as a
parameter; just confirm the per-page loader is threaded through. Add a one-line
caption at the top of the Kalshi accuracy expander noting it is on the CLI
settlement basis (gated on a small flag/arg so Robinhood's panel is unchanged).

## Data flow

`kalshi_page` → `load_accuracy_kalshi` → `backtest.run(cli=True, settle_offset)` +
`scoring.score(basis="cli")` → CLI-basis metrics + reliability in the panel.
Logging: the Kalshi snapshot's shifted probs are recorded under `basis="cli"`;
once those days settle, `scoring.score(basis="cli")` grades them vs CLI truth
(`fetch_actual_cli`). On cloud the scheduled Action writes both bases to the
`data` branch; the dashboard reads from there.

## Backward compatibility & error handling

- Existing `data`-branch records have no `basis` → read as hourly → Robinhood
  scoring identical to today. CLI live records accumulate going forward (CLI live
  self-scoring fills in after ~10 settled days, like the original hourly log).
- All fetch/score arms stay wrapped in try/except; a `daily.py` outage or empty
  CLI log degrades the Kalshi panel gracefully (None → existing "unavailable"
  captions).
- `settle_offset` / `cli` default off everywhere → hourly path unchanged.

## Testing

- `forecast_log`: hourly + CLI records for the same (day, var, lead) coexist as
  distinct keys; a legacy untagged record is treated as hourly on re-`record`
  upsert (tmp-file test, explicit `path`).
- `scoring.score(basis=...)`: monkeypatch `station_history.fetch_actual` and
  `fetch_actual_cli` + a synthetic logged set; assert basis filtering and that the
  matching truth source is used; assert `per_lead_sigma` uses hourly.
- `backtest.run(cli=True, settle_offset=...)`: monkeypatch
  `open_meteo_models.fetch_historical`, `station_history.fetch_actual_cli`, and
  `calibration.get` with synthetic data; assert CLI truth is used and the offset
  shifts the consensus/metrics vs `cli=False`.
- Full suite stays green; hourly/Robinhood defaults produce identical results.

## Robinhood unchanged

`score()`, `backtest.run()`, and `record()` with no new args behave exactly as
today (hourly default). Robinhood records gain only an explicit
`"basis":"hourly"` field (content/keys/scoring identical). The Robinhood accuracy
panel uses the unchanged `load_accuracy`.

## Out of scope (YAGNI)

- No change to the trading numbers (Part A) or the model offset itself.
- No CLI-basis recalibration of bias/σ (still hourly; offset is the only basis
  adjustment).
- No `log.yml` workflow changes.
