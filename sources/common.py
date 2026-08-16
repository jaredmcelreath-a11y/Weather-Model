"""Shared HTTP, caching, and parsing helpers for the data sources."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from config import CACHE_TTL_SECONDS, NWS_USER_AGENT, TIMEZONE

TZ = ZoneInfo(TIMEZONE)
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

_session = requests.Session()
_session.headers.update({"User-Agent": NWS_USER_AGENT})

# Circuit breaker: once a host looks DEAD, fast-fail further calls to it for a
# short cooldown. A total outage of one host (e.g. api.open-meteo.com, which
# several call sites hit per snapshot) then costs a few timeouts instead of one
# per call site. Keyed by host, so a healthy sibling host is unaffected.
#
# "Dead" means _FAILURES_TO_TRIP calls exhausted their retries IN A ROW, not
# one. A single exhausted call cannot distinguish a slow response from a dead
# host, and treating the two alike broke batch callers: screen.py hits
# api.weather.gov ~3x per city across 40 cities, which all falls inside the 60s
# cooldown, so one read timeout cost 38 cities (observed 2026-08-16:
# {'cities': 2, 'errors': 38}). Consecutive failures are the discriminator --
# a dead host never succeeds in between, a slow one does. Any success clears
# the count, so the trip only fires on a real run of failures.
#
# The cost of the threshold is that a genuine outage now pays up to
# _FAILURES_TO_TRIP timeouts on the first pass rather than one. That is bounded,
# it only happens once per cooldown, and the pages already degrade around a dead
# source rather than waiting on it.
#
# {host: (consecutive failures, time tripped or None)} -- one dict so that
# resetting it in a test resets the whole breaker.
_FAILED_HOSTS: dict[str, tuple[int, float | None]] = {}
_HOST_COOLDOWN = 60  # seconds
_FAILURES_TO_TRIP = 3


def _cache_path(url: str, params: dict) -> str:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    return os.path.join(_CACHE_DIR, digest + ".json")


def get_json(url: str, params: dict | None = None,
             ttl: int = CACHE_TTL_SECONDS, timeout: int = 10,
             retries: int = 1) -> dict:
    """GET JSON with a simple on-disk TTL cache. ttl=0 disables caching.

    Transient network errors (timeouts, dropped connections) are retried once
    with a short backoff so a brief upstream hiccup doesn't fail the call; a
    sustained outage still raises after `retries` extra attempts, letting the
    caller drop that source rather than crash the whole page. The timeout is
    kept tight (these APIs normally answer in well under a second) so a dead
    upstream is abandoned in ~20s, not ~90s.
    """
    params = params or {}
    path = _cache_path(url, params)
    if ttl > 0 and os.path.exists(path):
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return json.load(fh)
    host = urlparse(url).netloc
    failures, tripped_at = _FAILED_HOSTS.get(host, (0, None))
    if tripped_at is not None:
        if time.time() - tripped_at < _HOST_COOLDOWN:
            raise requests.exceptions.ConnectionError(
                f"{host} skipped: {failures} consecutive failures within "
                f"{_HOST_COOLDOWN}s cooldown")
        del _FAILED_HOSTS[host]  # cooldown elapsed — allow a fresh probe
        failures = 0
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            _FAILED_HOSTS.pop(host, None)  # recovered — clear the breaker
            break
        except requests.exceptions.RequestException:
            if attempt == retries:
                failures += 1
                # Only a RUN of failures means the host is down; one is a blip.
                _FAILED_HOSTS[host] = (
                    failures,
                    time.time() if failures >= _FAILURES_TO_TRIP else None)
                raise
            time.sleep(2 * (attempt + 1))  # brief backoff for a transient blip
    resp.raise_for_status()
    data = resp.json()
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


# Open-Meteo's free tier is rate-limited PER IP. On shared hosting (Streamlit
# Cloud, etc.) thousands of apps share one egress IP, so the busy api.open-meteo.com
# host gets throttled (HTTP 429) and the deterministic models feed drops out. An
# API key routes the call through the keyed `customer-` endpoint, which has a
# dedicated quota not tied to the shared IP. Absent the key, behavior is unchanged.
_OPEN_METEO_CUSTOMER = {
    "api.open-meteo.com": "customer-api.open-meteo.com",
    "ensemble-api.open-meteo.com": "customer-ensemble-api.open-meteo.com",
    "historical-forecast-api.open-meteo.com": "customer-historical-forecast-api.open-meteo.com",
    "archive-api.open-meteo.com": "customer-archive-api.open-meteo.com",
}


def get_open_meteo(url: str, params: dict | None = None, **kw) -> dict:
    """get_json for Open-Meteo, routed through the keyed customer endpoint when
    OPEN_METEO_API_KEY is set (a dedicated quota that dodges the shared-IP free
    tier rate limit). No key → the free host, unchanged."""
    key = os.environ.get("OPEN_METEO_API_KEY", "").strip()
    params = dict(params or {})
    if key:
        parts = urlparse(url)
        host = _OPEN_METEO_CUSTOMER.get(parts.netloc, parts.netloc)
        url = parts._replace(netloc=host).geturl()
        params["apikey"] = key
    return get_json(url, params, **kw)


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
