"""IEM MOS / LAMP statistical guidance — post-processed, station-specific temps.

The raw NWP models (GFS/ECMWF/ICON/GEM and their ensembles) are dynamical; this
source adds *statistical* guidance that has been calibrated to the station's own
history, which is exactly what beats raw model output at the 0-48h range:

  - LAV (GFS-LAMP): hourly, frequently updated, out to ~25-38h. The gold standard
    for an airport site's same-day high/low.
  - NBS (NBM short): ~3-hourly out to ~72h, so it reaches tomorrow's afternoon
    high where LAMP's horizon stops.

Pulled from the Iowa Environmental Mesonet MOS API as hourly (times, temps_f)
series, one per model, shaped like every other source. Live-only for now: like
the NWS forecast it has no Open-Meteo-style bulk archive wired into calibration,
so it folds into the consensus uncorrected (group 'guidance', bias 0) at the
average single-estimator weight. Skill-weighting it via the IEM run archive
(the API takes a `runtime`) is the natural follow-up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import STATION_ID, TIMEZONE
from sources.common import get_json

URL = "https://mesonet.agron.iastate.edu/api/1/mos.json"
TZ = ZoneInfo(TIMEZONE)

# Models to pull, in label order. LAMP for hourly same-day precision; NBM-short
# for the extended reach LAMP doesn't have.
MODELS = ["LAV", "NBS"]


def _parse(data: dict) -> tuple[list[datetime], list[float]]:
    """IEM mos.json payload -> (tz-aware local times, temps °F).

    Rows carry `ftime_utc` (UTC, no offset suffix) and `tmp` in °F. Rows missing
    either are skipped. Times are returned in the station timezone so the daily
    high/low windowing matches every other source.
    """
    times: list[datetime] = []
    temps: list[float] = []
    for row in data.get("data", []):
        ft = row.get("ftime_utc")
        tmp = row.get("tmp")
        if ft is None or tmp is None:
            continue
        # Truncate any fractional seconds so this parses on Python 3.9.
        dt = datetime.fromisoformat(ft[:19]).replace(tzinfo=timezone.utc)
        times.append(dt.astimezone(TZ))
        temps.append(float(tmp))
    return times, temps


def fetch(forecast_days: int = 2) -> dict[str, tuple[list[datetime], list[float]]]:
    """Live guidance, {f'mos_{model}': (times, temps_f)} for each MODEL.

    Omitting `runtime` returns the latest available run. A model that fails or
    returns nothing is skipped, so one degraded product can't take down the
    forecast (`forecast_days` is accepted for signature parity; the API returns
    its full native horizon).
    """
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for m in MODELS:
        try:
            data = get_json(URL, {"station": STATION_ID, "model": m})
        except Exception:
            continue
        times, temps = _parse(data)
        if times:
            out[f"mos_{m.lower()}"] = (times, temps)
    return out


def historical_extremes(start, end, ttl: int = 24 * 3600):
    """{target_day: {'mos_lav'/'mos_nbs': (high, low)}} from each day's
    prior-day 12Z run — a genuine ~24-38h day-ahead lead.

    Each target day is forecast from a DIFFERENT run (the 12Z cycle issued the
    day before), so unlike the NWP fetchers this returns per-day extremes rather
    than one continuous series. A model whose run is missing/short for a day is
    omitted; a day with no usable model is omitted entirely. Best-effort: any
    per-call failure skips that model, never raises.
    """
    from settlement import day_high_low  # local import: avoid load-time cycle
    out: dict = {}
    day = start
    while day <= end:
        runtime = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                   - timedelta(days=1)).replace(hour=12).strftime("%Y-%m-%dT%H:%MZ")
        systems: dict = {}
        for m in MODELS:
            try:
                data = get_json(URL, {"station": STATION_ID, "model": m,
                                      "runtime": runtime}, ttl=ttl)
            except Exception:
                continue
            times, temps = _parse(data)
            if not times:
                continue
            hi, lo = day_high_low(times, temps, day)
            if hi is not None or lo is not None:
                systems[f"mos_{m.lower()}"] = (hi, lo)
        if systems:
            out[day] = systems
        day += timedelta(days=1)
    return out
