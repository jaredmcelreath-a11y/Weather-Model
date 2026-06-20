# Kalshi Market Page — Design

**Date:** 2026-06-20
**Status:** Approved (design)

## Goal

Add a second dashboard page tailored to the **Kalshi** Dallas temperature market,
toggleable with the existing **Robinhood (ForecastEx)** page. The Kalshi page mirrors
everything the current page does, but speaks Kalshi's contract structure (2°F range
buckets) instead of Robinhood's 1° "Greater/Lower than T°" cumulative ladder.

## Context

- `sources/kalshi.py` already fetches live Dallas markets (verified 2026-06-20:
  6 contracts each for high/low, shapes `between` / `less` / `greater`, with
  yes/no bid/ask). No auth.
- `model.prob_for_strike(probs, strike_type, floor, cap)` already prices Kalshi's
  bucket structure; `model.prob_for_contract(probs, kind, strike)` prices Robinhood's.
- The two source modules already emit a compatible normalized contract dict:
  `label`, `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, `last` (Kalshi adds
  `strike_type/floor/cap/ticker/volume`; Robinhood adds `kind/strike`).
- Settlement differs: Robinhood → Weather Underground whole-degree KDFW value;
  Kalshi → NWS Climatological Report (CLIDFW). The model's whole-degree bins apply
  to both; CLIDFW and WU can occasionally differ by a degree.

## Decisions (user-approved)

- **Toggle UX:** native Streamlit multipage navigation (`st.navigation` + `st.Page`).
- **Code structure:** factor the shared ~90% render logic behind a small market
  adapter; both pages share one source of truth.
- **Kalshi safe-hold floor:** default slider to 55% (min 50%), because probability
  splits across 2° buckets and the top bucket usually peaks ~60–70%. Robinhood keeps
  80%/60%.

## Architecture

Three files; native multipage.

### `markets.py`
- `MarketAdapter` dataclass bundling everything that differs between exchanges:
  - `name`, `exchange` — display ("Robinhood" / "ForecastEx", "Kalshi" / "Kalshi").
  - `fetch(variable, day_iso)` — a `@st.cache_data(ttl=30, show_spinner=False)`
    wrapper around `robinhood.fetch_ladder` / `kalshi.fetch_contracts`
    (takes `day_iso` so the cache key is hashable; parses to `date` inside).
  - `model_prob(probs, contract)` — RH: `prob_for_contract(probs, c["kind"],
    c["strike"])`; Kalshi: `prob_for_strike(probs, c["strike_type"], c["floor"],
    c["cap"])`.
  - `vs_model_heading` / `contract_caption_verb` — RH uses "Greater than" /
    "Lower than"; Kalshi describes 2° buckets.
  - `settle_footer` — exchange-specific caveat.
  - `safe_hold_default`, `safe_hold_min` — RH 0.80/0.60; Kalshi 0.55/0.50.
- Two instances: `ROBINHOOD`, `KALSHI`.

### `market_view.py`
- The rendering moved out of `app.py`, parameterized by `adapter`:
  helpers `cents`, `pct`, `spread_c`, `exit_plan`, `flip_prob`, `_flag_hold_only`,
  `prob_table`, `lock_status` (all unchanged — market-agnostic).
- `render_variable(col, title, d, variable, day_iso, adapter, featured, safe_min)`
  — the market section now calls `adapter.fetch(...)` and `adapter.model_prob(...)`
  and uses the adapter's headings/captions. All trade math (3pp edge signal,
  flip-prob, exit plan, Top-3 flip, Top-3 hold, Safest-hold) unchanged.
- `render_page(snap, calib, adapter)` — draws the full page body: title (includes
  market name), top metrics row, Day radio + safe-hold slider (slider default/min
  from adapter), the two High/Low columns, per-source + "📊 Model accuracy"
  expanders, footer (`adapter.settle_footer`). Widget `key`s namespaced by
  `adapter.name` so the two pages don't collide.

### `app.py` (slimmed to orchestration)
- Page config, GitHub-secrets env setup.
- Cached `load_snapshot()` and `load_accuracy()` (unchanged).
- `forecast_log.record(snap)` (best-effort, unchanged).
- `robinhood_page()` / `kalshi_page()`: load snapshot, call
  `market_view.render_page(snap, calib, ROBINHOOD/KALSHI)`.
- `st.navigation([st.Page(robinhood_page, title="Robinhood"),
  st.Page(kalshi_page, title="Kalshi")]).run()`.

## Data flow

Both pages run the identical model pipeline and share the cached `snapshot` (same
`probs`). Only the contract list and the `probs→contract` mapping differ — both
encapsulated in the adapter. The normalized contract dict flows unchanged through the
shared edge/flip/hold logic.

## Error handling

- `adapter.fetch` returns `[]` on any source failure (existing behavior of both
  source modules); the page shows "No live market for this day yet." — unchanged.
- `forecast_log.record` stays wrapped in try/except so logging never breaks the UI.
- Model/source modules are untouched, so their error paths are unchanged.

## Testing

- `tests/` (pytest) gains adapter mapping tests:
  - Robinhood `model_prob` reproduces `prob_for_contract` on a known `probs` dict
    for a `>` and a `<` contract.
  - Kalshi `model_prob` reproduces `prob_for_strike` for `between`, `less`,
    `greater` buckets.
- Import/smoke test: `markets` and `market_view` import cleanly and `render_page`
  is callable (mapping/import tests need no Streamlit runtime).
- All existing tests stay green — `model` and `sources` are unchanged.

## What stays identical

3pp edge signals, flip-prob math, exit plans, Top-3 flip, Top-3 hold, Safest-hold,
bar/dist tables, lock-status badges, accuracy expander, auto-refresh, GitHub-log
persistence.

## Out of scope (YAGNI)

- No changes to the model, calibration, scoring, or settlement logic.
- No new data sources; Kalshi fetch already exists.
- No per-exchange model recalibration (CLIDFW-vs-WU divergence noted, not modeled).
