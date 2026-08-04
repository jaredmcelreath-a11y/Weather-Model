# Screen page: storm-chance column

**Date:** 2026-08-04
**Status:** approved

## Problem

The Screen flags a bracket when the market's price sits far from the NWS
forecast, and reports that distance as `Gap`. But a gap is only as trustworthy
as the forecast it is measured against, and a convective day is exactly when
that forecast is least reliable: a downdraft can crash a high or hold a low
down hours after the smooth guidance said otherwise.

Nothing on the page said whether storms were in play. Measured across the 20
screened cities on 2026-08-04, four of the eight flagged rows sat under active
thunderstorm forecasts (Chicago 80%, Philadelphia 80%, Miami 71%) while the
rest were clean (Boston, Denver, San Francisco all 0%) — a real split the table
was silent about.

## Design

**Zero extra requests.** The NWS hourly payload the screen already fetches for
`daily_extremes` carries `probabilityOfPrecipitation` and `shortForecast` on
every period. This reads the data already in hand.

### `screen_forecast.storm_chance(periods, day, tzname, variable, now)`

Returns the max POP, as a whole percent, over the climate-day periods that:

1. still lie ahead of `now`,
2. fall inside the window where the extreme can still move, and
3. mention thunder in `shortForecast`.

`0` when the window has hours but no thunder in them. `None` when the window is
empty, the day has no data, or the extreme can no longer move.

### The window is asymmetric on purpose

- **High** — from `now` through the **forecast peak hour**. Once the peak has
  passed a storm cannot raise the day's high, so counting a 9pm thunderstorm
  against an afternoon high row is noise.
- **Low** — from `now` through the **end of the climate day**. Evening
  convection can crash a low before midnight; that is the entire reason
  `convective.py` exists for KDFW. Mirroring the high's cutoff would hide the
  case that matters most.

This matches the asymmetry already recorded for the peak-lock guard: the low is
not the high run backwards.

### Data flow

Computed in `screen.py` inside the existing per-day loop, where `periods`,
`day`, `tzname` and `variable` are all in scope, and attached to each candidate
as `storm` before it is appended. `screen_rules.py` is untouched — it answers
"is this a candidate", not "what context does the human need".

### Local refactor

`daily_extremes` and `storm_chance` both need "the periods belonging to this
LST climate day", so that windowing moves into a shared `_day_periods` helper
rather than duplicating the offset arithmetic.

### Display

A `Storm` column immediately after `Gap`, because it qualifies how much to
trust the gap. Whole percent, `—` when absent — which is also what every row
logged before this ships will show, so there is no log migration. Tooltip
states it is thunderstorm-only POP over the hours that can still move the
extreme, from the same forecast as `Ref`, and that it is a caution flag, not a
probability that the bracket is wrong.

## Out of scope

Filtering, sorting or colouring on the value, and any use by the trader. The
column is informational.

## Testing

- max POP is taken across thunder hours only; non-thunder precip ignored
- a high's window ends at the forecast peak hour; a low's runs to end of day
- periods outside the LST climate day are excluded
- a missing `probabilityOfPrecipitation` counts as 0, not a crash
- empty window → `None`; hours but no thunder → `0`
- `screen_pass` attaches the value to every candidate it writes
- the view renders `—` for a row logged before the field existed
