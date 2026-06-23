"""Open-Meteo Ensemble API — the backbone of the probability distribution.

Each ensemble system expands into many members; every member is a full hourly
temperature curve and becomes one sample in the model. Columns look like
`temperature_2m_member01_ncep_gefs_seamless` (plus a control column without a
member number).
"""

from __future__ import annotations

from datetime import date, datetime

from config import ENSEMBLE_MODELS, LAT, LON, TIMEZONE
from sources.common import get_json, parse_local_times

URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


def _parse(data: dict) -> dict[str, tuple[list[datetime], list[float]]]:
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "ens_") if key != "temperature_2m" else "ens_control"
        out[label] = (times, values)
    return out


# Each system should expand into many members; a count of 1 means the API gave
# us only the control series (e.g. the gem_seamless trap) and the system's spread
# is silently missing. We warn rather than raise so a single degraded system can't
# take down the whole forecast.
_MIN_MEMBERS = 5


def _warn_if_thin(parsed: dict) -> dict:
    if 0 < len(parsed) < _MIN_MEMBERS:
        import warnings
        warnings.warn(
            f"ensemble fetch returned only {len(parsed)} member series for "
            f"{ENSEMBLE_MODELS} — a system may be delivering just its control "
            "series (check model ids).", stacklevel=2)
    return parsed


def fetch(forecast_days: int = 2) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {member_label: (times, temps_f)} across all ensemble systems."""
    data = get_json(URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(ENSEMBLE_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _warn_if_thin(_parse(data))


def fetch_historical(start: date, end: date,
                     ttl: int = 24 * 3600) -> dict[str, tuple[list[datetime], list[float]]]:
    """Archived ensemble members over [start, end] for skill weighting."""
    data = get_json(URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(ENSEMBLE_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }, ttl=ttl)
    return _parse(data)
