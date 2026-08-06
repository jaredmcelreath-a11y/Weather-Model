# Screen page: Ref-drift column

**Date:** 2026-08-06
**Status:** approved

## Problem

`Ref` is the reference the `Gap` and `Str` columns are measured from: the NWS
daily extreme for that bracket's climate day, folded with any temperature
already realized. It is taken at face value. Nothing on the page says whether
that forecast is currently verifying against the station it is supposed to
describe.

Found live on 2026-08-06. San Francisco `71° or below` was flagged at `Gap 4.0`,
`Str 0.8×` off `Ref 75.0`. The 75 was real — NWS grid MTR/85,98 forecast a 75°F
peak at 3–4pm PDT, and `KXHIGHTSFO` settles on CLI SFO, the same airport the
grid point resolves to — but the same forecast was running hot against KSFO all
morning:

| Hour (PDT) | NWS forecast | KSFO actual |
|---|---|---|
| 11:00 | 68 | 66.2 |
| 12:00 | 71 | 68.0 |

A ~2–3°F standing error puts the real peak nearer 72–73. The fade was probably
still right, but on a 1–2°F margin rather than the 4.0 the table showed. `Str`
overstated the evidence, and the page gave no way to see it.

This is a gap in `screen_forecast.fold_realized`, which takes
`max(realized, forecast)` for a high and never asks how that forecast is doing
today.

## Scope decision

**Display-only.** `Ref`, `Gap` and `Str` are unchanged, and the set of brackets
that get flagged is unchanged. The new column is context on how much to trust
`Gap`, in the same spirit as `Storm` — never an input to it.

Rejected: correcting `Ref` itself. It would alter which rows clear the 4°F bar
and therefore what lands in `scan_candidates.jsonl`, and
[[screen-outcome-scoring]] has only been accumulating that record since
2026-08-03. Breaking the comparability of a two-day-old measurement to fix a
display problem is the wrong trade.

## Design

**Zero extra requests.** On a climate day already in progress, `screen_pass`
already holds both halves: the NWS hourly `periods` fetched for
`daily_extremes`, and the station observations fetched for the `dead` screen.
The drift is a comparison of data already in hand.

### `screen_forecast.observed_anchor(readings, now)`

Returns `(temperature, timestamp)` — the **mean of the readings within the last
`ANCHOR_WINDOW_MIN` (30) minutes**, paired with the mean of those readings' own
timestamps. Returns `(None, None)` when it abstains, so the caller can unpack
unconditionally. One reading in the window is enough; its mean is itself.

Averaging rather than taking the newest reading is the
[[nowcast-resolved-and-consensus-stability]] lesson. Observations arrive in
whole degrees Celsius at many stations (see the `settled_range` work in
`screen_rules.py`), so a single reading jitters by up to 1.8°F between samples.
The drift shifts the entire remaining forecast 1:1, so a jittering anchor swings
the implied `Ref` while the temperature is flat.

When no reading falls in the 30-minute window, the single newest reading is used
instead, provided it is within `MAX_ANCHOR_AGE_MIN`. This is what keeps
slow-reporting stations usable at all — see "Station cadence" below.

### `screen_forecast.forecast_at(periods, when)`

The hourly forecast temperature linearly interpolated between the two periods
bracketing `when`. `None` when no period brackets it and neither end is in
range.

Interpolated, not snapped to the last whole hour. Snapping makes the value a
step function that jumps at the top of each hour while the observation anchor
has not yet updated, which is the sawtooth `model.py:211` documents at KDFW.

When `when` precedes the earliest available period, the earliest period's value
is used flat. NWS hourly returns exactly one past hour (verified 2026-08-06 at
FFC, MTR and OKX: `first == the current hour` at all three), so this extrapolates
at most ~1 hour and only for the slow-reporting stations.

### `screen_forecast.forecast_drift(periods, readings, now)`

```
anchor, anchor_time = observed_anchor(readings, now)
drift = anchor - forecast_at(periods, anchor_time)
```

Signed °F. Positive means the station is warmer than the forecast said, i.e. the
forecast is running cold; negative means it is running hot, which is the SF
case. `None` when either input abstains.

Interpolating to **the anchor's own timestamp**, not to `now`, is what makes
this correct across stations of different cadence. A 60-minute-old Denver
reading compared against the forecast for *now* would manufacture drift out of
the diurnal ramp alone; compared against what the forecast said *for that hour*,
it is apples to apples. Staleness then costs recency, not correctness.

### Applying the drift — re-fold, do not shift `Ref`

```
implied = fold_realized(forecast_extreme + drift, realized, variable)
```

The drift shifts only the **forecast** component. `Ref` is already
`fold_realized(forecast, realized)`, so shifting `Ref` directly would move a
realized number that is fact.

Worked example — a high whose peak passed at 3pm, realized 95, remaining
forecast 88, drift −3:

- shifting `Ref`: `95 - 3 = 92`. Wrong. 95 actually happened.
- re-folding: `max(95, 88 - 3) = 95`. Correct.

This also means **a settled extreme shows no drift for free**. No separate
"window has closed" rule is needed, and in particular `_still_open` is *not*
reused here — `fold_realized` already handles it on both sides. A low that
bottomed out at dawn behaves the same way: `min(71.6, tonight + drift)` stays
71.6.

Against the two live rows that motivated this:

| Row | Ref | Drift | Shown |
|---|---|---|---|
| San Francisco high, peak still ahead | 75.0 | −3 | `75→72` |
| Atlanta low, already bottomed at 8:45am | 71.6 | +2 | `72→72` |

### Persistence: straight 1:1, no decay, no cap

The full current error is applied however many hours out the extreme is. The
column states a conditional — *if this error persists* — and the tooltip says
exactly that. It is not a forecast.

Rejected: decaying the shift with time-to-extreme, and capping it at a fixed
°F. Both are more physically honest in principle, and both require a constant
this system has never measured. Inventing one is the
[[season-readiness-bins]] phantom-edge trap, where a made-up number produced a
live `0% → BUY NO +85`. 1:1 is also what `model.py`'s KDFW anchor already does
(`offset = obs_now - fc_now`, applied to the whole remaining forecast).

If the logged `drift` field later shows the error decays predictably, that is a
measured follow-up, not a guess made now.

### Abstain rules

`—` when any of:

- the climate day is not in progress (no observations exist to compare),
- no reading within 30 minutes **and** the newest is older than 70 minutes,
- no forecast period brackets the anchor time,
- the day has no forecast extreme for this variable,
- the observation fetch failed (`screen_pass` already degrades to
  forecast-only on that path),
- the row is a `dead` candidate — its `Ref` is the realized bound, a fact, so
  there is no forecast drifting, and an arrow whose left-hand number was not the
  `Ref` on display would be a visible inconsistency.

**This will blank New York and Denver a meaningful fraction of the time.**
Accepted deliberately: a context column that is honestly empty beats one that is
quietly wrong.

### Station cadence — the constraint behind the anchor rules

Measured 2026-08-06 across screened stations:

| Station | Median gap | Newest reading age | Readings in last 30 min |
|---|---|---|---|
| KATL, KSFO, KMIA, KLAS | 5 min | 18 min | 3 |
| KNYC | 31 min | 67 min | 0 |
| KDEN | 60 min | 55 min | 0 |

A uniform "last 4 readings" anchor — the KDFW rule, written for a 5-minute feed
— would silently span 4 hours at Denver. Hence a time-based window with an
explicit age cap, and interpolation to the anchor's own time.

## Storage

Two new floats attached in `screen.py`'s day loop, the same way `storm` already
is — `screen_rules._candidate` is not touched, because these are context, not
screening inputs:

- `drift` — signed °F, the forecast's current error against the station.
- `drift_ref` — the implied reference after re-folding.

Both must be **logged**, not computed at display time. Unlike `Str`, which
`screen_view` derives from fields already on the row and which therefore applied
retroactively with no migration, the drift depends on the forecast payload as it
stood at that firing. It cannot be reconstructed later.

No log migration. Rows written before this feature lack both keys and render
`—`, the same precedent the `storm` key set.

`drift` is carried for [[screen-outcome-scoring]] as well as display: it is the
raw measurement that would let a future pass ask whether the correction actually
predicted anything.

## Display

New `Drift` column in `screen_view._COLUMNS`, immediately before `Ref` (so the
order becomes `… Storm, Settled, Drift, Ref, Hrs, Side`).

Rendered as whole degrees, `75→72`, matching the whole-°F basis Kalshi settles
on. `drift` and `drift_ref` are **stored** as floats; the rounding is a display
choice only.

Both sides of the arrow are rounded, which is why a row can read `72→72` beside
a `Ref` column showing `71.6` — as the Atlanta example above does. That is
intended: the arrow answers "what does this bracket settle at", and settlement is
whole degrees. The unrounded reference stays visible in the adjacent column.

`—` when `drift_ref` is absent or `None`.

Shown, never sorted or filtered on. Sort stays on urgency, the same discipline
`Str` follows — filtering on a context column would destroy the evidence
[[screen-outcome-scoring]] exists to collect.

Tooltip, added to `screen_view._TIPS`:

> How the NWS forecast is verifying against the station right now, applied to
> Ref. `75→72` means the forecast is running 3°F hot at this hour, so if that
> error persists the real extreme is nearer 72. A conditional, not a forecast —
> it assumes the current error holds to the extreme, which it may not. Gap and
> Str are NOT adjusted by this. `—` means no recent observation, or the extreme
> has already formed.

## Refactor this forces

`screen.py::_observed_temps_f` discards timestamps, returning bare °F floats.
The anchor needs times. It becomes `_observed_readings`, returning
`(timestamp, temp_f)` pairs; the existing `realized` callers take the values.

## Error handling

Any failure in the drift path yields `None` and never raises. `screen_pass`
already wraps the observation fetch in `except Exception` so one city cannot
cost the others; the drift computation sits inside the same guarantee. A `None`
drift is indistinguishable from any other abstain at the display layer, which is
correct — all of them mean "nothing to say here".

## Testing

Unit:

- `forecast_at` — interpolation between two hours; exactly on an hour; before
  the earliest period (flat); past the last period; empty periods.
- `observed_anchor` — mean over the 30-minute window; falls back to the newest
  reading outside it; abstains past the 70-minute cap; abstains on empty.
- `forecast_drift` — sign convention (station warmer ⇒ positive); abstains when
  either input abstains.
- Re-fold behaviour — the passed-peak high and the formed low both show no
  movement; a high with the peak still ahead moves by the full drift.
- `screen_pass` — `drift`/`drift_ref` land on candidate rows on an in-progress
  day and are absent on a future day.
- `screen_view` — cell formats as `75→72`; rounds both sides, so a 71.6 ref with
  no effective drift reads `72→72`; renders `—` for a legacy row with no
  `drift_ref` key.

Live: a fixture built from a real KSFO hourly payload and real KSFO
observations, asserting the SF case reproduces `75→72`.
[[mispriced-bracket-screen]] records that unit tests passed against both of that
feature's original defects and only a live pass caught them. The same rule
applies here: run the real thing once before believing it.

## Out of scope

- Changing `Ref`, `Gap`, `Str`, or which brackets are flagged.
- Any decay or cap on the shift (see above).
- Backfilling drift onto historical candidate rows — impossible, the forecast
  payloads are gone.
- Applying the same anchor to the KDFW/KAUS model, which already has one.
