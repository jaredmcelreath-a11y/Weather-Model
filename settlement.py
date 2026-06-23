"""The single source of truth for *what the market settles on*.

A contract's outcome is the official daily high/low at KDFW within a local
midnight->midnight window, rounded to a whole degree Fahrenheit. Every part of
the model that needs "the high/low for day D" goes through here so the
definition stays consistent.

IMPORTANT (verify before trusting edge cases): this implements clock-time
midnight->midnight in America/Chicago, as the user described the market. The
NWS *climate day* uses local STANDARD time year-round, which can shift a
near-midnight low onto a different calendar day during DST. Confirm against one
actually-settled Kalshi/Robinhood KDFW market before relying on edge cases.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import BIN_HIGH, BIN_LOW, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def round_half_up(x: float) -> int:
    """Round to the nearest whole degree, .5 going up — matching the NWS /
    Weather Underground convention (Python's built-in round() uses banker's
    rounding, which would send 90.5 -> 90 instead of 91)."""
    return math.floor(x + 0.5)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of the local calendar day, as tz-aware datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
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


def bin_for_temp(temp: float) -> str:
    """Label of the bin a (continuous) temperature settles into after rounding."""
    t = round_half_up(temp)
    if t <= BIN_LOW:
        return f"<= {BIN_LOW}"
    if t >= BIN_HIGH:
        return f">= {BIN_HIGH}"
    return str(t)
