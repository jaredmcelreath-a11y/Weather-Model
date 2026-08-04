# Multi-city mispriced-bracket screen: surface candidates, don't trade them

Date: 2026-08-03
Status: approved, ready for implementation plan

## Why

The autonomous trader buys favorites and runs 8W/9L across its whole shadow
history (avg win +0.20, avg loss -0.35, so it needs ~64% to break even). The trade that actually looks good is the opposite:
fading a bracket the market prices richly when the forecast says it is far away —
a Denver low market asking ~35% on "above 72" when the forecast was well below
that. That trade was found by hand, on a city this system does not model.

Two things stand between here and doing that systematically:

1. The trader **structurally cannot see it.** `select_bracket` only ever returns
   the bracket containing the market's EV, `size_bracket` is YES-only by design,
   and `require_edge` is currently `False`. It cannot look at other brackets,
   cannot take the NO side, and is not comparing model price to market price.
2. A full fade bot wants a **calibrated model per city** — per-city settlement
   basis verification, convective research, weeks of calibration — for ~20
   cities.

The 2026-08-03 scanner (`scanner.py`) already answers whether these markets are
*systematically* mispriced. This spec answers a different and more immediately
useful question: **where is a gross mispricing right now?**

The key insight is that gross mispricing detection needs far less precision than
trading does. Resolving 100 vs 101 requires the whole calibrated apparatus.
Noticing that the market pays 35% for an outcome 6°F from the forecast does not.

## What this is not

Read-only. No orders, no trading logic, no imports from `trader`, `trade_logic`,
`trade_state`, `kalshi_orders`, or `trade_params`.

It is a **screen, not a signal.** Its job is to narrow ~480 live brackets to a
handful worth two minutes of human attention. Deciding to act on a candidate is
the user's, exactly as it was for the Denver trade.

No ntfy, no push notifications, no alerting of any kind. Candidates are written
to a log and displayed on a page.

## The city table is cheap — deliberately

`scan_cities.py`: a mapping of Kalshi series ticker to `(lat, lon)`. The live
scan finds 40 priced series across ~20 cities (a high and a low series each), so
that is ~40 one-line entries pointing at ~20 distinct coordinates, verified once
by hand.

**Airport coordinates, not city center.** The nearest-station lookup below
resolves to whatever station is closest to the point given, and the realized-
extreme check in Phase 2 is only sound if that station is the one Kalshi settles
on. Denver's airport point resolves to `KDEN` first; a downtown point would not.

Everything else derives from NWS `points/{lat},{lon}` and caches for 24h:

- `properties.gridId/gridX/gridY` → the forecast gridpoint
- `properties.timeZone` → e.g. `America/Denver`, which is why **no timezone
  column is needed** in the table
- `properties.observationStations` → nearest stations, first entry is the airport

Verified live 2026-08-03 against Denver (39.8561, -104.6737), a city with no
`StationConfig`: gridpoint `BOU 74/66`, timeZone `America/Denver`, stations
`['KDEN', 'KCFO', 'KBKF', 'KEIK']`, hourly forecast periods present.

This is explicitly NOT the expensive per-city work. No convective counties, no
CLI product verification, no calibration, no `StationConfig` entry.

## Phase 1 — forecast distance (soft screen)

For each city, fetch the NWS hourly forecast and reduce it to a high and a low
for the bracket's own climate day.

The target day is derived per market from its `close_time` (Kalshi's close IS the
climate-day end), converted into the timezone NWS returned for that point. A
single firing therefore screens both today's and tomorrow's markets, each against
its own day's forecast — no assumption that "today" means the same thing in
Denver and Boston.

For each priced bracket in that city's ladder, compute the gap in °F from the
forecast value to the **nearest edge** of the bracket. A bracket containing the
forecast has gap 0; `72-73` against a forecast of 66 has gap 6.

Flag a candidate when:

- `price >= MIN_CANDIDATE_PRICE` (0.10) — below that there is nothing to
  harvest, and
- `gap >= MIN_CANDIDATE_GAP_F` (4.0)

**Distance, not probability.** There is no per-city calibrated sigma, and
converting a gap into a probability with an invented sigma manufactures a
confident-looking number that is a guess. That is precisely the season-readiness
phantom-edge bug (2026-07-17), where a bin outside the model's representable
range printed 0% and produced a live "0% -> BUY NO +85" signal. "The market pays 35% for something 6°F
from the forecast" is an honest screening statement; a fabricated "8% true
probability" is not.

Thresholds are module constants, tunable once there is candidate history to
score against.

## Phase 2 — realized extreme (hard screen)

For each city, fetch observations from the nearest station covering the climate
day so far — the same `close_time`-derived day window as Phase 1, from the day's
start up to now. Phase 2 applies only to markets whose climate day is currently
in progress; a future day has no realized extreme to bound it.

The physics is one-directional and needs no calibration:

- the **minimum realized so far is a ceiling** on the settled low — the low can
  only go lower
- the **maximum realized so far is a floor** on the settled high — the high can
  only go higher

Any bracket lying entirely on the impossible side of that bound is `dead`. A low
bracket of `72-73` when the day has already touched 66 cannot settle YES, at any
price, regardless of forecast. This is the same principle as the existing
passed-bracket-zero work, applied nationally without a model.

**Guard:** require **two corroborating observations** before treating an extreme
as established. A single spurious reading must not declare a live bracket dead.
This mirrors the `min_support` guard in the KDFW model, and errs toward missing a
candidate rather than inventing one.

Phase 2 fires later in the day than Phase 1 — a low is only bounded once the
overnight minimum is largely in — and finds fewer candidates, but they are hard
rather than probabilistic. Both kinds are written to the same log, distinguished
by `kind`.

## Output

`scan_candidates.jsonl`, appended per firing on the existing `scan-data` branch:

```json
{"ts": "2026-08-03T18:00:00Z", "series": "KXLOWTDEN", "variable": "low",
 "ticker": "KXLOWTDEN-26AUG03-B72.5", "floor": 72, "cap": 73,
 "price": 0.35, "forecast": 66.0, "gap": 6.0, "kind": "forecast",
 "hours_to_close": 11.0}
```

`kind` is `"forecast"` (Phase 1) or `"dead"` (Phase 2). For a `dead` row,
`forecast` carries the realized bound rather than the forecast value and `gap` is
the distance past it.

A log rather than a live-only view, for one reason: it makes the screen
**scorable**. Joined to `scan_settled.jsonl` after settlement it answers *"of 112
brackets flagged, how many expired worthless?"* — which is the only way to learn
whether the screen works instead of trusting it. A live-only page can never
answer that.

## Screen page

A new nav page reading `scan_candidates.jsonl`: most recent firing first, one row
per candidate, columns for city, variable, bracket, price, forecast/bound, gap,
kind, and hours to close. Sorted by `price × gap` — the two things that make a
candidate worth looking at.

Follows the existing hand-rolled HTML table pattern (canvas-rendered `st.dataframe`
cannot center cells, which is why every display table here is hand-rolled HTML) and the established nav ordering.

A scoring view — flagged vs. actually-expired-worthless — is deliberately
deferred until candidate history exists to score.

## Testing

Pure functions, unit-tested against **fixtures copied from live NWS and Kalshi
responses, not invented**. This is non-negotiable and comes at a cost already
paid: the scanner's tests passed against a hand-written market payload whose
field names do not exist, and a live pass produced 0 rows from 40 active series.

- bracket-to-forecast gap, including a bracket containing the forecast (gap 0)
  and open-ended `less`/`greater` tails with one strike
- the dead-bracket rule for both variables, including the two-observation guard
  and the case where a single outlier reading must NOT kill a bracket
- daily high/low extraction from hourly periods across a climate-day window in a
  non-local timezone
- candidate row construction and the `price`/`gap` thresholds at their boundaries
- a city whose NWS lookup fails is skipped, not fatal — one city's outage must
  not cost the others their screen

## Known limits

**The NWS forecast is public information.** Every market participant can see it.
Most forecast-vs-price gaps therefore mean the market knows something the
forecast does not — a recent trend, an observation, a model update — rather than
that the market is wrong. This is why the output is a screen for human review and
why Phase 2's hard bound is the more trustworthy of the two.

**No calibration means no probabilities.** The gap is a distance. It does not say
how likely the bracket is.

**Basis mismatch.** The NWS point forecast is not the CLI daily basis Kalshi
settles on; the measured KDFW difference is ~+0.9°F. Immaterial at a 4°F
threshold, material if the threshold is tightened, and unquantified for cities
other than KDFW.

**Station match is an assumption.** The dead rule is only as sound as the nearest
station being the settlement station. Airport coordinates make this true for
Denver; each city's first station should be eyeballed once when the table is
built.

**Observations can be revised or missing.** The two-reading guard reduces but does
not eliminate this.

## Out of scope

Any trading or order placement; ntfy or any alerting; probability estimates;
per-city `StationConfig` entries; calibration of any kind; the flagged-vs-settled
scoring view; changes to `scanner.py`'s existing snapshot or settlement passes.
