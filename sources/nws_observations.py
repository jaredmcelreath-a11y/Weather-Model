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

# The NWS feed counts as down when it has served nothing this recent. Routine
# METARs land ~hourly with ~20 min propagation lag, so 90 min means "missed at
# least one full cycle" — a real gap, not ordinary latency.
STALE_AFTER_S = 90 * 60


def _iem_fallback(start: datetime, now: datetime):
    """The same station's METARs from the IEM ASOS archive (independent
    pipeline), for the NWS-outage path. Best-effort: any failure returns an
    empty series and the caller keeps whatever NWS gave it."""
    from datetime import timedelta
    from sources import station_history
    try:
        # asos.py's day2 is exclusive — +1 day so today's rows are included.
        times, temps = station_history._fetch_series(
            start.date(), now.date() + timedelta(days=1), ttl=60)
    except Exception:
        return [], []
    pairs = [(t, v) for t, v in zip(times, temps) if start <= t <= now]
    return [t for t, _ in pairs], [v for _, v in pairs]


def fetch(limit: int = 500, continuous: bool = False, now: datetime | None = None,
          start: datetime | None = None
          ) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {'obs': (times, temps_f)} sorted ascending in time.

    The feed is sub-hourly (~13 readings/hour), so a fixed count spans far less
    time than it looks: a 200-cap covers only ~15 hours, which from a late-evening
    capture starts *after* the early-morning low and makes the same-day low anchor
    to the evening cooldown (printing several degrees warm). So we instead bound
    the window by `start` = local midnight of `now`'s day, guaranteeing the whole
    settlement day — including the morning minimum — is always in view regardless
    of capture time. `limit` is just a generous ceiling on a single day's readings.

    `start` overrides that default. The last-hour capture needs the whole PRIOR
    climate day in view (~25h back), which clock midnight excludes; callers pass
    that day's LST start instead.

    With `continuous=True`, also return `'obs_continuous'`: the full sub-hourly
    feed (5-minute readings, not just the routine :53 METAR) before the hourly
    reduction. The CLI/Kalshi basis uses it to catch a brief spike between routine
    reports; the default hourly basis (Robinhood) ignores it.
    """
    now = now or datetime.now(TZ)
    start = start or datetime(now.year, now.month, now.day, tzinfo=TZ)  # local midnight
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
    # Outage fallback: the obs series is the nowcasting engine, so a transient
    # NWS gap must not read as "no obs yet today" — that silently reverts the
    # consensus to the pure forecast (live 2026-07-18 04:00: one capture
    # snapped 2°F to 76.8 and back). When NWS has served nothing within
    # STALE_AFTER_S, use IEM's copy of the same METARs instead; a healthy
    # feed never consults IEM, and if IEM comes back empty the (stale) NWS
    # readings are kept.
    if not raw[0] or (now - raw[0][-1]).total_seconds() > STALE_AFTER_S:
        fb = _iem_fallback(start, now)
        if fb[0]:
            raw = fb
    # Settle on the routine hourly readings, not 5-minute spikes.
    out = {"obs": to_hourly(*raw)}
    if continuous:
        out["obs_continuous"] = raw
    return out
