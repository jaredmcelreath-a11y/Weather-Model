"""A free 5-model consensus temperature for every screened city.

The Screen page measures every gap from the NWS point forecast alone. This is
the independent second opinion: the same five deterministic models the KDFW
model already trusts, equal-weighted, with their spread as the honest statement
of how much they agree.

DISPLAY ONLY. Nothing here gates a flag, blends into Ref, or is imported by
screen_alert -- so screen_score's settled track record stays comparable across
this change.

Equal weight, deliberately. Skill weights at KDFW came from months of
self-scoring AT THAT STATION; nothing equivalent exists for Denver or Miami, and
inventing one would repeat the 2026-07-17 season-readiness bug where a number
derived from no data printed 0% and produced a live "BUY NO +85". The log this
module writes is how that can change on evidence instead of taste.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import screen_forecast

# Below this many contributing models there is no consensus worth printing: two
# models agreeing is not agreement, and the spread of two is a range.
MIN_MODELS = 3

# Past this the published document is too old to show. The globals refresh every
# 6 hours and HRRR hourly, so a document this stale means the Action has been
# failing, not that the models have not moved.
STALE_AFTER_HOURS = 6

_PREFIX = "temperature_2m_"


def _lst_date(unix_ts, offset_hours: int) -> date:
    """The fixed-LST calendar date this instant falls on."""
    return (datetime.fromtimestamp(int(unix_ts), timezone.utc)
            + timedelta(hours=offset_hours)).date()


def series_extreme(times: list, temps: list, day: date,
                   offset_hours: int) -> dict:
    """{'high': f, 'low': f} for one model over one LST climate day.

    Mirrors screen_forecast.daily_extremes, which does the same reduction for
    NWS periods -- same day boundary, same Nones-when-absent contract."""
    values = [float(t) for stamp, t in zip(times or [], temps or [])
              if t is not None and _lst_date(stamp, offset_hours) == day]
    if not values:
        return {"high": None, "low": None}
    return {"high": max(values), "low": min(values)}


def model_extremes(hourly: dict, day: date, offset_hours: int) -> dict:
    """{model: {'high': f, 'low': f}} for every model with data on `day`.

    A model whose series is entirely null on this day is ABSENT rather than
    present with Nones -- routine for HRRR, which does not reach tomorrow."""
    times = (hourly or {}).get("time") or []
    out = {}
    for key, temps in (hourly or {}).items():
        if not key.startswith(_PREFIX):
            continue
        got = series_extreme(times, temps, day, offset_hours)
        if got["high"] is not None:
            out[key[len(_PREFIX):]] = got
    return out


def consensus(values: list):
    """{'value','spread','n'} over these model extremes, or None.

    Plain mean and full range. Rounded to a tenth because the inputs are
    tenths and a consensus printed to more places than its members would claim
    precision the models do not have."""
    numbers = [float(v) for v in (values or []) if v is not None]
    if len(numbers) < MIN_MODELS:
        return None
    return {"value": round(sum(numbers) / len(numbers), 1),
            "spread": round(max(numbers) - min(numbers), 1),
            "n": len(numbers)}
