"""Live KDFW observations (METAR) — the nowcasting engine.

These are what *actually happened*: the observed max-so-far is a hard floor on
today's high, the observed min-so-far a hard ceiling on today's low.
"""

from __future__ import annotations

from datetime import datetime

from config import STATION_ID
from sources.common import c_to_f, get_json, parse_local_times, to_hourly

OBS_URL = f"https://api.weather.gov/stations/{STATION_ID}/observations"


def fetch(limit: int = 200,
          continuous: bool = False) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {'obs': (times, temps_f)} sorted ascending in time.

    `limit` of ~200 METARs comfortably covers the last couple of days,
    including the overnight low.

    With `continuous=True`, also return `'obs_continuous'`: the full sub-hourly
    feed (5-minute readings, not just the routine :53 METAR) before the hourly
    reduction. The CLI/Kalshi basis uses it to catch a brief spike between routine
    reports; the default hourly basis (Robinhood) ignores it.
    """
    data = get_json(OBS_URL, {"limit": limit}, ttl=300)
    pairs = []
    for feature in data["features"]:
        props = feature["properties"]
        temp_c = props.get("temperature", {}).get("value")
        if temp_c is None:
            continue
        pairs.append((props["timestamp"], c_to_f(temp_c)))
    # API returns newest-first; normalize to ascending time.
    pairs.reverse()
    raw = (parse_local_times([p[0] for p in pairs]), [p[1] for p in pairs])
    # Settle on the routine hourly readings, not 5-minute spikes.
    out = {"obs": to_hourly(*raw)}
    if continuous:
        out["obs_continuous"] = raw
    return out
