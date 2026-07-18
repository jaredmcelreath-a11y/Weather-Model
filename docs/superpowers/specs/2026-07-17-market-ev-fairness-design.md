# Market-EV fairness for the Edge page — design

**Date:** 2026-07-17
**Status:** Approved (design), pending implementation plan

## Goal

Make the Edge page's model-vs-market comparison honest, and confirm the low
side is surfaced. Two substantive changes plus one verification:

1. **Fair bin comparison** — score the model and the market with the *same*
   statistic (both by where their expected value lands), fixing a mean-vs-mode
   asymmetry in `edge_report`.
2. **Liquidity annotation** — surface each subset's traded volume and flag
   thin-market rows, without excluding any day from the tally.
3. **Verify the low path** — prove a low betting-time row flows all the way
   into a low Edge row (low betting slots already ship; this just confirms the
   Edge page surfaces them).

## Background: what already exists

- **Low betting slots are already shipped** (commits `5f5f099`, `168aece`).
  `betting_log.LOW_SLOT_OFFSETS` captures sunrise-anchored `sr-90 … sr+30`
  slots for the low with the market attached; `capture_if_slot` is wired into
  `scheduled_log.py`; `edge_report.metrics` groups by `(capture_slot, variable)`
  so low rows appear once they settle. No capture work is needed — only the
  end-to-end verification in item 3.
- **Market EV** (`sources/kalshi.implied_forecast`) returns `ev` (mean of the
  implied PMF over bucket midpoints), `buckets` (normalized PMF), and `volume`
  (total traded across priced contracts). `betting_log._row` logs `market_ev`
  and `market_buckets` but currently **drops `volume`**.

## The bug (item 1)

In `edge_report._subset_metrics` the two sides are scored by different
statistics:

```python
model_b  = settled_bucket(r["cli_consensus"], r["market_buckets"])  # model MEAN -> bucket
market_b = top_bucket(r["market_buckets"])                          # market MODE (argmax) -> bucket
```

On a skewed PMF, mean-bucket ≠ mode-bucket for the *same* forecast, so
`disagreements` / `model_bin_wins` / `market_bin_wins` compare apples to
oranges.

### Fix

```python
market_b = settled_bucket(r["market_ev"], r["market_buckets"])      # market MEAN -> bucket
```

Now both sides use their expected value mapped into the settlement bucket.
`market_ev` is already logged on every row, so this **retroactively** corrects
all historical data. A day with `market_ev is None` is skipped from the
disagreement tally (matching today's behavior when `market_buckets` is absent).

`top_bucket` stays defined (still used elsewhere / harmless), but the
disagreement loop no longer calls it.

## Liquidity annotation (item 2)

Non-destructive: every day stays in the counts; thin markets are just marked.

- **`betting_log._row`**: also store `market_volume = market_var.get("volume")`
  when a market block is present. New field, forward-only — rows logged before
  this ships have no `market_volume` and read as `None` (blank, unflagged).
- **`config`**: add `MARKET_LIQUIDITY_FLOOR = 20` (contracts). A conservative
  first guess — it only drives the ⚠ marker, so it is safe to tune once real
  volume accrues. Documented as such.
- **`edge_report._subset_metrics`**: add
  - `market_volume`: median of `market_volume` across the subset's days that
    have it (`None` if none do).
  - the subset is **thin** when `market_volume` is not `None` and is below
    `MARKET_LIQUIDITY_FLOOR`.

  `join()` already spreads `**r`, so `market_volume` reaches the joined rows
  with no extra plumbing.
- **`edge_view._edge_rows`**: add a **volume** column (median, or "—" when
  unknown) and prefix the row's day-type with ⚠ when the subset is thin.

## Verify the low path (item 3)

Add a test that runs a low `betting_log` row through
`edge_view.assemble(rows, cli_map, hourly_map)` and asserts a
`(slot, "low", "all")` entry appears in the returned metrics with the expected
headline roll-up. This proves the Edge page surfaces the low side end-to-end.

## Testable seams

- `edge_report._subset_metrics` — extend existing `edge_report` tests: a case
  where the market's mode-bucket and ev-bucket differ, asserting the
  disagreement/win outcome follows the **ev** bucket; and a case asserting
  `market_volume` median + the thin flag.
- `betting_log._row` — assert `market_volume` is logged from the market block.
- `edge_view._edge_rows` — assert the volume column and ⚠ prefix appear.
- `edge_view.assemble` — the low-path verification above.

## Data availability & degradation

- The mean-vs-mean fix works on existing logged rows immediately (uses
  `market_ev`, already present).
- `market_volume` is forward-only; historical rows show "—" and never flag.
- All render paths remain empty-safe; no new network or credentials.

## Out of scope (YAGNI)

- No mode-vs-mode tally, no liquidity gating/exclusion of days.
- No change to the point-forecast MAE numbers (consensus vs EV is already fair).
- No model, calibration, or betting-slot capture changes.
