"""Normalized, READ-ONLY fetchers over the authenticated Kalshi client.

Pulls the user's fills and settlements for the Dallas temp series (both the recent
/portfolio tier and the older /historical tier), pages through Kalshi's cursor
pagination, and normalizes to plain dicts. `fills` filters by series AND start
date (by the ticker's WEATHER day, not the fill timestamp — see fills());
`settlements` filters by series only (it's keyed by ticker and looked up
only for tickers already date-filtered via fills) — its `start` param is
accepted for signature symmetry with `fills`, not used to filter.
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


def _ticker_date(ticker: str) -> date | None:
    """Weather (target) day from a ticker like 'KXHIGHTDAL-26JUL22-B97', or None
    if unparsable. (Duplicated from bet_history._ticker_date to keep the sources
    layer from importing upward.)"""
    try:
        return datetime.strptime(ticker.split("-")[1], "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def _iter_pages(fetch, path, items_key):
    """Yield each item across all cursor pages of `path`."""
    cursor = None
    while True:
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
    # /portfolio holds recent fills (required); /historical holds only
    # older-than-cutoff fills and is best-effort — if that tier 404s or errors,
    # skip it rather than failing the whole page (recent bets are all in /portfolio).
    for path, required in (("/portfolio/fills", True), ("/historical/fills", False)):
        try:
            items = list(_iter_pages(fetch, path, "fills"))
        except Exception:
            if required:
                raise
            continue
        for f in items:
            ticker = f.get("ticker", "")
            var = variable_of(ticker)
            if var is None:
                continue
            ts = _parse_ts(f["created_time"])
            # Cut off by the ticker's WEATHER day, not the fill's UTC timestamp:
            # the whole page buckets bets by weather day, and a bet placed the
            # evening of day D Central rolls into D+1 in UTC — filtering on ts.date()
            # let such a fill slip past `start` and then display under weather day D
            # (e.g. a Jul 22 market showing after a Jul 23 reset). Fall back to the
            # timestamp only when the ticker can't be parsed.
            wday = _ticker_date(ticker)
            if (wday or ts.date()) < start:
                continue
            fid = f.get("fill_id") or f.get("trade_id")
            if fid in seen:
                continue
            seen.add(fid)
            side = f.get("side")
            # Kalshi fills: count_fp (string, maybe fractional) and *_price_dollars
            # (string dollars). Keep BOTH side prices — a sell realizes at the
            # dominant-held side's price, not the fill's own `side` (see build_rows).
            yes_p = float(f.get("yes_price_dollars") or 0)
            no_p = float(f.get("no_price_dollars") or 0)
            out.append({
                "trade_id": fid, "ticker": ticker, "variable": var,
                "side": side, "action": f.get("action"),
                "count": float(f.get("count_fp") or 0),
                "price": yes_p if side == "yes" else no_p,
                "yes_price": yes_p, "no_price": no_p,
                "fee": float(f.get("fee_cost") or 0), "ts": ts,
            })
    return out


def settlements(start: date, fetch=None) -> dict[str, dict]:
    fetch = fetch or kalshi_auth.signed_get
    out: dict[str, dict] = {}
    for path, required in (("/portfolio/settlements", True),
                           ("/historical/settlements", False)):
        try:
            items = list(_iter_pages(fetch, path, "settlements"))
        except Exception:
            if required:
                raise
            continue
        for s in items:
            ticker = s.get("ticker", "")
            if variable_of(ticker) is None:
                continue
            rev = s.get("revenue")   # cents -> dollars (actual payout received)
            out[ticker] = {"result": s.get("market_result"),
                           "ts": _parse_ts(s["settled_time"]),
                           "revenue": (rev / 100.0) if rev is not None else None,
                           "fee": float(s.get("fee_cost") or 0)}
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


def market_price(ticker: str, side: str, fetch_public=None):
    """Current sellable value (dollars, ~0-1) of `side` for `ticker` — the BID for
    the held side (what you'd collect selling now), fallback to the last price
    (side-adjusted). None if unavailable. Read-only; a short TTL so it's live."""
    fetch_public = fetch_public or (lambda t: get_json(
        f"{kalshi_auth.HOST}{kalshi_auth.API_PREFIX}/markets/{t}", ttl=30))
    m = (fetch_public(ticker) or {}).get("market") or {}

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    if side == "yes":
        bid = _f(m.get("yes_bid_dollars"))
        last = _f(m.get("last_price_dollars"))
    else:
        bid = _f(m.get("no_bid_dollars"))
        last = _f(m.get("last_price_dollars"))
        if last is not None:
            last = 1 - last                      # last_price is in YES terms
    return bid if bid is not None else last



def balance(fetch=None):
    """Current Kalshi cash balance in DOLLARS (the API returns cents), or None on
    error. Read-only GET /portfolio/balance."""
    fetch = fetch or kalshi_auth.signed_get
    try:
        b = fetch("/portfolio/balance", None) or {}
    except Exception:
        return None
    cents = b.get("balance")
    return (cents / 100.0) if cents is not None else None
