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


def _fetch_series(start: date, end: date) -> tuple[list[datetime], list[float]]:
    params = {
        "station": "DFW", "network": "TX_ASOS", "data": "tmpf",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
        "tz": TIMEZONE, "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T",
    }
    text = get_text(URL, params)
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
    hourly settlement basis (same as live obs)."""
    times, temps = to_hourly(*_fetch_series(start, end))
    out: dict[date, tuple[float, float]] = {}
    day = start
    from datetime import timedelta
    while day <= end:
        hi, lo = day_high_low(times, temps, day)
        if hi is not None:
            out[day] = (hi, lo)
        day += timedelta(days=1)
    return out
