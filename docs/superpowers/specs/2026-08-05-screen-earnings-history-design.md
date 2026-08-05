# Screen page: earnings history chart + trade table

Date: 2026-08-05

## Problem

The Screen page lists mispriced brackets and, since 2026-08-04, says how the
flagged fades have settled *per contract* (`screen_score`). What it cannot say is
whether the money actually made on them is going up. The only account view on the
page is "Your Open Positions" — a live snapshot with no history, no settled
trades, and no cumulative figure.

Real fades have been placed since 2026-08-03 (the first was a Denver bracket).
The question this answers is the plain one: **what have these trades earned over
time?**

## Scope

A cumulative-earnings chart and a full trade table on the Screen page, over the
user's real Kalshi fills in screened brackets since 2026-08-03. Read-only:
nothing here places, sizes, or exits an order.

Explicitly NOT in scope:

- Per-period (Daily/Weekly/Monthly) tables. With three days of history each tab
  would hold one row; add them when there is history worth reading.
- A separate bankroll for these trades. They share the one Kalshi account.
- Any change to a screen threshold. Measurement only, like `screen_score`.

## Which trades count

A trade counts when its ticker's series is one of the 40 screened series AND the
ticker does **not** belong to a station this app models itself (KDFW/KAUS today,
via `bet_history.ticker_station`). So: every non-Dallas/Austin weather bet.

Why membership is by city rather than by "the screen logged this bracket":

- The candidate log's first firing is `2026-08-04T01:04Z` — Aug 3, ~8pm CDT. A
  Denver bracket bought earlier that day is not in the log, and a strict
  log-membership test would silently drop exactly the trade the user named as the
  starting point.
- Dallas and Austin bets already have the History page. Excluding them via
  `ticker_station` (read from config, not hardcoded) means a third modeled city
  drops out of this page the day it is added.
- Since 2026-08-03 the user's non-Dallas/Austin bets exist *because* of this
  screen, so the city rule and the intent coincide.

The cost is that a manual bet on a screened city would be counted here. A
`Flagged` column makes that visible rather than hiding it: it says whether the
screen's own log contains the bracket, computed from the three days of candidate
rows the page already loads. Trades older than that window read `—`, and the
column tooltip says so — an honest `—` beats a fabricated `No`.

## Architecture

`screen_pnl.py` — new, pure: no Streamlit, no network, no clock except what is
passed in. Same relationship to `screen_view` that `bet_history` has to
`bet_view`.

```
SCREEN_START = date(2026, 8, 3)     # the first Denver trade; the one date constant

is_screen_ticker(ticker) -> bool
trade_rows(fills, settlements, meta, mark) -> list[dict]
earnings_curve(rows, today) -> list[{date, total}]
summary(rows) -> dict
```

- `trade_rows` delegates assembly to `bet_history.build_rows`, which already owns
  the episode splitting, the dominant-side rule, the sold-out-before-settlement
  branch and the fee accounting. Duplicating any of that would create a second
  P&L truth to keep in sync. It then filters to screen tickers and attaches
  `current_value` to open rows from the `mark` callable.
- `earnings_curve` is cumulative P&L from **$0**, one point per **weather day**
  parsed from the ticker (`bet_history._ticker_date`) — bucketing by settlement
  time plots each day's result a day late, since these markets settle ~1-2am the
  next morning. Today's open positions fold into the final point when that point
  already exists rather than appending a duplicate. A `$0` anchor dated the day
  before the first trade gives the line a visible origin when only one day has
  traded.
- `summary` reports realized-only W–L and win rate (an open bet has not won yet)
  but marks the money figures to market, matching `bet_history.summary`.

`screen_view.py` — `_render_positions` is replaced by `_render_history`:

- `_screen_trades()` (cached 60s) fetches fills + settlements since
  `SCREEN_START` with `all_markets=True` — the default scoping drops 38 of the 40
  screened cities, the bug that once made this section render nothing — plus
  `market_meta` per distinct ticker for the bracket label.
- Bracket labels come from Kalshi's market metadata and the city from the series
  prefix (`scan_cities.city_name`), so a trade older than the loaded candidate
  window still renders with a real label. The current code reads both off the
  candidate row, which cannot work for older trades.
- Layout: metric cards (Net P&L, Record, Win rate, Avg % Return) → earnings chart
  → one `Trade History` table covering open, sold and settled rows, newest first.
- Open rows carry a `~` prefix on their P&L/% and a row-tint class. The `_class`
  hook `_table` already supports — not an inline `<span>`, because `_table`
  escapes every cell.
- Every empty state keeps a caption saying *why* it is empty (no creds / no
  screened trades / N open elsewhere). An unexplained blank section is what hid
  the KDFW-scoping bug.

`earnings_chart` follows `bet_view.equity_chart`: transparent background,
tap-to-pin readout (touch devices never fire Vega hover), `pd.to_datetime` before
plotting (bare date strings on a `:T` axis render a day early), dashed rule at
`$0` rather than a bankroll.

`scan_cities.is_screened_series(series)` — a one-line public predicate so the
membership test is not written against the private `_SERIES_CITY` map.

## Column order

`Date, City, Contract, Result, P&L, % Gain, Entry, Exit, Qty, Flagged, Side`

A mobile decision, as with the candidate table: at 390px only four columns are
visible before the wrap scrolls, and on a *history* table the outcome and the
money are the point — so Result, P&L and % Gain sit ahead of the mechanics of the
fill, and Side sits last because it is NO on nearly every row.

Eleven columns overflow even a 1400px desktop window, which the first build got
wrong: with Side ahead of the money, `Result` fell off the right edge where
nobody would find it.

## Display corrections found in verification

Three things only rendering could show, all fixed:

- `st.caption` is markdown, and markdown reads a PAIR of `$` as inline LaTeX: a
  caption quoting two amounts rendered the text between them as italic math
  (`+$2.27 on $18.56` became one equation). Captions escape their dollar signs
  (`_caption_safe`); the HTML tables need no such thing.
- Staked is unsigned (`_usd`). `_money` printed it `+$18.56`, as though staking
  money were a gain.
- Percents use the app's true minus (`_pct_signed`), so one table does not spell
  a loss `−$3.08` and `-100.0%`.
- The chart's x axis is pinned to day granularity. Over a three-day span Vega
  picks hourly ticks and labels a daily line `12 PM`, `06 PM` — times at which
  nothing on this chart ever happens.

## Testing

`tests/test_screen_pnl.py`

- Membership: a Denver high/low ticker is in; `KXHIGHTDAL`/`KXLOWTAUS` are out;
  an unmapped prefix is out; malformed input does not raise.
- Curve: starts from $0; buckets by weather day, not settlement day; two trades
  on one day make one point; open MTM folds into an existing same-day point
  instead of duplicating it; the anchor sits one day before the first trade.
- Summary: W–L counts realized only; net P&L includes open marks; ROI and median
  per-trade return over a known set.

`tests/test_screen_view.py` (extended)

- Display rows: an open row is marked `~` and tinted; a settled loss reads its
  result; a row with no mark reads `—` and does not raise.
- Empty notices name the reason.

## Verification limit

There is no local `secrets.toml`, so the real table cannot be rendered on this
machine. Verification is unit tests plus a stubbed-data screenshot; the live
numbers only prove out on the deployed app.
