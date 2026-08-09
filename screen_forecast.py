"""Reduce an NWS hourly forecast to a climate day's high and low.

NWS hourly temperatures already arrive in Fahrenheit, so no conversion happens
here -- only windowing onto the right day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_MONTHS = {m.upper(): i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def climate_day_of_ticker(ticker: str):
    """The settlement day a Kalshi ticker refers to, from its middle segment.

    Parsed from the ticker rather than derived from close_time on purpose:
    KXHIGHAUS-26AUG04-T97 closes at 2026-08-05T05:59Z, which is Aug 5 in the
    city's LOCAL time and only resolves to Aug 4 in fixed standard time. The
    ticker states the day outright, so use it."""
    parts = (ticker or "").split("-")
    if len(parts) < 2 or len(parts[1]) != 7:
        return None
    stamp = parts[1].upper()
    try:
        year = 2000 + int(stamp[:2])
        month = _MONTHS[stamp[2:5]]
        day = int(stamp[5:])
        return date(year, month, day)
    except (ValueError, KeyError):
        return None


def lst_offset_hours(tzname: str) -> int:
    """UTC offset of the zone's STANDARD time, in whole hours.

    Taken in January, when no US zone is on daylight time. The climate day is a
    fixed-LST window; using the current local offset would shift the day
    boundary by an hour for half the year."""
    january = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(tzname))
    return int(january.utcoffset().total_seconds() // 3600)


def in_progress_day(now: datetime, tzname: str) -> date:
    """The climate day running right now in this city.

    Fixed LST, not local time: the climate day ends at 01:00 local during
    daylight saving, so the local date is a day ahead for that hour.

    Lives here rather than in screen_alert because the Screen page needs the
    same answer -- the page's red highlight means "the alert pushed this", and
    two copies of the day boundary would silently drift apart."""
    offset = lst_offset_hours(tzname)
    return (now.astimezone(timezone.utc) + timedelta(hours=offset)).date()


def _day_periods(periods: list, day: date, tzname: str) -> list:
    """[(start, period)] for the periods falling in `day`'s LST climate window,
    in forecast order. `start` is the period's own aware UTC-offset time, so a
    caller can still compare it against `now`."""
    offset = timedelta(hours=lst_offset_hours(tzname))
    out = []
    for p in periods or []:
        try:
            start = datetime.fromisoformat(str(p.get("startTime")))
        except (TypeError, ValueError):
            continue
        utc_offset = start.utcoffset()
        if utc_offset is None:
            continue
        # Shift into fixed standard time, then take the calendar date.
        if (start - utc_offset + offset).date() == day:
            out.append((start, p))
    return out


def forecast_at(periods: list, when: datetime):
    """The hourly forecast temperature linearly interpolated to `when`.

    Interpolated rather than snapped to the last whole hour: snapping makes the
    value a step function that jumps at the top of each hour while the
    observation it is compared against has not yet updated, which is the
    sawtooth `model.py` documents for the KDFW anchor.

    Flat outside the payload's range. NWS hourly carries exactly one past hour
    (verified 2026-08-06 at FFC, MTR and OKX), so a stale anchor at a
    slow-reporting station can sit before the first period; extrapolating that
    last ~hour flat beats abstaining. None when no period is usable."""
    points = []
    for p in periods or []:
        temp = p.get("temperature")
        if temp is None:
            continue
        try:
            start = datetime.fromisoformat(str(p.get("startTime")))
        except (TypeError, ValueError):
            continue
        if start.utcoffset() is None:
            continue
        points.append((start, float(temp)))
    if not points:
        return None
    points.sort(key=lambda pair: pair[0])
    before = [pt for pt in points if pt[0] <= when]
    after = [pt for pt in points if pt[0] > when]
    if not before:
        return after[0][1]
    if not after:
        return before[-1][1]
    (t0, v0), (t1, v1) = before[-1], after[0]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return v0
    frac = (when - t0).total_seconds() / span
    return round(v0 + (v1 - v0) * frac, 2)


# How far back the drift anchor averages, and how stale a reading may be before
# it says nothing about now. Station cadence is NOT uniform -- measured
# 2026-08-06: 5 min at KATL/KSFO/KMIA/KLAS, 31 min at KNYC, 60 min at KDEN, with
# newest readings up to 67 min old. A fixed "last N readings" anchor (the KDFW
# rule, written for a 5-minute feed) would silently span four hours at Denver.
ANCHOR_WINDOW_MIN = 30
MAX_ANCHOR_AGE_MIN = 70


def observed_anchor(readings: list, now: datetime) -> tuple:
    """(temperature, timestamp) for the station's current reading, or
    (None, None).

    The mean of the readings inside ANCHOR_WINDOW_MIN, paired with the mean of
    their own timestamps. Averaging rather than taking the newest reading damps
    the whole-degC jitter that would otherwise swing the implied reference while
    the temperature is flat.

    A station too slow to put anything in the window falls back to its single
    newest reading, provided it is inside MAX_ANCHOR_AGE_MIN. Pairing the value
    with the time it was actually taken is what lets the caller compare it
    against the forecast for THAT hour rather than for now."""
    usable = [(t, v) for t, v in (readings or [])
              if t is not None and v is not None and t <= now]
    if not usable:
        return (None, None)
    window = [(t, v) for t, v in usable
              if (now - t).total_seconds() <= ANCHOR_WINDOW_MIN * 60]
    if not window:
        newest = max(usable, key=lambda pair: pair[0])
        if (now - newest[0]).total_seconds() > MAX_ANCHOR_AGE_MIN * 60:
            return (None, None)
        window = [newest]
    temp = sum(float(v) for _, v in window) / len(window)
    base = min(t for t, _ in window)
    mean_offset = sum((t - base).total_seconds() for t, _ in window) / len(window)
    return (round(temp, 2), base + timedelta(seconds=mean_offset))


def forecast_drift(periods: list, readings: list, now: datetime):
    """How wrong the hourly forecast currently is at this station, in F.

    Positive: the station is warmer than the forecast said, i.e. the forecast is
    running cold. Negative: it is running hot.

    Measured against the forecast interpolated to the ANCHOR'S OWN timestamp,
    not to `now`. Station cadence is not uniform, and comparing an hour-old
    Denver reading against the forecast for now would manufacture drift out of
    the diurnal ramp; against what the forecast said for that hour it is apples
    to apples, and staleness costs recency rather than correctness."""
    anchor, at = observed_anchor(readings, now)
    if anchor is None:
        return None
    expected = forecast_at(periods, at)
    if expected is None:
        return None
    return round(anchor - expected, 2)


def daily_extremes(periods: list, day: date, tzname: str) -> dict:
    """{'high': f, 'low': f} over the LST climate day, or Nones when absent."""
    temps = [float(p["temperature"])
             for _, p in _day_periods(periods, day, tzname)
             if p.get("temperature") is not None]
    if not temps:
        return {"high": None, "low": None}
    return {"high": max(temps), "low": min(temps)}


def _pop(period: dict) -> int:
    """A period's precipitation probability, treating an absent one as zero."""
    value = (period.get("probabilityOfPrecipitation") or {}).get("value")
    return 0 if value is None else int(value)


def _still_open(day_periods: list, variable: str) -> list:
    """The periods in which this variable's extreme can still MOVE.

    Asymmetric, and deliberately not mirrored. A high is finished at its peak:
    no later storm can raise it, so counting an evening thunderstorm against an
    afternoon high row is noise. A low is not — evening convection can crash it
    any time before midnight, which is the whole reason convective.py exists —
    so its window runs to the end of the climate day. The same asymmetry the
    peak-lock guard already records."""
    if variable != "high":
        return day_periods
    temps = [(i, p.get("temperature")) for i, (_, p) in enumerate(day_periods)
             if p.get("temperature") is not None]
    if not temps:
        return day_periods
    peak = max(temps, key=lambda pair: float(pair[1]))[0]
    return day_periods[:peak + 1]


def storm_chance(periods: list, day: date, tzname: str, variable: str, now):
    """Whole-percent chance of THUNDERSTORMS over the hours that can still move
    this variable's extreme, or None when there are no such hours.

    Thunder-only on purpose: it is the convective downdraft that crashes a high
    or props up a low, while a steady overcast drizzle at the same POP does
    neither. 0 and None mean different things — 0 is a live window with clean
    hours in it, None is no window left at all."""
    # The window is cut on the WHOLE day first, then narrowed to what is still
    # ahead. Cutting the remaining hours instead would re-peak on whatever is
    # left, so a high at 9pm would keep reporting the evening's storms long
    # after the peak that settled it had passed.
    day_periods = _day_periods(periods, day, tzname)
    window = [(start, p) for start, p in _still_open(day_periods, variable)
              if start >= now]
    if not window:
        return None
    return max((_pop(p) for _, p in window
                if "thunder" in str(p.get("shortForecast") or "").lower()),
               default=0)


def fold_realized(forecast_value, realized_temps: list, variable: str):
    """The day's extreme over BOTH what happened and what is still forecast.

    For a climate day already in progress the remaining hourly periods no longer
    contain an extreme that has already occurred, so a forecast-only reduction is
    badly wrong: on 2026-08-03 Oklahoma City's low of 65 had passed, leaving the
    forecast-only "low" as that evening's 82. The settled extreme is the extreme
    of the realized readings and the remaining forecast together."""
    values = [float(t) for t in (realized_temps or []) if t is not None]
    if forecast_value is not None:
        values.append(float(forecast_value))
    if not values:
        return None
    return min(values) if variable == "low" else max(values)
