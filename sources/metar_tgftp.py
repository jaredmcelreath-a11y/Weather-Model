"""The raw routine METAR, ~2 minutes after the observation.

The dashboard's live reading has always come from the 5-minute MADIS feed on
api.weather.gov, which is the freshest CONTINUOUS source but lands ~20 minutes
late and in whole degrees Celsius. Whole °C cannot express 101°F at all --
38°C is 100.4 and 39°C is 102.2 -- so a bracket boundary at 101 is invisible on
that feed no matter how long you stare at it.

NOAA republishes each station's latest routine report as a two-line text file
within about two minutes of the observation (measured 2026-08-16 across KAUS,
KDFW, KORD and KJFK: all +2.2 min). It carries the `Txxxxxxxx` remark in tenths
of a degree C -- about 0.18°F -- so it is both an order of magnitude fresher
and finer than the 5-minute feed.

DISPLAY ONLY. This never feeds `predict_variable`: the settlement basis is a
separate question from what the page shows, and mixing them is what the
`obs_continuous_display` split in model.gather_series exists to prevent.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
from sources.common import get_text

URL = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{id}.TXT"

# Line 1 of the file, always UTC: "2026/08/16 21:53".
_STAMP = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")
# The remark: sign digit + tenths of °C, temperature then dewpoint.
_T_GROUP = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
# The report body's whole-°C temperature/dewpoint, "39/15" or "M03/M09".
_BODY = re.compile(r"\s(M?\d{2})/(M?\d{2})\s")


def _c_to_f(celsius: float) -> float:
    # Rounded because 38.9 * 9 / 5 + 32 is 102.02000000000001 in binary floating
    # point, and this value is compared and displayed rather than accumulated.
    return round(celsius * 9 / 5 + 32, 2)


def parse(text: str):
    """(observation time in UTC, temperature °F), or None.

    None rather than an exception for every unreadable shape: this feed is an
    enhancement to the live reading, and a reshaped or dead upstream must fall
    back to the 5-minute feed instead of taking the page down."""
    lines = (text or "").strip().splitlines()
    if len(lines) < 2:
        return None
    stamp = _STAMP.match(lines[0].strip())
    if not stamp:
        return None
    year, month, day, hour, minute = (int(g) for g in stamp.groups())
    try:
        when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    report = lines[1]
    group = _T_GROUP.search(report)
    if group:
        sign, tenths = group.group(1), int(group.group(2))
        celsius = -tenths / 10.0 if sign == "1" else tenths / 10.0
        return when, _c_to_f(celsius)
    body = _BODY.search(report)
    if not body:
        return None
    raw = body.group(1)
    celsius = -int(raw[1:]) if raw.startswith("M") else int(raw)
    return when, _c_to_f(float(celsius))


def latest_for_id(station_id: str, fetch=None) -> dict | None:
    """{'temp': °F, 'time': aware UTC} for a RAW station id, or None.

    Takes an ICAO rather than a config station code, so it serves the Hourly
    page's 20 reference cities, which `config` has never heard of — the same
    split `nws_observations.latest` makes, and for the same reason. The
    timestamp stays UTC and localising it is the caller's job: a Miami reading
    must not be stamped Central."""
    fetch = fetch or (lambda url: get_text(url, ttl=60, timeout=10))
    try:
        parsed = parse(fetch(URL.format(id=station_id)))
    except Exception:                     # noqa: BLE001 - see parse's docstring
        return None
    if parsed is None:
        return None
    when, temp = parsed
    return {"temp": temp, "time": when}


def latest(station: str = config.DEFAULT_STATION, fetch=None):
    """The station's newest routine reading, in ITS OWN timezone, or None.

    Station-local because the caller compares this against the 5-minute feed's
    last reading, which model.py has already localised; comparing a UTC value
    against a local one would silently pick the wrong reading."""
    cfg = config.station(station)
    got = latest_for_id(cfg.id, fetch=fetch)
    if got is None:
        return None
    return got["time"].astimezone(ZoneInfo(cfg.timezone)), got["temp"]
