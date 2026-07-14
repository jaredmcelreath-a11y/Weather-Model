# LST climate-day settlement window — design

**Date:** 2026-07-14
**Status:** approved design, pending implementation plan

Follows the verification in `docs/benchmarks/2026-07-14/climate-day/FINDINGS.md`.

## Problem

The Kalshi/CLIDFW climate day runs midnight-to-midnight **Local Standard Time**
(UTC−6 year-round) — i.e. 1:00 AM → 1:00 AM CDT during daylight saving; it
coincides with clock time only in winter (CST). The model's `local_day_bounds`
builds the day window in `America/Chicago` (clock midnight → midnight), so from
mid-March to early November the model's settlement window is offset one hour from
the window Kalshi actually settles on.

The settlement *truth* (`settlements.jsonl` via IEM `daily.py`) already matches
the CLI, so scoring, corrections, and offsets are correct. The gap is in the
**live model's window logic** and in the **backtest/per-source extremes** (which
also go through `local_day_bounds`). Proven by May 26, 2026: the CLI minimum of
67 was recorded at "11:59 PM LST" = 12:59 AM CDT May 27, a reading the
clock-midnight window drops from May 26 entirely.

## Decision summary

| Question | Decision |
|---|---|
| Window basis | **Global move**: `local_day_bounds` builds in fixed LST (`Etc/GMT+6`); one day definition for the whole pipeline |
| "Today" during 00:00–00:59 CDT | **Clock-based** (`now.date()`), unchanged — the prior day's final live hour stays unserved (option A). Not a regression: current code doesn't serve it either. |

## Mechanism

The change is deliberately localized. Every window comparison in the codebase
already does `t.astimezone(TZ)` and compares against `local_day_bounds`' return
values; Python compares tz-aware datetimes by absolute instant, so expressing the
bounds in a different zone is correct without touching the comparisons.
Hour-of-day checks (`t.hour`, diurnal windows) intentionally stay in
`America/Chicago` — they encode when the dawn low / afternoon peak occur, which is
a wall-clock fact, not a settlement-day fact.

### 3a. `local_day_bounds` builds in LST (`config.py` + `settlement.py`)

- `config.py`: add `CLIMATE_TZ = "Etc/GMT+6"` with a comment: the NWS
  Climatological (CLI) day for DFW is fixed UTC−6 (Local Standard Time)
  year-round — this is the settlement-day boundary, distinct from `TIMEZONE`
  (`America/Chicago`), which stays the wall-clock/diurnal zone for hour-of-day
  logic and all display.
- `settlement.py`: a module-level `_CLIMATE_TZ = ZoneInfo(CLIMATE_TZ)`;
  `local_day_bounds` builds `start`/`end` in `_CLIMATE_TZ` instead of `TZ`.
  Everything else in the function is unchanged. Because `Etc/GMT+6` has no DST,
  every settlement day is exactly 24h (a minor correctness bonus over the current
  code, which yields 23h/25h windows on the two America/Chicago transition days).

### 3b. Front-guard scan must include the post-midnight tail (`model.py`)

`_member_extreme`'s locked-low front scan currently keeps only readings with
clock `t.hour >= FRONT_SCAN_FROM_HOUR` (12). Under the LST window the settlement
day's final hour is 00:00–00:59 of the *next* clock day (`t.hour == 0`), which
this filter silently drops — defeating the guard's purpose exactly on the
post-midnight-front nights it exists for.

Fix: scan by absolute time from local noon of the settlement day's primary date,
not by raw clock hour. `_member_extreme` already computes
`start, end = local_day_bounds(day)`; derive
`noon = start.astimezone(TZ).replace(hour=FRONT_SCAN_FROM_HOUR, minute=0,
second=0, microsecond=0)` and filter `remaining` to `t >= noon`. This includes
the post-midnight tail in summer and is byte-identical in winter (window start is
clock midnight, so noon is `start + 12h` either way). `FRONT_SCAN_FROM_HOUR`
stays the tunable noon anchor.

### 3c. `covers_extreme` — re-examined, no change

`_LOW_WINDOW = (0, 9)` / `_HIGH_WINDOW = (12, 18)` check clock `t.hour` on
readings already filtered to `[start, end)`. The dawn low still lands ~5–7 AM
clock (in `[0, 9]`); the afternoon peak still ~12–18 clock. The post-midnight
tail (clock hour 0) reads as low-window coverage, which is correct (it is an
overnight reading). No change; the subtle widening is documented in a comment.

### 3d. Overnight cooling window shifts +1h in summer — self-consistent

`_overnight_mean` measures `hours = (t - start) / 3600` (hours since window
start) against `NIGHT_WINDOW_HOURS = (0, 8)`. With the LST start (01:00 CDT in
summer) the window becomes 01:00–09:00 CDT instead of 00:00–08:00 CDT. It already
uses `start.tzinfo`, so it follows automatically. This is a conscious, accepted
shift: `_cooling_offset` in `calibration.py` measures the offset over the same
window it is later applied to, so it stays self-consistent. Winter: unchanged.

### 3e. "Today", lead buckets, snapshot — unchanged

`snapshot()` keeps `today = now.date()`, `tomorrow = today + 1`;
`lead_bucket(now, day)` keeps its clock-date arithmetic. During 00:00–00:59 CDT
the model predicts the new calendar day as a pure forecast (`now` precedes that
day's LST window start, so `is_today` is False and no obs are in-window), and
does not serve the still-open prior day — identical to today's behavior. No
ripple into dashboard labels, ticker↔date mapping, or `forecast_log` target
dates.

### 3f. Live obs fetch coverage — unchanged, over-covers

`nws_observations.fetch` bounds its API pull at clock midnight of `now`'s day,
which is *earlier* than the LST window start (01:00 CDT), so the settlement
window is fully covered; the window filter trims the extra. The `limit=500`
sub-hourly cap (~40h) comfortably spans a 24h settlement day. No change.

## What this fixes / does NOT fix (explicit)

Fixes:
- **Morning over-coverage:** the model stops ingesting 00:00–00:59 CDT readings
  that belong to *yesterday's* settlement day into today's observed extremes and
  hard bound.
- **Post-midnight-front anticipation:** the forecast window for today's low now
  extends to the true settlement end (01:00 CDT next day), so the front guard and
  member extremes see a forecast front in that final hour and pull today's
  predicted low down — the forecast-level defense against the May 26 pattern.
- **Backtest / per-source-extreme consistency:** `day_high_low`, `covers_extreme`,
  and `observed_so_far` slice on the settlement day, matching `settlements.jsonl`.
- **DST-transition days** become exactly-24h windows.

Does NOT fix (accepted, option-A boundary):
- Serving the prior day's market during its final live hour (00:00–00:59 CDT),
  i.e. reacting to the *actual observed* post-midnight reading in real time. That
  needs climate-date "today" (option B) and is out of scope; the forecast-level
  anticipation above covers the predictable case.

## Interactions verified

- **Comparisons:** every `[start, end)` check is instant-based (tz-aware
  comparison), correct across the zone change without edits.
- **Convective scan** (`_window_max`, `[now, end)`): `end` is the window end, so
  it auto-extends to the true settlement end. No change.
- **Hourly (Robinhood) basis:** also moves to LST. The live site is CLI-only
  (Robinhood page retired), so the hourly cohort is backtest/history-only; whether
  WU's daily summary uses the exact LST convention is unverified but low-risk. Flag
  to re-verify if the Robinhood page is ever revived. (Out of scope to gate on.)
- **Settlement offset:** `_settlement_offset`/`_conditional_settlement_offset`
  compare CLI (`daily.py`) vs hourly (`fetch_actual`, which uses
  `local_day_bounds`) daily extremes. Both operands now slice on the LST window,
  removing boundary-day cross-window noise; the 45-day rolling recalibration
  absorbs the redefinition automatically.

## Testing

1. **Winter byte-identity:** for any date in CST (e.g. January), `local_day_bounds`
   returns the same absolute window as before. A test asserts the new bounds equal
   the old `America/Chicago` construction on a winter date, and differ by exactly
   1h on a summer date.
2. **Front-scan tail:** a locked-low front where the only undercutting forecast
   reading is at 00:30 CDT the next clock day → the guard fires (old clock-hour
   filter would have dropped it); a pre-noon dip still cannot trigger; winter date
   byte-identical.
3. **`day_high_low` on May 26, 2026:** from the real 5-min obs, the LST-window
   minimum equals the CLIDFW value (67), where the clock window gave a different
   value — the settlement-correctness gate. (Use archived obs; may be a
   benchmark-style replay rather than a unit test if it needs network.)
4. **Fixture re-basing:** existing behavior tests that build July-dated obs series
   starting at clock hour 0 (e.g. `test_warm_overnight_does_not_false_lock_high`,
   the low-lock and nowcast tests) now have their hour-0 readings fall outside the
   LST window. Each must be preserved in *intent* — re-based by moving fixtures to
   clock hour ≥ 1, pinning to a winter date where the windows coincide, or building
   fixtures in the LST zone. The plan enumerates each affected test individually;
   no test is weakened or deleted to make it pass.
5. **May 26 pipeline replay** (benchmark, like the front-guard replay): confirm the
   model's computed low for May 26 now matches CLI, and a winter control day is
   unchanged, with artifacts in `docs/benchmarks/<date>/lst-window/`.

## Out of scope

- Option B (climate-date "today" / serving the prior day's final live hour) and
  the 12:00–12:59 AM near-certain last-hour trade — a separate feature.
- Re-verifying Robinhood/WU's exact settlement convention.
- Any change to the front guard's trigger margin or the convective floor.
