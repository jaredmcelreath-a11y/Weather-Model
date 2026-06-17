"""Live Robinhood prediction-market data for the Dallas daily temperature.

Robinhood's weather markets run on ForecastEx as a 1-degree ladder:
  * High: "Greater than T°"  (YES if the high exceeds T, i.e. high >= T+1)
  * Low:  "Lower than T°"    (YES if the low is below T, i.e. low  <= T-1)

The contracts and *live quotes* are embedded in the category page's Next.js
`__NEXT_DATA__` (server-rendered, no auth). We read the stable category listing
page — which includes every city's events for today and tomorrow plus a `quotes`
map — and pick out Dallas. Re-fetching gives fresh prices.
"""

from __future__ import annotations

import json
import re
from datetime import date

import requests

CATEGORY = {"high": "daily-high-temperature", "low": "daily-low-temperature"}
KIND = {"high": ">", "low": "<"}
BASE = "https://robinhood.com/us/en/prediction-markets/climate"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "text/html",
}
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _f(x):
    return None if x in (None, "") else float(x)


def _event_name(variable: str, day: date) -> str:
    label = "High" if variable == "high" else "Low"
    return f"Dallas Daily Temperature {label} {day.strftime('%B')} {day.day} {day.year}"


def fetch_ladder(variable: str, day: date) -> list[dict]:
    """Live Dallas ladder for `variable` on `day`:
    [{label, kind, strike, yes_bid, yes_ask, no_bid, no_ask, last}], strike-sorted.
    """
    cat = CATEGORY.get(variable)
    if not cat:
        return []
    resp = requests.get(f"{BASE}/{cat}/", headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    m = _NEXT.search(resp.text)
    if not m:
        return []
    pp = json.loads(m.group(1))["props"]["pageProps"]
    quotes_by_symbol = {q.get("symbol"): q for q in pp.get("quotes", {}).values()}

    target = _event_name(variable, day)
    event = next((e for e in pp.get("events", []) if e.get("name") == target), None)
    if not event:
        return []

    ecs = event["eventContracts"]
    ecs = list(ecs.values()) if isinstance(ecs, dict) else ecs
    out = []
    for c in ecs:
        short = (c.get("displayShortName") or "").strip()
        q = quotes_by_symbol.get(c.get("symbol"), {})
        out.append({
            "label": short,                                   # ">90" / "<69"
            "kind": KIND[variable],
            "strike": int(float(c["floorStrikeValue"])),
            "yes_bid": _f(q.get("yes_bid_price")),
            "yes_ask": _f(q.get("yes_ask_price")),
            "no_bid": _f(q.get("no_bid_price")),
            "no_ask": _f(q.get("no_ask_price")),
            "last": _f(q.get("last_trade_price")),
        })
    out.sort(key=lambda c: c["strike"])
    return out
