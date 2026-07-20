# Prior-Day Trade Measurement — Design

**Date:** 2026-07-19
**Status:** Approved (brainstorming), pending implementation plan
**Related:** Plan C edge measurement (`edge_report.py`, `betting_log.py`),
audit roadmap item 4 accepted-scope note (LST climate-day fix, 15100eb),
[[plan-c-edge-measurement]], [[kalshi-cli-settlement-basis]]

## Goal

Measure two untested entry windows for the KDFW Kalshi temperature markets,
using the existing betting-log → edge-report instrument. **Measure only** — no
UI, no alerts, no sizing. The data decides in ~2–3 weeks whether either window
deserves trade support.

1. **Last-hour trade** (the audit roadmap item): the CLI climate day ends at
   06:00Z — 1:00 AM CDT in summer — and Kalshi's markets close at exactly that
   instant (verified live 2026-07-19: `close_time = 2026-07-20T06:00:00Z`).
   From clock midnight to 1 AM CDT, yesterday's markets are open, its high is
   settled and its low nearly settled, but the model serves nothing ("today" is
   clock-based). Question: **is the settled bracket still priced below ~99¢ in
   that hour?**
2. **Evening-before entry**: day D's markets open 14:00Z on D−1 (verified
   live), so tomorrow's high/low trade all evening. Question: **does the
   day-ahead model at 21:00–23:00 have more edge against the market than the
   same-day morning/afternoon slots?** (No retro shortcut exists:
   `consensus_history` only began logging tomorrow-target `market_ev` on
   2026-07-19.)

## Non-goals

- No Forecast-page "yesterday still live" block, no ntfy alert, no Kelly wiring.
  Those come only if the measured edge is real.
- No change to the production same-day slots or their logged rows
  (production-invariance test pins this).
- No changes to `consensus_history` or the charts.

## Design

### 1. New slot families in `betting_log.py`

Today's slots: 5 sunrise-anchored low slots (`sr-90`…`sr+30`) + 5 fixed
afternoon high slots (`15:00`…`17:00`). Two families join them:

- **Evening day-ahead** — labels `eve-21:00`, `eve-22:00`, `eve-23:00`; fixed
  local clock times. Each captures **both** `high` and `low` for **tomorrow**,
  from the snapshot's existing `tomorrow` block and `market["tomorrow"]`
  (already attached by `scheduled_log`).
- **Close-anchored** — labels `close-45`, `close-15`; resolved as 45/15 minutes
  before the **ending climate day's** end (`settlement.local_day_bounds(d).end`
  == Kalshi close). The current climate day is the date of `now` converted to
  `CLIMATE_TZ`. In summer these land at 00:15/00:45 CDT and target
  clock-yesterday; in winter at 23:15/23:45 CST and target clock-today.
  Season-proof by construction (same trick as the sunrise-anchored slots), no
  DST gating needed. Captures **both** variables.

Mechanics:

- `current_slot` gains the two families: evening = fixed clock comparison
  (like `HIGH_SLOTS`); close = offsets against
  `local_day_bounds(climate_day_of(now)).end`. Existing `SLOT_TOLERANCE_MIN`
  (±8 min) unchanged; the 10-minute scheduler cadence covers all new times,
  including the 00:00–00:59 CDT runs (Actions cron runs around the clock).
- Each slot resolves to a **target day**: existing slots → clock today
  (unchanged); `eve-*` → clock tomorrow; `close-*` → the ending climate day.
  `record()` picks the snapshot block (`today` / `tomorrow` / `yesterday`) and
  the market block whose day matches the target day, instead of hardcoding
  `today`. `SLOT_VARS` maps both new families to `("high", "low")`.
- Row schema unchanged for evening rows (the label prefix distinguishes the
  family). Close rows add one field, `market_asks`: raw per-contract
  `[floor, cap, yes_bid, yes_ask]` from `fetch_contracts`, because the logged
  `buckets` are an overround-normalized PMF and cannot answer "what would the
  settled bracket actually have cost". Evening and same-day rows do **not**
  get `market_asks` (their question is distributional, same as Plan C).
- Upsert key stays `(target_date, variable, capture_slot)`. Day-ahead and
  same-day rows for one target day coexist because the labels differ.

### 2. Serving the last hour (`model.snapshot` + `scheduled_log`)

- `snapshot` gains a `yesterday` block, computed **only while yesterday's
  climate day is still live**: `now < local_day_bounds(clock_today − 1).end`.
  True only 00:00–00:59 CDT in summer; never in winter (the window then lies
  before clock midnight, inside the normal `today` block). The block is
  `_predict_from(series, obs, yesterday, now, calib, settle_offset, live=True)`
  — the same live CLI-basis machinery (running extremes within the LST window,
  daily-summary anchor/floor, °C-wall sigma, peak locks) that already handles
  mostly-observed days.
- **Plan-level check:** during that hour the obs history must reach back to
  yesterday 01:00 CDT (~25 h). Verify `gather_series`' obs span; extend it
  (ideally only when the window is active) if it falls short.
- `scheduled_log` attaches `market["yesterday"]` via
  `kalshi.implied_forecast(var, yesterday)` in the same window.
- The raw contract quotes for `market_asks` are fetched whenever a **close
  slot** is in tolerance (not gated to the summer window — in winter the close
  slots read `market["today"]` and still need raw asks), for the ending
  climate day's contracts.
- Dashboard untouched: the extra block is ignored by every page.

### 3. Reporting (`edge_report.py`)

- New slots flow through `join`/`metrics` automatically as new
  `(capture_slot, variable)` blocks; settlement joining already keys on
  `target_date`.
- New per-slot stat `settled_bucket_ask` (mean and min across days of the
  eventually-settled bracket's YES ask at capture) for rows carrying
  `market_asks` — the direct cents-on-the-table ROI measure for the close
  slots.
- ASSESSMENT block ordering becomes family-grouped: day-ahead (`eve-*`) →
  same-day (`sr*`, afternoon) → close (`close-*`), so evening-vs-morning reads
  side by side. CSV column order gains `settled_bucket_ask` fields (null for
  slots without them).

### Decision criteria (~2–3 weeks of data)

- **Close slots:** if the settled bracket's mean YES ask at `close-15` is
  ≤ ~95¢ (and order-book size is non-trivial on the days it happens), the
  last-hour trade is real → then scope serve/alert/sizing as a follow-up.
  Watch the °C-wall caveat: on days where the running extreme sits on a °F
  boundary the bracket isn't actually certain, and the report should not count
  a fair 90¢ there as free money — the boundary slice already in
  `edge_report` (`is_boundary`) covers this.
- **Evening slots:** same Plan-C metrics as the existing slots (model vs
  market MAE, bin wins, disagreements, boundary slice), compared against the
  morning low / afternoon high blocks for the same target days.

### 4. Testing

- `current_slot` frozen-clock tests: evening labels at 21/22/23 local;
  `close-45`/`close-15` in summer (00:15/00:45 CDT → target clock-yesterday)
  and winter (23:15/23:45 CST → target clock-today); both DST transition days;
  near-miss times outside ±8 min return None.
- `record()` block routing: `eve-*` rows read the `tomorrow` snapshot/market
  blocks; `close-*` rows read `yesterday` (summer case) and `today` (winter
  case); rows missing their block are skipped, not mis-filed.
- `snapshot` gating: `yesterday` block present only inside the live window.
- `market_asks` present on close rows only; absent elsewhere.
- Production invariance: existing 10 slots produce byte-identical rows.
- `edge_report`: `settled_bucket_ask` math, family-grouped ASSESSMENT order.
