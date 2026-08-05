# Screen candidate outcome scoring

**Date:** 2026-08-04
**Status:** approved

## Problem

The Screen has flagged brackets since 2026-08-03 and nothing measures whether
those flags were right. `scan_report.py` builds the price reliability curve but
never reads `CANDIDATES_PATH`. Every threshold on the page — the 4°F gap, the
0.10 price floor, the 0.20 live-NO gate — is a guess with no feedback.

**The trap this must avoid:** of the 7,994 settled brackets on the scan-data
branch, 6,667 settled NO — a **83.4% base rate**. A screen whose flagged fades
"win 83% of the time" therefore has *zero* edge. A raw hit rate would flatter
this table enormously while proving nothing.

## Design

New module `screen_score.py`: pure scoring over the two existing logs, joined
on `ticker`. No new data collection, no trading.

### One decision per bracket

The same bracket is flagged at every firing; that is one trading decision, not
twenty. Score the **earliest** candidate row per ticker — the moment you could
have acted — and carry a `firings` count recording how long it persisted. Any
other choice lets a single long-lived row dominate the sample.

### The arithmetic

`price` on the candidate row is the YES ask, so the fade costs `1 − price` and
pays `1.00` when the bracket settles NO:

| Quantity | Definition |
|---|---|
| `won` | `result == "no"` |
| `pnl` | `+price` when won, else `−(1 − price)` |
| `implied_no` | `1 − price` — what the market thought the fade was worth |
| **`edge`** | **realized NO rate − mean `implied_no`** |

`edge` is the headline. Hit rate is reported beside it but never alone, and the
83.4% all-brackets base rate is printed as a reference line.

### Splits

By `kind` (the spec claims `dead` ≈ 100% and `forecast` ≈ chance — this is the
first test of that), by gap band, by hours-to-close band, and by storm band
once enough rows carry the field added earlier today.

### A required data fix

The candidate row stores only the YES ask, so the true NO cost cannot be
reconstructed — and `1 − ask` is **optimistic**, since the real fill is
`1 − bid` or worse. `build_snapshot_row` already carries `yes_bid` and
`volume` and `screen_rules._candidate` discards both.

Both are added to the candidate row going forward. Scoring uses the exact NO
cost when `yes_bid` is present and falls back to `1 − price` otherwise, and
each reported number says which mode produced it. Without this the measurement
would be biased in its own favour.

### Refusing to over-read

`MIN_SAMPLE = 30` decisions, below which the report prints the counts and
explicitly declines a verdict, matching `MIN_LEAD_DAYS` and `min_nights`
elsewhere in this repo. **The first run will be below it** — candidates begin
2026-08-03 and settlements 2026-08-04 — and that is the correct output, not a
failure. The value accrues daily.

Every summary also carries the standard error of the win rate, so a difference
smaller than the noise cannot be read as a result.

### Surfaces

- `python screen_score.py report` — full breakdown with all splits.
- A four-number block under the Screen table: n, hit rate, implied, edge, with
  the insufficient-sample caption when it applies.

## Out of scope

Any automatic change to screening thresholds, and any trader use. This measures;
it does not act. Deciding what to do about long-lead rows (the flat 4°F gap is
~5.7σ same-day but ~2.1σ day-ahead) waits for this data.

## Testing

- earliest flag wins; `firings` counts the repeats
- tickers with no settlement are excluded, not counted as losses
- `pnl` both directions, and `edge` arithmetic against a hand-computed case
- the `MIN_SAMPLE` gate suppresses the verdict but still reports counts
- exact NO cost used when `yes_bid` is present, documented fallback when not
- bucket splits partition the records without loss
