"""NWS CLIDFW daily climate report — the official settlement basis product.

NWS Fort Worth issues a preliminary CLIDFW around 4:41 PM CDT reporting the
day's (by then locked) high and low; overnight/early-AM issuances report the
prior completed day. We fetch the latest product and parse today's extremes.

The "is this today's report?" decision (comparing report_date to the climate
day) lives in the callers, which already import `settlement`; this module stays
a pure fetch+parse layer with no upward dependency.
"""

from __future__ import annotations

import re
from datetime import datetime

import config
from config import CACHE_TTL_SECONDS
from sources.common import TZ, get_json


def list_url_for(location: str) -> str:
    """NWS CLI product-list endpoint for a bare product location, e.g. 'ATL'."""
    return "https://api.weather.gov/products/types/CLI/locations/" + location


def list_url(station: str = config.DEFAULT_STATION) -> str:
    """NWS CLI product-list endpoint for `station`'s climate report."""
    return list_url_for(config.station(station).cli_location)

_DATE_RE = re.compile(r"CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})")
# The time column differs by issuing office: NWS Fort Worth (CLIDFW) prints
# "1220 PM" while NWS Austin/San Antonio (CLIAUS) prints "12:20 AM". Accept both
# so the same parser serves every station's CLI product.
_TIME = r"(\d{1,2}:\d{2}|\d{1,4})"
_MAX_RE = re.compile(r"^\s*MAXIMUM\s+(-?\d+)\s+" + _TIME + r"\s+([AP]M)", re.M)
_MIN_RE = re.compile(r"^\s*MINIMUM\s+(-?\d+)\s+" + _TIME + r"\s+([AP]M)", re.M)


def parse_cli(text: str, issued: datetime) -> dict | None:
    """Parse a CLIDFW product's text into today's extremes, or None."""
    dm = _DATE_RE.search(text)
    hm = _MAX_RE.search(text)
    nm = _MIN_RE.search(text)
    if not (dm and hm and nm):
        return None
    try:
        report_date = datetime.strptime(dm.group(1).title(), "%B %d %Y").date()
    except ValueError:
        return None
    return {
        "report_date": report_date,
        "high_f": int(hm.group(1)),
        "low_f": int(nm.group(1)),
        "high_time": f"{hm.group(2)} {hm.group(3)}",
        "low_time": f"{nm.group(2)} {nm.group(3)}",
        "issued": issued.astimezone(TZ),
    }


def fetch_latest_cli(ttl: int | None = None,
                     station: str = config.DEFAULT_STATION) -> dict | None:
    """Fetch and parse the newest CLI product for `station`, or None on failure.

    `ttl` controls the cache freshness of the product list; pass 0 for an
    always-fresh read (the scheduled Action), or a short TTL for the dashboard.
    """
    return fetch_latest_for(config.station(station).cli_location, ttl)


def fetch_latest_for(location: str, ttl: int | None = None) -> dict | None:
    """Fetch and parse the newest CLI product for a bare product location.

    Addressable by location because the Hourly page shows the climate report for
    cities this system does not model; the parser already handles every issuing
    office's time format (verified against all 20 products, 2026-08-07).
    """
    t = CACHE_TTL_SECONDS if ttl is None else ttl
    try:
        listing = get_json(list_url_for(location), ttl=t)
        graph = listing.get("@graph") or []
        if not graph:
            return None
        product = get_json(graph[0]["@id"], ttl=t)
        text = product.get("productText") or ""
        issued = datetime.fromisoformat(product["issuanceTime"])
        return parse_cli(text, issued)
    except Exception:
        return None
