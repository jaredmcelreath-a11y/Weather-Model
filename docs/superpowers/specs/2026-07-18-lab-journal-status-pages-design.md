# Lab, Journal & Status pages — design

**Date:** 2026-07-18
**Status:** approved

## Goal

Three new top-level dashboard pages, all mobile-friendly using the established
widget patterns:

- **Lab** — scored forward-log experiments: shadow-consensus head-to-head and
  the per-model (incl. matched-lead MOS) scoreboard.
- **Journal** — the trading diary: one full scorecard card per settled day,
  newest first, all settled days.
- **Status** — log-derived system health: is the Action alive, are the feeds
  fresh, are the logs growing.

## Shared foundation

- Three new modules — `lab_view.py`, `journal_view.py`, `status_view.py` —
  each with **pure, streamlit-free assembly functions** plus a thin `render()`,
  mirroring `edge_view` / `accuracy_view`.
- Three new `st.Page` entries in `app.py`. Final nav order: Forecast, Hourly,
  Accuracy, Edge, History, Lab, Journal, Status.
- Every `render()` starts with `market_view._theme_controls()` (theme/Settings
  on every page).
- **Mobile consistency by reuse, not new CSS:**
  - Metric boxes: `market_view.metric_card(label, value, tip)` inside
    `st.container(key="metrics2_<page>")` — gives the 2-per-row phone grid and
    tap-to-open bottom-sheet tooltips.
  - Tables: `market_view._html_table`.
  - Charts: the History-page equity-curve pattern — Altair with
    `alt.selection_point(on="click", nearest=True)` + visible point marks +
    tooltips, so touch devices tap-select points (hover-only Vega tooltips are
    already hidden on touch by the shared CSS).
- Every section is best-effort like the Edge page: a failing data source
  blanks its own section with a note, never the page.

## Lab page (`lab_view.py`)

Two sections, both scored against CLI settlements, both starting sparse. Each
shows the Edge-page-style "Accumulating — n settled days so far" info box until
data exists.

### A — Shadow consensus head-to-head

- Input: `forecast_log.load()` rows (basis `cli`) that carry
  `candidate_consensus`, joined to `settlements.as_map("cli")`.
- Pure fn `head_to_head(rows, settled)` → per `(variable, lead_bucket)`:
  `{n, prod_mae, cand_mae, prod_wins, cand_wins, ties, days: [...]}` where
  `days` carries per-day `(date, prod_err, cand_err)` for the chart.
  Rolling rows only (skip `capture_cohort` rows) so each (day, variable, lead)
  counts once.
- Display: top metric cards (settled days, production wins vs candidate wins,
  MAE gap), a lead × variable `_html_table`, and a tappable per-day
  absolute-error chart (two series: Production / Candidate).

### B — Per-model scoreboard

- Input: forecast-log rows with a `sources` dict (`ensemble`, `deterministic`,
  `nws`, `mos_lav`, `mos_nbs` means), joined to settlements.
- Pure fn `per_model_scores(rows, settled)` → per `(source, variable, lead)`:
  `{n, mae, bias}`. This is the matched-lead MOS evidence the held
  MOS-weighting decision waits on.
- **Data hygiene:** exclude `mos_lav` same-day (`lead_bucket == 0`) low rows
  captured before 2026-07-19 — the wrong-tail rows from the pre-fix
  `covers_extreme` bug (fixed 14a2a3a).
- Display: one `_html_table` split same-day vs day-ahead, plus a caption
  explaining bias sign convention (model − settled).

## Journal page (`journal_view.py`)

### Grading logic — extracted, not duplicated

- Extract `recap.day_scorecard(day, settled_map, forecast_rows, bet_rows)` from
  the existing `yesterday_scorecard` body; `yesterday_scorecard` becomes a
  one-line wrapper (`day_scorecard(today - 1 day, ...)`). The Morning Recap and
  the Journal can never disagree.
- Bet-row building (currently duplicated in `app.load_recap` and
  `app.load_portfolio_value`) is extracted into one cached `app` helper
  (`_bet_rows()` returning rows annotated with `target_date`); all consumers
  share it. Cloud-only (needs the `[kalshi]` secret); local/no-secret returns
  None and the P&L lines are simply omitted.

### Assembly

- Pure fn `assemble(today, settled_map, forecast_rows, bet_rows)`
  → `{summary, days: [entry, ...]}` newest first, one entry per settled day:
  - `high` / `low`: settled value, model call (0900 cohort → day-ahead → any
    lead, via `day_scorecard`), exact flag, signed miss, market-closer flag.
  - `flags`: front/convective from that day's forecast-log rows.
  - `pnl`: realized bet P&L via `recap.yesterday_pnl`-style attribution
    (by weather day), when bet rows exist.
  - `summary`: last-7-settled-days exact-bin hit rate (high, low), total
    realized P&L across the journal span, current exact-hit streak
    (consecutive most-recent days where BOTH high and low hit their bin;
    a day missing either variable's grade breaks the streak).
- All settled days are rendered (~30 now, grows ~1/day; plain HTML, cheap).

### Display

- Top summary strip: metric cards in a `metrics2_journal` container.
- One full-width HTML card per day (styled with the metric-card surface
  variables so themes apply): date header, High line, Low line, flag badges
  (⛈ storm / 🌪 front), P&L line when present. Exact hits get a ✓, misses show
  the signed error (e.g. "+1.2°F").

## Status page (`status_view.py`)

- Pure fn `checks(inputs) -> [{label, value, state, tip}, ...]` where `inputs`
  is a plain dict of timestamps/counts assembled by the loader; `state` is
  `green | amber | red | unknown` (missing data → `unknown`, shown grey).
  Thresholds live in the pure fn and are unit-tested:

  | Check | Green | Amber | Red |
  |---|---|---|---|
  | Action heartbeat (last cli consensus capture) | < 25 min | < 60 min | ≥ 60 min |
  | Obs reading age (snapshot current reading) | < 45 min | < 90 min | ≥ 90 min |
  | Forecast feeds | no dropped sources | 1 dropped | ≥ 2 dropped |
  | Calibration age (`computed` stamp) | < 36 h | < 72 h | ≥ 72 h |
  | Settlements currency | through yesterday | 2 days behind | older |
  | Betting log today | rows captured today | — | none today |

- Row-count table (`_html_table`): forecast_log, betting_log, settlements,
  consensus_history, calibration_history totals.
- Display: health cards in a `metrics2_status` grid; value prefixed 🟢/🟡/🔴
  (⚪ unknown); each card's tap tooltip explains the check and its thresholds.
- Data comes from the already-cached snapshot loader plus cheap log reads —
  no new credentials, works locally and on cloud.

## Loading & caching (`app.py`)

- `load_lab()` — forecast_log + settlements joins, ttl 6 h.
- `load_journal()` — forecast_log + settlements + shared `_bet_rows()`,
  ttl 1 h.
- Status: reuses `load_snapshot_kalshi()` (ttl 60 s) plus a `load_status()`
  for log timestamps/counts, ttl 60 s.

## Testing

- Pure-function tests per module: joins, grading, MAE/bias math, threshold
  states, sparse/empty inputs, the mos_lav exclusion rule, the recap
  extraction (yesterday_scorecard unchanged behavior).
- Render smoke tests via the existing streamlit-stub pattern
  (as in `test_recap_render.py`), asserting key strings/cards appear.
- Known local constraint: bet-row-dependent paths degrade to None locally
  (no `cryptography`); tests stub them.

## Out of scope

- No new data logging (all three pages read existing logs).
- No GitHub Actions API querying (log-derived health only).
- No pagination/virtualization on the Journal (revisit if it ever slows).
