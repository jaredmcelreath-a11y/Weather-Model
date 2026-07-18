"""Historical *actual* KDFW observations from the Iowa Environmental Mesonet
(IEM) ASOS archive — the ground truth for calibration and backtesting.

The live NWS observations endpoint only retains about a week; IEM keeps the full
archive. We pull 5-minute ASOS temps (most slots are 'M'/missing because routine
reports are hourly) and reduce to a daily high/low in the station timezone.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from config import TIMEZONE
from settlement import day_high_low
from sources.common import get_text, to_hourly
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DAILY_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"


def _fetch_series(start: date, end: date,
                  ttl: int | None = None) -> tuple[list[datetime], list[float]]:
    """`ttl` None keeps get_text's long archive TTL (immutable past days —
    calibration/backtest). Live callers fetching TODAY's still-growing series
    (the NWS-outage fallback in nws_observations) pass a short ttl."""
    params = {
        "station": "DFW", "network": "TX_ASOS", "data": "tmpf",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
        "tz": TIMEZONE, "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T",
    }
    text = get_text(URL, params) if ttl is None else get_text(URL, params, ttl=ttl)
    times, temps = [], []
    for row in csv.DictReader(io.StringIO(text)):
        raw = row.get("tmpf", "M")
        if raw in ("M", "T", ""):
            continue
        try:
            temps.append(float(raw))
        except ValueError:
            continue
        times.append(datetime.fromisoformat(row["valid"]).replace(tzinfo=TZ))
    return times, temps


def fetch_actual(start: date, end: date) -> dict[date, tuple[float, float]]:
    """{day: (actual_high_f, actual_low_f)} for each day in [start, end].

    Resampled to hourly so the calibration/backtest ground truth matches the
    hourly settlement basis (same as live obs).

    IEM asos.py's `day2` param is exclusive, so a fetch bounded at `end` only
    returns rows through `end - 1 day` 23:59 clock — missing `end`'s LST
    settlement tail (00:00-00:59 the next clock day). Fetch one extra day of
    raw rows so every emitted day has its full LST window in view; the
    emission loop below still stops at `end` inclusive, so no extra day is
    emitted."""
    from datetime import timedelta
    times, temps = to_hourly(*_fetch_series(start, end + timedelta(days=1)))
    out: dict[date, tuple[float, float]] = {}
    day = start
    while day <= end:
        hi, lo = day_high_low(times, temps, day)
        if hi is not None:
            out[day] = (hi, lo)
        day += timedelta(days=1)
    return out


def _parse_daily(text: str) -> dict[date, tuple[float, float]]:
    """Parse the IEM daily-summary CSV into {day: (max_temp_f, min_temp_f)}.

    Rows with a missing/'None'/'M' max or min are skipped. This is the NWS-CLI
    settlement basis (continuous ASOS daily extremes) that Kalshi resolves on.
    """
    out: dict[date, tuple[float, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        hi, lo = row.get("max_temp_f"), row.get("min_temp_f")
        if hi in (None, "", "M", "None") or lo in (None, "", "M", "None"):
            continue
        try:
            out[date.fromisoformat(row["day"])] = (float(hi), float(lo))
        except (ValueError, KeyError):
            continue
    return out


def fetch_actual_cli(start: date, end: date,
                     ttl: int | None = None) -> dict[date, tuple[float, float]]:
    """{day: (cli_high_f, cli_low_f)} from the IEM daily summary for [start, end].

    The CLI daily max/min come from continuous (1-minute) ASOS data, so they can
    exceed the hourly METAR extremes that `fetch_actual` returns — this is the
    basis Kalshi settles on (vs Robinhood's hourly basis).

    `ttl` defaults to None, which leaves `get_text`'s own long archive TTL in
    place (calibration/backtest callers fetch immutable PAST days and rely on
    that). Live callers fetching TODAY's still-tightening summary should pass
    a short live-data ttl (e.g. CACHE_TTL_SECONDS) so it isn't frozen stale by
    the archive cache for a week."""
    params = {
        "network": "TX_ASOS", "stations": "DFW", "format": "comma",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
    }
    if ttl is None:
        return _parse_daily(get_text(DAILY_URL, params))
    return _parse_daily(get_text(DAILY_URL, params, ttl=ttl))
