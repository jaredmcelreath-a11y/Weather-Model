"""Shared HTTP, caching, and parsing helpers for the data sources."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from config import CACHE_TTL_SECONDS, NWS_USER_AGENT, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

_session = requests.Session()
_session.headers.update({"User-Agent": NWS_USER_AGENT})


def _cache_path(url: str, params: dict) -> str:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    return os.path.join(_CACHE_DIR, digest + ".json")


def get_json(url: str, params: dict | None = None,
             ttl: int = CACHE_TTL_SECONDS, timeout: int = 30,
             retries: int = 2) -> dict:
    """GET JSON with a simple on-disk TTL cache. ttl=0 disables caching.

    Transient network errors (timeouts, dropped connections) are retried with a
    short backoff so a brief upstream hiccup doesn't fail the call; a sustained
    outage still raises after `retries` extra attempts, letting the caller drop
    that source rather than crash the whole page.
    """
    params = params or {}
    path = _cache_path(url, params)
    if ttl > 0 and os.path.exists(path):
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return json.load(fh)
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            break
        except requests.exceptions.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))  # brief backoff for a transient blip
    resp.raise_for_status()
    data = resp.json()
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


def get_text(url: str, params: dict | None = None,
             ttl: int = 7 * 24 * 3600, timeout: int = 90) -> str:
    """GET text with a long-lived on-disk cache (for immutable archive data)."""
    params = params or {}
    path = _cache_path(url, params) + ".txt"
    if ttl > 0 and os.path.exists(path):
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return fh.read()
    for attempt in range(4):
        resp = _session.get(url, params=params, timeout=timeout)
        if resp.status_code == 429:
            time.sleep(5 * (attempt + 1))  # polite backoff for archive rate limits
            continue
        resp.raise_for_status()
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(resp.text)
        return resp.text
    resp.raise_for_status()
    return resp.text


def parse_local_times(iso_times: list[str]) -> list[datetime]:
    """Parse ISO timestamps into tz-aware datetimes in the station timezone.

    Open-Meteo (with timezone=America/Chicago) returns naive local strings;
    NWS returns UTC-offset strings. Both are normalized to the station tz.
    """
    out = []
    for s in iso_times:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        out.append(dt.astimezone(TZ))
    return out


def c_to_f(celsius: float | None) -> float | None:
    return None if celsius is None else celsius * 9.0 / 5.0 + 32.0


def to_hourly(times: list[datetime], temps: list[float]):
    """Reduce a sub-hourly series to the routine on-the-hour METAR readings.

    Weather Underground / NWS settle the daily high/low on the hourly
    observations (issued ~:53), not the 5-minute ASOS data. Sub-hourly spikes
    (e.g. a brief 91.4°F between hours) are excluded so the model's high/low
    matches what actually settles. Keeps one reading per hour: the one whose
    minute is closest to :53, within the routine window [51, 56].
    """
    best: dict = {}
    for t, v in zip(times, temps):
        if v is None or not (51 <= t.minute <= 56):
            continue
        key = (t.year, t.month, t.day, t.hour)
        dist = abs(t.minute - 53)
        if key not in best or dist < best[key][0]:
            best[key] = (dist, t, v)
    rows = sorted(best.values(), key=lambda r: r[1])
    return [r[1] for r in rows], [r[2] for r in rows]
