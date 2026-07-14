"""The single source of truth for *what the market settles on*.

A contract's outcome is the official daily high/low at KDFW within the NWS
climate day (fixed Local Standard Time, UTC−6) -- not clock midnight to
midnight -- rounded to a whole degree Fahrenheit. Every part of the model
that needs "the high/low for day D" goes through here so the definition
stays consistent.

The LST window was verified 2026-07-14
(docs/benchmarks/2026-07-14/climate-day/FINDINGS.md). The settlement day in
summer runs 01:00 CDT → 01:00 CDT the next clock day; in winter (CST) it
coincides with clock midnight to midnight.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import BIN_HIGH, BIN_LOW, CLIMATE_TZ, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
_CLIMATE_TZ = ZoneInfo(CLIMATE_TZ)


def round_half_up(x: float) -> int:
    """Round to the nearest whole degree, .5 going up — matching the NWS /
    Weather Underground convention (Python's built-in round() uses banker's
    rounding, which would send 90.5 -> 90 instead of 91)."""
    return math.floor(x + 0.5)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of the settlement (NWS climate) day, as tz-aware datetimes.

    Built in fixed Local Standard Time (CLIMATE_TZ, UTC−6) — the CLIDFW climate
    day Kalshi settles on — NOT clock time: in summer this window is 01:00 CDT →
    01:00 CDT, one hour after clock midnight. Comparisons elsewhere convert obs
    to America/Chicago and compare against these bounds by absolute instant, so
    the zone difference is transparent to them; only the day *boundary* moves.
    Fixed UTC−6 means every settlement day is exactly 24h (no DST 23h/25h days).
    """
    start = datetime(day.year, day.month, day.day, tzinfo=_CLIMATE_TZ)
    end = start + timedelta(days=1)
    return start, end


def _within_day(times: list[datetime], temps: list[float], day: date,
                upto: datetime | None = None) -> list[float]:
    """Temps whose timestamp falls in [day_start, day_end) (and <= upto)."""
    start, end = local_day_bounds(day)
    out = []
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if t < start or t >= end:
            continue
        if upto is not None and t > upto:
            continue
        out.append(v)
    return out


def day_high_low(times: list[datetime], temps: list[float],
                 day: date) -> tuple[float | None, float | None]:
    """Official-style (rounded) high and low for `day` from an hourly series.

    Returns (None, None) if the series has no points inside the day window.
    """
    vals = _within_day(times, temps, day)
    if not vals:
        return None, None
    return round_half_up(max(vals)), round_half_up(min(vals))


# Local-hour windows in which each daily extreme actually occurs. A source must
# have at least one in-window point to legitimately define that extreme; a
# now-forward forecast (NWS / LAMP / NBM) that starts after the window has
# already passed only sees the tail of the day and would otherwise report a
# spurious extreme (e.g. an afternoon minimum as the "low").
_LOW_WINDOW = (0, 9)    # overnight / sunrise
_HIGH_WINDOW = (12, 18)  # mid-afternoon peak


def covers_extreme(times: list[datetime], temps: list[float], day: date,
                   variable: str) -> bool:
    """Whether `times` covers the window in which `day`'s high/low occurs.

    True only if at least one non-null sample falls inside the variable's
    occurrence window for `day`. Lets a now-forward source abstain from an
    extreme it never observed instead of reporting the wrong tail value.

    Under the LST settlement window, `day`'s final hour is clock 00:00-00:59
    of the next clock day (the post-midnight tail in summer). A reading there
    has clock hour 0, which falls inside `_LOW_WINDOW` (0, 9) -- it correctly
    counts as low-window coverage (it's an overnight reading), so no source
    wrongly abstains on it.
    """
    lo_h, hi_h = _HIGH_WINDOW if variable == "high" else _LOW_WINDOW
    start, end = local_day_bounds(day)
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if start <= t < end and lo_h <= t.hour <= hi_h:
            return True
    return False


def observed_so_far(times: list[datetime], temps: list[float], day: date,
                    now: datetime) -> tuple[float | None, float | None]:
    """Max/min actually observed so far today (unrounded — these are hard
    floors/ceilings used by the nowcast blend, not yet the settlement value)."""
    vals = _within_day(times, temps, day, upto=now)
    if not vals:
        return None, None
    return max(vals), min(vals)


def _corroborated_extreme(vals: list[float], which: str,
                          tol: float, min_support: int) -> float:
    """Most extreme value supported by >= `min_support` readings within `tol`,
    rejecting a lone sensor spike. Falls back to the raw extreme if nothing
    clears the support threshold (too few readings to corroborate)."""
    ordered = sorted(vals, reverse=(which == "max"))
    for v in ordered:
        support = (sum(1 for x in vals if x >= v - tol) if which == "max"
                   else sum(1 for x in vals if x <= v + tol))
        if support >= min_support:
            return v
    return ordered[0]


def observed_so_far_robust(times: list[datetime], temps: list[float], day: date,
                           now: datetime, tol: float = 0.7,
                           min_support: int = 2
                           ) -> tuple[float | None, float | None]:
    """Like `observed_so_far`, but for the sub-hourly continuous feed, which
    occasionally reports a single reading a whole °C off the real value. An
    extreme is only trusted when corroborated by >= `min_support` readings within
    `tol`°F — a genuine peak/trough persists across several 5-min samples, a
    sensor spike stands alone. `tol`=0.7°F is under one °C step, so it never
    merges adjacent real °C levels."""
    vals = _within_day(times, temps, day, upto=now)
    if not vals:
        return None, None
    return (_corroborated_extreme(vals, "max", tol, min_support),
            _corroborated_extreme(vals, "min", tol, min_support))


def bin_for_temp(temp: float) -> str:
    """Label of the bin a (continuous) temperature settles into after rounding."""
    t = round_half_up(temp)
    if t <= BIN_LOW:
        return f"<= {BIN_LOW}"
    if t >= BIN_HIGH:
        return f">= {BIN_HIGH}"
    return str(t)
