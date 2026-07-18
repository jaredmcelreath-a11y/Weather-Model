"""Probe candidate Open-Meteo models for live availability + archive depth at KDFW.

Run manually. Prints a table and is the source of truth for which candidate IDs
ship into config.CANDIDATE_* in Task 2. Not a unit test — it hits the network.
"""
from __future__ import annotations

from datetime import date, timedelta

from config import LAT, LON, TIMEZONE
from sources.common import get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

DET_CANDIDATES = [
    "ecmwf_aifs025_single",   # ECMWF AIFS (AI)
    "gfs_graphcast025",       # GraphCast (AI)
    "ukmo_seamless",          # UK Met Office
    "jma_seamless",           # JMA
    "meteofrance_seamless",   # Meteo-France ARPEGE/AROME
]
ENS_CANDIDATES = [
    "ukmo_global_ensemble_20km",
    "bom_access_global_ensemble",
]


def _count_temp_cols(data: dict) -> int:
    hourly = (data or {}).get("hourly", {})
    return sum(1 for k in hourly if k.startswith("temperature_2m"))


def _nonnull_frac(data: dict) -> float:
    hourly = (data or {}).get("hourly", {})
    cols = [v for k, v in hourly.items() if k.startswith("temperature_2m")]
    total = sum(len(c) for c in cols) or 1
    good = sum(1 for c in cols for x in c if x is not None)
    return good / total


def probe_live(url: str, model: str) -> dict:
    try:
        data = get_json(url, {
            "latitude": LAT, "longitude": LON,
            "hourly": "temperature_2m", "models": model,
            "temperature_unit": "fahrenheit", "timezone": TIMEZONE,
            "forecast_days": 2,
        }, ttl=0)
        return {"ok": _count_temp_cols(data) > 0,
                "cols": _count_temp_cols(data),
                "nonnull": round(_nonnull_frac(data), 2)}
    except Exception as e:  # noqa: BLE001 - probe reports failures, never raises
        return {"ok": False, "error": type(e).__name__}


def probe_archive(model: str, days: int = 45) -> dict:
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=days)
    try:
        data = get_json(HIST_URL, {
            "latitude": LAT, "longitude": LON,
            "hourly": "temperature_2m", "models": model,
            "temperature_unit": "fahrenheit", "timezone": TIMEZONE,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        }, ttl=0)
        hourly = (data or {}).get("hourly", {})
        n = len(hourly.get("time", []))
        return {"archive_hours": n, "nonnull": round(_nonnull_frac(data), 2)}
    except Exception as e:  # noqa: BLE001
        return {"archive_hours": 0, "error": type(e).__name__}


def main() -> None:
    print("=== DETERMINISTIC candidates (forecast API) ===")
    for m in DET_CANDIDATES:
        print(m, probe_live(FORECAST_URL, m), probe_archive(m))
    print("\n=== ENSEMBLE candidates (ensemble API) ===")
    for m in ENS_CANDIDATES:
        print(m, probe_live(ENSEMBLE_URL, m))


if __name__ == "__main__":
    main()
