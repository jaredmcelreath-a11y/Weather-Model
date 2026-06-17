"""Open-Meteo Ensemble API — the backbone of the probability distribution.

Each ensemble system expands into many members; every member is a full hourly
temperature curve and becomes one sample in the model. Columns look like
`temperature_2m_member01_ncep_gefs_seamless` (plus a control column without a
member number).
"""

from __future__ import annotations

from datetime import datetime

from config import ENSEMBLE_MODELS, LAT, LON, TIMEZONE
from sources.common import get_json, parse_local_times

URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


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
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "ens_") if key != "temperature_2m" else "ens_control"
        out[label] = (times, values)
    return out
