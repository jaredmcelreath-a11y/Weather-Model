"""Live Kalshi market data — the exchange behind Robinhood's prediction markets.

Public, no-auth endpoints. Maps each daily Dallas temperature event to its
range-bucket contracts with current YES/NO bid/ask, so the dashboard can show the
live market price next to the model's probability and flag edges.

Settlement note: per Kalshi's contract rules these resolve on the NWS
Climatological Report (Daily) for Dallas/Fort Worth (CLIDFW). Confirm the prices
and structure match your Robinhood screen before trading.
"""

from __future__ import annotations

from datetime import date

from sources.common import get_json

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"high": "KXHIGHTDAL", "low": "KXLOWTDAL"}


def _event_suffix(day: date) -> str:
    return day.strftime("%y%b%d").upper()  # e.g. date(2026,6,16) -> "26JUN16"


def _f(x):
    """Kalshi returns prices/sizes as strings (e.g. "0.5900"); cast to float."""
    return None if x is None or x == "" else float(x)


def fetch_contracts(variable: str, day: date) -> list[dict]:
    """Live range-bucket contracts for `variable` ('high'/'low') on `day`.

    Each dict: label, strike_type/floor/cap (for model mapping), and current
    yes/no bid/ask, last price, volume (all prices in dollars 0–1).
    """
    series = SERIES.get(variable)
    if not series:
        return []
    data = get_json(f"{BASE}/markets",
                    {"series_ticker": series, "status": "open", "limit": 100},
                    ttl=30)
    suffix = _event_suffix(day)
    out = []
    for m in data.get("markets") or []:
        if not m.get("event_ticker", "").upper().endswith(suffix):
            continue
        out.append({
            "ticker": m["ticker"],
            "label": m.get("yes_sub_title") or m.get("subtitle") or "",
            "strike_type": m.get("strike_type"),
            "floor": m.get("floor_strike"),
            "cap": m.get("cap_strike"),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask": _f(m.get("yes_ask_dollars")),
            "no_bid": _f(m.get("no_bid_dollars")),
            "no_ask": _f(m.get("no_ask_dollars")),
            "last": _f(m.get("last_price_dollars")),
            "volume": _f(m.get("volume_fp")),
        })

    def sort_key(c):
        if c["strike_type"] == "less":
            return (0, c["cap"] if c["cap"] is not None else -1)
        if c["strike_type"] == "greater":
            return (2, c["floor"] if c["floor"] is not None else 1e9)
        return (1, c["floor"] if c["floor"] is not None else 0)

    out.sort(key=sort_key)
    return out
