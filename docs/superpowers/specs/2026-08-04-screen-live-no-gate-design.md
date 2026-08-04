# Screen page: live NO-price gate

**Date:** 2026-08-04
**Status:** approved

## Problem

The Screen table lists rows the fade is already lost on. Observed live:

| City | Var | Bracket | Price | NO Now | Gap | Settled | Ref | Hrs | Side |
|---|---|---|---|---|---|---|---|---|---|
| San Antonio | high | 97° to 98° | 0.82 | 9% | 0.6 | Yes | 98.6 | 7.69 | NO |
| Houston | low | 78° to 79° | 0.95 | 6% | 2.8 | Yes | 75.2 | 7.69 | NO |
| San Francisco | high | 75° or below | 0.96 | 4% | 4.0 | No | 79.0 | 9.69 | NO |

A NO ask of 9% means the live market has YES at ~91%: the bracket is
effectively resolved, and the screen's reference is the thing that is wrong,
not the price. There is no fade left to harvest.

The existing gates in `screen_rules._tradeable_price` (`MIN_CANDIDATE_PRICE`
0.10, `SETTLED_PRICE` 0.97) both act on the YES price **at firing time**, which
is up to an hour stale by the time the page renders — exactly the San Antonio
row, logged at 0.82 and now 0.91+. Nothing gates the live NO price, which is
the price the trade actually happens at.

## Design

One display-layer gate in `screen_view.py`. Nothing in `screen_rules.py` or
`screen.py` changes.

1. **`MIN_LIVE_NO_PRICE = 0.20`** — module constant beside
   `_SETTLED_BELOW_HOURS`, documented with the reasoning above.

2. **`tradeable_now(rows, live) -> (visible, hidden)`** — pure function.
   A row is hidden only when it has a live NO price *and* that price is
   strictly below the constant. A row with no live quote (`—`) stays visible:
   a missing quote is thin liquidity or a just-closed market, not evidence the
   market resolved against the fade. Returns the surviving rows and the count
   dropped.

3. **`render()`** filters through it before building the table, and captions
   the drop: *"3 hidden — live NO under 20%, the market has already resolved
   them."* The page never silently loses rows; same principle as
   `empty_notice` on the positions table.

4. **Freshness count** (`new_tickers`) is intersected with the visible rows, so
   the "N in red arrived with this hour's firing" caption cannot count a row
   that is not on screen.

5. When the filter empties the table entirely, the page shows the existing
   "No candidates in the latest firing" notice **plus** the hidden-count
   caption, so an empty table is never unexplained.

## Deliberately unchanged

- **`scan_candidates.jsonl` keeps every candidate.** The log is a measurement
  record; filtering at write time would discard rows the market later
  re-prices, and would silently change what the log means across the
  2026-08-04 boundary for any later analysis.
- **`SETTLED_PRICE` stays 0.97.** Lowering it to 0.80 would catch these three
  rows at firing but still miss any bracket that drifts up after the firing —
  the case actually hit here.
- **"Your Open Positions" is not filtered.** A position you hold at 9% is
  exactly the one you need to see.

## Testing

Unit tests against the pure function in `tests/test_screen_view.py`:

- a row under the threshold is dropped and counted
- a row at exactly 0.20 survives (strict `<`)
- a row above the threshold survives
- a row with no live quote survives
- the hidden count is correct with a mix of all four
- `new_tickers` ∩ visible: a filtered-out fresh row is not counted red
