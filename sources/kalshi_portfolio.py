"""Normalized, READ-ONLY fetchers over the authenticated Kalshi client.

Pulls the user's fills and settlements for the Dallas temp series (both the recent
/portfolio tier and the older /historical tier), pages through Kalshi's cursor
pagination, filters to the series and start date, and normalizes to plain dicts.
Market metadata (strike range) comes from the PUBLIC markets endpoint (no auth).
"""
from __future__ import annotations

from datetime import date, datetime

from sources import kalshi_auth
from sources.common import get_json

SERIES_PREFIXES = ("KXHIGHTDAL", "KXLOWTDAL")


def variable_of(ticker: str) -> str | None:
    if ticker.startswith("KXHIGHTDAL"):
        return "high"
    if ticker.startswith("KXLOWTDAL"):
        return "low"
    return None


def _parse_ts(s: str) -> datetime:
    # Kalshi timestamps are ISO 8601 with a trailing Z; normalize to +00:00.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iter_pages(fetch, path, items_key):
    """Yield each item across all cursor pages of `path`."""
    cursor = None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        # tests key the fake on (path, cursor) with no cursor -> None
        page = fetch(path, {"cursor": cursor} if cursor else None)
        for item in page.get(items_key) or []:
            yield item
        cursor = page.get("cursor")
        if not cursor:
            return


def fills(start: date, fetch=None) -> list[dict]:
    fetch = fetch or kalshi_auth.signed_get
    seen, out = set(), []
    for path in ("/portfolio/fills", "/historical/fills"):
        for f in _iter_pages(fetch, path, "fills"):
            ticker = f.get("ticker", "")
            var = variable_of(ticker)
            if var is None:
                continue
            ts = _parse_ts(f["created_time"])
            if ts.date() < start:
                continue
            tid = f.get("trade_id")
            if tid in seen:
                continue
            seen.add(tid)
            side = f.get("side")
            price_c = f.get("yes_price") if side == "yes" else f.get("no_price")
            out.append({
                "trade_id": tid, "ticker": ticker, "variable": var,
                "side": side, "action": f.get("action"),
                "count": int(f.get("count", 0)),
                "price": (price_c or 0) / 100.0, "ts": ts,
            })
    return out


def settlements(start: date, fetch=None) -> dict[str, dict]:
    fetch = fetch or kalshi_auth.signed_get
    out: dict[str, dict] = {}
    for path in ("/portfolio/settlements", "/historical/settlements"):
        for s in _iter_pages(fetch, path, "settlements"):
            ticker = s.get("ticker", "")
            if variable_of(ticker) is None:
                continue
            out[ticker] = {"result": s.get("market_result"),
                           "ts": _parse_ts(s["settled_time"])}
    return out


def _public_market(ticker: str) -> dict:
    return get_json(f"{kalshi_auth.HOST}{kalshi_auth.API_PREFIX}/markets/{ticker}",
                    ttl=3600)


def market_meta(ticker: str, fetch_public=None) -> dict:
    fetch_public = fetch_public or _public_market
    m = (fetch_public(ticker) or {}).get("market") or {}
    return {
        "label": m.get("yes_sub_title") or m.get("subtitle") or ticker,
        "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
        "strike_type": m.get("strike_type"), "variable": variable_of(ticker),
    }
