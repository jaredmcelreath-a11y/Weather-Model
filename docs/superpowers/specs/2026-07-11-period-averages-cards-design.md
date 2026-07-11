# Period averages on the Performance-by-Period tables

**Date:** 2026-07-11
**Status:** Approved, ready for implementation plan

## Problem

The "My Bets" / History page (`bet_view.py`) has a **Performance by Period** section
with Daily / Weekly / Monthly tabs. Each tab shows a table of per-period rows
(period label, % Gain, Gain $, running Total) built by `bet_history.period_table()`.
There is no at-a-glance summary of how a typical period performed — the user wants
averages (avg $ gained, avg % gained) plus the overall portfolio % gain surfaced on
each tab.

## Solution

Add a row of **metric cards** above each period table, reusing
`market_view.metric_card` (same visual style as the top-of-page metrics, wrapping
2-per-row on mobile). The per-period tables themselves are unchanged.

### Cards per tab (6)

| Card | Daily example | Definition | Basis |
|---|---|---|---|
| Periods | `8 days` | count of periods in the table | realized |
| Avg $ / period | `+$0.50` | total realized gain ÷ # periods (per-tab unit: day/week/month) | realized |
| Avg % | `+5.4%` | unweighted mean of each period's **% Gain** | realized |
| Green rate | `63%` (subtext: *5 of 8 days profitable*) | share of periods with gain > 0 | realized |
| Best / Worst | `+$3.20 / −$1.10` | max / min single-period $ gain | realized |
| Portfolio % | `+180%` | marked-to-market total % gain (`summary()['pct_gain']`) | marked-to-market |

The per-tab noun ("days" / "weeks" / "months") follows the tab.

### Intentional consistency choices

- **Cards 1–5 are realized-only**, matching the table rows directly below them
  (`period_table` already filters to `status in ("settled", "closed")`).
- **Portfolio %** is marked-to-market (`summary()['pct_gain']`, which includes open
  positions' live P&L) to match the top-of-page **Total % Gain** card. It is the same
  value in all three tabs. Its subtext states it is marked-to-market so the different
  basis from the other cards is not confusing.
- **Green** means gain > 0; a flat `$0.00` period counts as not-green, matching the
  `losses = pnl <= 0` convention in `summary()`.

## Architecture

### New pure function: `bet_history.period_summary(entries, pct_gain)`

- **Input:** `entries` = the list returned by `period_table(rows, period)` (each item
  `{label, pct, gain, total}`), and `pct_gain` = the marked-to-market total % from
  `summary()`.
- **Output:** a dict with:
  - `count` (int) — number of periods
  - `avg_gain` (float) — mean of `gain`
  - `avg_pct` (float) — mean of `pct` (a fraction, e.g. 0.054; caller formats ×100)
  - `green_count` (int) — periods with `gain > 0`
  - `green_rate` (float) — `green_count / count` (fraction)
  - `best_gain` (float) — max `gain`
  - `worst_gain` (float) — min `gain`
  - `pct_gain` (float) — passthrough of the marked-to-market total % (already a percent
    like the top metric, e.g. 180.0)
- **Precondition:** `entries` is non-empty (caller only invokes it when the table has
  rows), so all denominators are ≥ 1. Function may assume this; a defensive empty-input
  guard returning `None` is acceptable but not required.
- Pure/stateless — unit-testable with hand-built entry lists.

### Rendering: `bet_view.render()`

Inside the existing Daily/Weekly/Monthly tab loop, after computing `entries` and the
existing empty-guard (`if not entries: caption; continue`), before rendering the table:

1. Call `period_summary(entries, summ['pct_gain'])`.
2. Lay out 6 `market_view.metric_card`s via `st.columns(6)` (same pattern as the
   top-of-page metrics), using the per-tab noun for the Periods and Green-rate labels.
3. Then render the existing `_html_table(...)`.

Formatting reuses the existing helpers: `_fmt_pnl` for $ values (`+$/−$`), and inline
`{x*100:+.1f}%` / `{x:+.0f}%` for percentages, consistent with the table and top cards.

## Testing

Extend `tests/test_bet_history.py`:
- `period_summary` over a hand-built multi-period `entries` list: verify count,
  avg_gain, avg_pct, green_count/green_rate (including a flat-$0 period counted as
  not-green), best_gain, worst_gain, and pct_gain passthrough.
- Single-period case: best_gain == worst_gain == the one period's gain; green_rate is
  0 or 1.
- (Optional) empty-input behavior if a guard is added.

No new tests needed for `bet_view` rendering beyond confirming the existing page still
loads; the logic under test is isolated in `period_summary`.

## Out of scope

- No change to `period_table`, the equity curve, or the top-of-page metrics.
- No new data sources or Kalshi API calls.
- No change to the table columns themselves.
