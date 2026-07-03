"""Local sunrise for a fixed station, computed from NOAA's general solar
position equations (fractional-year method). No network, no dependencies — the
lock path calls this on every render, so it must be pure and cheap. Accurate to
~1 minute, far finer than the low lock needs.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import LAT, LON, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def _solar_params(day: date) -> tuple[float, float]:
    """(equation of time in minutes, solar declination in radians) for `day`,
    from NOAA's fractional-year approximation."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365.0 * (n - 1)                       # fractional year (rad)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g)
                       - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                       - 0.040849 * math.sin(2 * g))         # minutes
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))   # radians
    return eqtime, decl


def _at_utc_minutes(day: date, minutes: float, tz: ZoneInfo) -> datetime:
    """UTC `minutes` past midnight on `day`, converted to `tz` (DST via zoneinfo)."""
    utc = datetime(day.year, day.month, day.day,
                   tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return utc.astimezone(tz)


def solar_noon(day: date, lon: float = LON, tz: ZoneInfo = TZ) -> datetime:
    """Local, tz-aware solar noon for `day` at `lon` (east-positive) — the moment
    the sun crosses the meridian, when the hour angle is zero."""
    eqtime, _ = _solar_params(day)
    return _at_utc_minutes(day, 720 - 4 * lon - eqtime, tz)


def sunrise(day: date, lat: float = LAT, lon: float = LON,
            tz: ZoneInfo = TZ) -> datetime:
    """Local, tz-aware sunrise for `day` at `(lat, lon)` (lon east-positive).

    Uses the refraction-corrected zenith of 90.833°. Computed in UTC and
    converted to `tz`, so DST is handled by zoneinfo. On a polar day/night the
    hour-angle cosine is clamped (never triggers at KDFW's latitude).
    """
    eqtime, decl = _solar_params(day)
    latr = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(latr) * math.cos(decl))
              - math.tan(latr) * math.tan(decl))
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))                    # sunrise hour angle (deg)
    return _at_utc_minutes(day, 720 - 4 * (lon + ha) - eqtime, tz)
