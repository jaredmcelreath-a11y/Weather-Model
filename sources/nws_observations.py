"""Live KDFW observations (METAR) — the nowcasting engine.

These are what *actually happened*: the observed max-so-far is a hard floor on
today's high, the observed min-so-far a hard ceiling on today's low.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import STATION_ID, TIMEZONE
from sources.common import c_to_f, get_json, parse_local_times, to_hourly

OBS_URL = f"https://api.weather.gov/stations/{STATION_ID}/observations"
TZ = ZoneInfo(TIMEZONE)


def fetch(limit: int = 500, continuous: bool = False, now: datetime | None = None
          ) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {'obs': (times, temps_f)} sorted ascending in time.

    The feed is sub-hourly (~13 readings/hour), so a fixed count spans far less
    time than it looks: a 200-cap covers only ~15 hours, which from a late-evening
    capture starts *after* the early-morning low and makes the same-day low anchor
    to the evening cooldown (printing several degrees warm). So we instead bound
    the window by `start` = local midnight of `now`'s day, guaranteeing the whole
    settlement day — including the morning minimum — is always in view regardless
    of capture time. `limit` is just a generous ceiling on a single day's readings.

    With `continuous=True`, also return `'obs_continuous'`: the full sub-hourly
    feed (5-minute readings, not just the routine :53 METAR) before the hourly
    reduction. The CLI/Kalshi basis uses it to catch a brief spike between routine
    reports; the default hourly basis (Robinhood) ignores it.
    """
    now = now or datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)  # local midnight
    # Short cache so a newly-published reading surfaces within one page refresh.
    # The dashboard re-blends every ~60s and NWS publishes a new 5-min reading only
    # every 5 min, so 60s adds at most ~1 min of staleness on top of the feed's own
    # ~20-min propagation lag (down from up to 5 min with the old 300s window).
    data = get_json(OBS_URL, {"limit": limit, "start": start.isoformat()}, ttl=60)
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
