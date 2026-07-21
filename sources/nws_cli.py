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

from config import CACHE_TTL_SECONDS
from sources.common import TZ, get_json

LIST_URL = "https://api.weather.gov/products/types/CLI/locations/DFW"

_DATE_RE = re.compile(r"CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})")
_MAX_RE = re.compile(r"^\s*MAXIMUM\s+(-?\d+)\s+(\d{1,4})\s+([AP]M)", re.M)
_MIN_RE = re.compile(r"^\s*MINIMUM\s+(-?\d+)\s+(\d{1,4})\s+([AP]M)", re.M)


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


def fetch_latest_cli(ttl: int | None = None) -> dict | None:
    """Fetch and parse the newest CLIDFW product, or None on any failure.

    `ttl` controls the cache freshness of the product list; pass 0 for an
    always-fresh read (the scheduled Action), or a short TTL for the dashboard.
    """
    t = CACHE_TTL_SECONDS if ttl is None else ttl
    try:
        listing = get_json(LIST_URL, ttl=t)
        graph = listing.get("@graph") or []
        if not graph:
            return None
        product = get_json(graph[0]["@id"], ttl=t)
        text = product.get("productText") or ""
        issued = datetime.fromisoformat(product["issuanceTime"])
        return parse_cli(text, issued)
    except Exception:
        return None
