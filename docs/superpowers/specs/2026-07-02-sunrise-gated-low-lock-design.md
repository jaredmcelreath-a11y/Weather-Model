# Sunrise-gated early low lock

**Date:** 2026-07-02
**Status:** approved, pending implementation
**Area:** `solar.py` (new), `model.py` (`_extreme_locked`), `config.py`, tests

## Problem

The daily **low** locks — switches from the forecast blend to the realized
minimum — only once `cur − running_min ≥ PEAK_LOCK_DROP (2.0°F)` in
`model._extreme_locked`. That 2°F-rise rule is a *lagging* proxy for "the low is
set."

On 2026-07-02 (verified against KDFW obs): the low bottomed at **78.8°F by
5:10am** and never went lower, sunrise was **~6:23am**, but the temp did not
climb 2°F above the min until **8:00am** — so the model showed its cold forecast
blend (77–78) for ~3 hours after the low was physically set, only jumping to 78.8
(=79) at 8am. This is the "mid-morning lock lag" half of that day's miss
(the other half — the cold forecast — is addressed by
[[2026-07-02-warm-night-low-bias-design]]).

The physical fact the current rule ignores: the daily minimum occurs right around
sunrise. Once past sunrise with temps rising, the min is behind us — no need to
wait for a full 2°F climb.

## Goal

Lock the low earlier in the morning using sunrise as the gate, without
false-locking on a pre-dawn wiggle. Low only; leave the high and everything
downstream unchanged.

## Non-goals / out of scope

- The high lock (its afternoon-peak 2°F drop is appropriate — steep evening
  cooling).
- Lowering `PEAK_LOCK_DROP` (kept at 2.0 as the fallback trigger).
- Fetching sunrise over the network (computed locally — the lock runs in the
  obs path and must stay network-free).
- Any change to the settlement basis, sigma logic, or the bias corrections.

## Design

### New module `solar.py`

A small, dependency-free module (peer of `convective.py`).

```
sunrise(day: date, lat: float = LAT, lon: float = LON,
        tz: ZoneInfo = TZ) -> datetime
```

Returns the local, tz-aware sunrise for `day` at `(lat, lon)`. Implements the
standard **sunrise equation** (Wikipedia/NOAA): Julian day → solar mean anomaly →
equation of center → ecliptic longitude → solar declination and transit → sunrise
hour angle at the refraction-corrected zenith **90.833°**, produced as a Julian
date and converted to UTC then to the target zone (so DST is handled by
`zoneinfo`). Longitude-west-positive convention internally (KDFW `LON = -97.04`).
Uses only `math` + `datetime`. On a polar day/night `acos` domain overflow the
hour angle is clamped, but at KDFW's latitude this never triggers.

Accuracy target: within ~3 minutes of published KDFW sunrise (e.g. 2026-07-02 ≈
06:23 CDT; a winter date ≈ 07:3x CST) — far finer than the lock needs.

### The lock rule — `model._extreme_locked`, low branch only

Current low branch:

```python
return (cur - min(vals)) >= drop
```

Becomes (whichever condition fires first):

```python
risen = cur - min(vals)
if risen >= drop:                      # existing 2F fallback, unchanged
    return True
# Early lock: past sunrise the dawn minimum is behind us; a small confirming
# rise (clears obs + rounding jitter) means we're off the trough, not sitting
# in it. The margin naturally waits for the true min even when it lands after
# sunrise, since temps are still falling toward it until then (risen <= 0).
try:
    sr = solar.sunrise(day)
except Exception:
    return False
return now.astimezone(TZ) >= sr and risen >= LOW_LOCK_RISE
```

The high branch is unchanged. `now` is guaranteed non-`None` here (the function
returns early when `now is None`); the `.astimezone(TZ)` keeps the comparison
tz-correct even if called with a differently-zoned `now`.

Rationale for the two parts:
- **Sunrise gate** blocks a pre-dawn non-monotonic wiggle (temp dips, ticks up
  >0.8°F, then drops to the real dawn low) from locking early and high. Before
  sunrise, nothing locks except the 2°F fallback.
- **Rise margin `LOW_LOCK_RISE = 0.8°F`** confirms we are genuinely off the
  trough. 0.8 clears the ~0.5°F sub-hourly obs + rounding jitter while still
  locking promptly. No separate sunrise buffer — the rise margin already defers
  to a min that lands shortly after sunrise.

### Config

Add `LOW_LOCK_RISE = 0.8`. `PEAK_LOCK_DROP` stays `2.0`.

### Interaction with the bias corrections

Locking earlier simply hands the day to the obs-anchored path sooner. All three
forecast corrections (flat bias, lead-time `by_lead`, and the new warm-night
`warm_low`) are pure-forecast-path only (`obs_now is None`); the moment the low
locks, `obs_now` is set and they stop applying, the realized continuous low
supersedes, and (today) the display shows 78.8→79 at ~6:55am instead of ~8:00am.
No double-counting, no conflict — the realized value wins, just earlier.

### Expected effect on 2026-07-02

Sunrise ~6:23am; running min 78.8 from 5:10am; temp reaches 80.6 at 6:55am →
`risen = 1.8 ≥ 0.8` and past sunrise → **early-lock at ~6:55am**, ~65 min before
the old 8:00am trip. Displayed low: 78.8 (=79) from 6:55am.

## Testing

**`solar.sunrise` (unit):**
- 2026-07-02 at `(LAT, LON)` returns ~06:23 CDT (assert within a ±5 min window,
  e.g. 06:18–06:28), tz-aware, `.date()` == the input day, UTC offset −5 (CDT).
- A winter date (e.g. 2026-01-15) returns a CST morning time (~07:2x–07:4x) with
  UTC offset −6 — confirms the DST/zone conversion.

**`model._extreme_locked` low (unit):**
- **Early-locks:** min at 6:00am (78.8), temps rise to 80.0 by 7:00am
  (`risen 1.2`), `now` = 7:00am (past sunrise) → `True`. The same series under the
  old rule (`risen 1.2 < 2.0`) would be `False` — this is the behavior change.
- **No pre-dawn false lock:** min at 3:00am, a wiggle up to +1.0°F at 4:00am,
  `now` = 4:00am (before sunrise) → `False` despite `risen ≥ 0.8`.
- **2°F fallback intact:** `risen ≥ 2.0` at any time → `True`.
- **High untouched:** an afternoon high with `risen ≥ 2.0` still `True`; the low
  early-lock path never affects the high branch (existing high lock tests in
  `tests/test_accuracy.py` must stay green).

**Regression (existing, must stay green):**
- `tests/test_cli_basis.py::test_locked_low_anchors_on_continuous_and_skips_widening`
  (now = 16:00 → 2°F fallback still locks).
- `tests/test_cli_basis.py::test_unlocked_low_still_widens_with_continuous`
  (now = 04:00, descending, pre-sunrise → still unlocked).

**Integration:**
- `predict_variable` over a synthetic today-morning obs series (min set pre-7am,
  temps rising, `now` ~7am) reports `peak_locked True` and `consensus` equal to
  the observed continuous low — where the pre-change model would still be unlocked
  and showing the forecast.

**Verification:** new tests red→green; full `pytest` green; spot-check
`solar.sunrise(date(2026,7,2))` prints ≈ 06:23 CDT.
