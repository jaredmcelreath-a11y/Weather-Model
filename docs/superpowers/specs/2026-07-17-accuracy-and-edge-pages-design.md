# Accuracy Scorecard & Edge Tracker pages — design

**Date:** 2026-07-17
**Status:** Approved (design), pending implementation plan

## Goal

Add two standalone dashboard pages that surface analytics the model already
computes but does not present as first-class pages:

1. **Accuracy Scorecard** — "how good is the forecast itself?" (forecast skill,
   the missing complement to the betting-P&L History page).
2. **Edge Tracker** — "is the model ahead of the market, and did my betting
   capture it?" (forecast edge + realized-edge P&L attribution).

Most of the computation already exists (`scoring.py`, `edge_report.py`,
`calibration_history.py`, `bet_history.py`); this work is primarily about
surfacing it as real pages, plus a small amount of new aggregation.

## Navigation

Current `st.navigation` in `app.py`: `Forecast` (default, `kalshi_page`) and
`History` (`bet_view.render`).

New order: **Forecast · Accuracy · Edge · History** (Forecast still default).

New page modules, each exposing a thin `render()` mirroring `bet_view.py`:

- `accuracy_view.py` → `render()`
- `edge_view.py` → `render()`

`app.py` wires two new `st.Page` entries and passes the cached accuracy loader
(`load_accuracy_kalshi`) into the accuracy page.

## Approach (and rejected alternatives)

Each page is its own module with a `render()` function, wired into
`st.navigation` — matches the existing `bet_view.py` pattern and keeps each
module focused.

- **Rejected:** tabs *within* the Forecast page — clutters the bet-focused page
  we are deliberately cleaning up.
- **Rejected:** one combined "Analytics" page with internal tabs — Accuracy and
  Edge answer different questions and each deserves a distinct nav entry.

## Page 1 — Accuracy Scorecard (`accuracy_view.render`)

Promotes `market_view._render_accuracy` out of the Forecast page into its own
page and expands it. **The Forecast page's accuracy panel is removed entirely**
(the call to `_render_accuracy` is dropped from `market_view.render_page`);
the Scorecard becomes the single home for all accuracy content.

Data sources (all existing): `scoring.score(basis="cli")`,
`scoring.market_accuracy()`, `scoring.correction_exclusions()`,
`calibration_history`, and the reliability bins already inside `score()`'s
`by_variable[...]["reliability"]`.

Sections:

1. **Headline tiles** — settled-day count; High & Low exact-bin %, within-1 %,
   and Brier (rolling, from `by_variable`).
2. **Decision-time same-day cohort** (`same_day_0900`) — the honest same-day
   number, separated from the ~11:45pm rolling lead-0 capture.
3. **Per-lead-time table** — bucket → High/Low exact %, within-1, bias, sigma,
   n (from `by_lead`). Makes the same-day → day-ahead skill drop visible.
4. **Reliability curves (NEW)** — predicted-probability bucket vs. observed
   frequency, one Altair chart per variable, built from the existing
   `reliability` bins. The "when I print 70%, does it happen 70%?" view.
5. **Model vs. market MAE** (`market_accuracy`) — model_mae vs market_mae and
   market-closer-%.
6. **Active corrections + storm/front exclusions** (`correction_exclusions`) —
   carried over from the old panel.
7. **Calibration-drift timeline** — carried over from the old panel
   (`calibration_history`).

## Page 2 — Edge Tracker (`edge_view.render`)

### Part A — Forecast edge vs. market

Live Streamlit render of `edge_report.metrics()` over `betting_log.load()`
joined with `settlements`. `edge_report` currently only writes CSV/markdown to
`docs/benchmarks/`; the page calls the same pure functions (`join`, `metrics`)
against the live logs instead of writing files.

- **Headline:** total disagreements, model-won N (%), market-won N (%);
  Model MAE vs Market MAE.
- **Boundary-day slice** broken out (`subset == "boundary"`) — the days the
  decision actually turns on — alongside `all` and `mid_bin`.
- **Flat-vs-live +0.9 offset verdict** (high only): `flat_rmse` vs `live_rmse`,
  flips toward/away — surfaces the one real edge lever.
- Grouped by capture slot × variable (morning = low, afternoon = high).

### Part B — Realized edge (P&L attribution)

From `bet_history` rows. Split settled bets by `entry` price:

- `entry >= 0.50` → **with-market** (bought the market favorite)
- `entry < 0.50` → **against-market** (bought the underdog)

For each bucket: wins, losses, net P&L. **Headline:** against-market net P&L
(your true edge) vs with-market net (riding favorites).

## Testable seams

Keep `render()` thin; put new aggregation in pure functions unit-tested without
Streamlit:

- `edge_view.assemble(betting_rows, cli_map, hourly_map) -> dict` — wraps
  `edge_report.join` + `edge_report.metrics`, returning the metrics dict the
  page renders (plus rolled-up headline totals).
- `edge_view.pnl_attribution(bet_rows) -> dict` — the with-/against-market
  split described in Part B.

Existing scoring/reliability logic is already covered by `scoring`/`backtest`
tests. New tests: `pnl_attribution` (entry-price classification, P&L sums) and
`assemble` (join + headline roll-up on a small fixture).

## Data availability & degradation

- **Empty-safe:** both pages degrade to an "accumulating — N days so far"
  message when data is sparse.
- **Edge Part A** needs settled `betting_log.jsonl` rows — present on the
  deployed/GitHub copy, absent locally (same as `forecast_log`).
- **Edge Part B** and **History** need the `[kalshi]` secret; reuse
  `bet_view`'s credential-absent handling (info message, no crash).
- **Local Streamlit cannot run in this dev env** (no streamlit/cryptography);
  rendering is verified on deploy. Pure functions are verified via pytest.

## Rendering conventions

Hand-rolled `market_view._html_table` for tables (canvas `st.dataframe` cannot
center — established project constraint) and Altair charts matching
`market_view._chart_colors()`, consistent with the History page.

## Out of scope (YAGNI)

- No new data capture — every field consumed already exists in the logs.
- No changes to the model, calibration, or betting-slot capture logic.
- No Robinhood/hourly-basis variants (the live site is Kalshi/CLI-only).
