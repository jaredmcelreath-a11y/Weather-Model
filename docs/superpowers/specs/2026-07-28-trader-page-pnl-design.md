# Trader page: position detail, decision log cleanup, and a shadow P&L curve

Date: 2026-07-28
Status: approved, ready for implementation plan

## Why

The autonomous trader started producing real decisions on 2026-07-28 (after the
CLI-settlement-basis fix, `130c1c6`). The Trader page cannot yet answer the two
questions the shadow run exists to answer: *what am I holding, and is it making
money?*

Concretely:

- **Open Positions** shows one opaque `Ticker` column and no price. You cannot
  see when a position was taken, what it is worth now, or how close it is to
  being stopped out.
- **Recent Decisions** is an undifferentiated list. Every run logs one record per
  variable per city, so the two or three real actions of a day are buried under
  dozens of repetitive skips.
- There is **no P&L view at all**. The shadow run's whole purpose is to measure
  whether the strategy makes money before real money is at risk.

Two defects block an honest P&L, and both must be fixed first:

1. **Exit records carry no price.** `trader.py` logs
   `{"kind": "exit", ticker, side, count, reason, mode}`. The sale price is
   absent, so a round-trip that exits before settlement has unrecoverable P&L.
   Today's 21:12 reversal exit on `KXHIGHAUS-26JUL28-B97.5` is already
   unscorable.
2. **Positions held to settlement are never closed.** `runtime["entries"]` is
   popped only on an exit (`trader.py:144`); there is no settlement or
   day-rollover pass. A position that rides to settlement lingers into the next
   day, and the reversal path eventually dumps it at
   `cur_bid if cur_bid is not None else 0.01`. A settled market's book is empty,
   so a bracket that settled YES at $1.00 is booked as a **1¢ near-total loss**.
   While it lingers it also occupies the `max_open_per_variable: 1` slot.

## Scope

Four parts, in dependency order. Parts 1-2 are correctness; parts 3-4 are the
requested UI.

### 1. Log schema

Extend the records built in `trader.py` (`trade_log.build_record` already accepts
arbitrary fields, so no schema module change is needed):

- **entry** gains `variable`, `day`, `floor`, `cap`, `label`. `variable` and
  `day` are already in scope in the entry loop (`var`, `today_iso`); the bracket
  geometry is already on the `target` contract dict. Without them, scoring a
  position against settlement requires parsing the ticker string, which is
  brittle in both halves: the bracket suffix (`B97.5` → 97-98) and the day suffix
  (`26JUL28`), and the `T`-prefixed tail contracts do not follow the bracket rule
  at all (`KXLOWTAUS-26JUL28-T69` is labelled "68° or below" with `cap=69`).
- **`runtime["entries"][ticker]`** gains the same `day`, `floor`, `cap`, `label`
  alongside the `entry_ask`/`side`/`count`/`variable` it already stores. The
  settlement pass reads positions through `_managed_positions`, which sources
  shadow positions from this record, so the geometry must live here too — not
  only in the append-only log.
- **exit** gains `exit_price` (the bid sold into, or the settlement value) and
  `pnl` (`(exit_price - entry_ask) * count`).

**No backfill.** The curve begins at the first record carrying these fields.
Today's three records are excluded — the order books they would reference no
longer exist. `trade_pnl` must skip pre-schema records rather than assume zero.

### 2. Settlement close

A new pass in `trader.run_once`, running **before** the exit pass, so a settled
position is never handed to the reversal path.

For each managed position whose recorded `day` is earlier than the current
climate day (`settlement.climate_day_of(now, station)`, already computed in
`run_once`):

- Look up the day's settled value via `settlements.as_map("cli", station=...)`,
  keyed by the position's `variable`.
- If no settlement is recorded yet, leave the position alone and retry next run
  (settlement lands the following morning).
- Otherwise the position wins when `floor <= settled <= cap`. `exit_price` is
  1.00 on a win and 0.00 on a loss, for a `side == "yes"` position. (Every entry
  is YES-only as of `a71eea2`; a `side == "no"` position inverts. Handle both so
  the function stays correct if that changes.)
- Append an exit record with `reason` `"settled won"` / `"settled lost"` and the
  computed `exit_price`/`pnl`, then pop the ticker from `runtime["entries"]`.
- No order is placed. Kalshi settles the contract itself; in live mode the cash
  appears via `balance()`, and in shadow mode nothing ever existed.

A position whose runtime record predates the schema change has no `day`/`floor`/
`cap`. Treat a missing `day` as "older than today" so it cannot linger forever,
and close it with `reason: "settled (unscored, pre-schema)"` and no `pnl`, so it
frees its slot without contaminating the curve. This is the path today's live
`KXHIGHAUS-26JUL28-B99.5` position will take.

### 3. `trade_pnl.py`

New module. Pure functions, no network and no Streamlit, in the style of
`trade_logic.py`.

- `closed_trades(records) -> list[dict]` — pair each `entry` with the `exit`
  bearing the same ticker, oldest first. Emit
  `{ticker, variable, day, entry_ts, exit_ts, entry_ask, exit_price, count, pnl, reason}`.
  Skip entries with no matching exit (still open) and any pair missing
  `exit_price` (pre-schema).
- `daily_pnl(trades) -> list[dict]` — totals per **weather day** (the day encoded
  in the position, not the wall-clock timestamp), oldest first.
- `equity_curve(trades, today, open_marks) -> list[dict]` — cumulative P&L from
  0.0. Leads with an anchor point at 0.0 dated the day before the first trade, so
  a single trading day still renders a visible slope. Appends a live final point
  carrying today's open positions' unrealized P&L, folded into today's realized
  point when one already exists rather than duplicating the date. This mirrors
  `bet_history.equity_curve_live`'s shape; it is a separate implementation
  because that function is anchored to `STARTING_BANKROLL` and bound to the bet
  schema.

`open_marks` is `{ticker: current_bid}`, supplied by the caller so the module
stays IO-free.

### 4. Trader page

**Open Positions** — replace the single `Ticker` column with:

| Date | Time | Variable | Contract | Side | Count | Entry | Current | P&L | Stop-out | Model % |

- `Date` / `Time` come from the entry record's `ts`, rendered in `config.TIMEZONE`.
- `Contract` is the human label (`97° to 98°`), falling back to the ticker's
  bracket suffix for pre-schema rows.
- `Current` is the current **ask** — the price `stop_loss_hit` references.
- `P&L` uses the current **bid**, the price a sale would actually fill into.
  These are deliberately different columns; collapsing them into one "price"
  hides which number drives the stop.
- `Stop-out` is `entry_ask - stop_loss`, the ask at which the loop sells.
- `Model %` is the model's current probability for that bracket, via
  `model.prob_for_strike` — shows whether the model still backs the position.

Extend `trade_view.position_rows` (added in `d3ebe50`) rather than writing a new
builder; it already delegates to `trader._managed_positions` so the panel cannot
disagree with what the loop manages. Prices and model probabilities are passed
in by the renderer, keeping `position_rows` pure and testable.

**Recent Decisions** — three parts:

1. A status strip: one line per enabled variable giving its current state
   (`HIGH → holding B99.5`, `LOW → settled, no trade`).
2. A compact table of entries and exits only — the day's actual actions.
3. Skips behind a collapsed toggle labelled with their count.

**Shadow P&L** — a line chart at the bottom of the page.

- Y axis: cumulative dollars from 0.
- X axis: weather day.
- City toggle via `city_view.city_control("trader_pnl", arity=3)`, defaulting to
  **Both**; in Both mode one line per city, coloured from
  `market_view._chart_colors()`.
- A zero rule line, matching `bet_view.equity_chart`'s bankroll rule.
- Dates must be passed through `pd.to_datetime` before hitting a `:T` axis — bare
  date strings render a day early (see the Altair UTC gotcha that already bit the
  Lab and equity charts in `684f7a6`).
- Empty state: a caption explaining the curve appears once a position closes or
  settles, rather than an empty chart.

## Testing

- `trade_pnl` unit tests: entry/exit pairing, unmatched entries, pre-schema
  records skipped, weather-day bucketing, anchor point, live point folding into
  an existing same-day point, empty input.
- `trader` tests: settlement close wins/loses/defers-when-unsettled, frees the
  `max_open_per_variable` slot, does not place an order, and runs before the exit
  pass so a settled position never reverses out at 0.01.
- `trade_view` tests: `position_rows` column set, ask/bid distinction, stop-out
  arithmetic, pre-schema fallbacks; decision-list partitioning into
  actions/skips.
- Live verification via the `verify` skill against the real `trade-data` branch,
  with `TRADE_GH_REPO` set so the page reads live documents.

## Out of scope

- Backfilling P&L for records predating the schema change.
- Any change to entry/exit *strategy* — this is measurement only.
- Restoring `require_edge` to True, tuning `stop_loss`, or the whipsaw cost
  observed on 2026-07-28 (exited 57¢, re-entered the neighbouring bracket at
  70¢). The chart is what will let those be judged.
