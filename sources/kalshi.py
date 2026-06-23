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


def _bucket_mid(c) -> float | None:
    """A representative temperature for a contract's range, for an implied EV.

    Open-ended tails ('less'/'greater') have no true midpoint, so use a point
    half a degree inside the strike as a stand-in (good enough for a point
    estimate; the tails rarely carry much probability mass)."""
    f, cap, kind = c.get("floor"), c.get("cap"), c.get("strike_type")
    if kind == "between" and f is not None and cap is not None:
        return (f + cap) / 2.0
    if kind == "less" and cap is not None:
        return cap - 0.5
    if kind == "greater" and f is not None:
        return f + 0.5
    return None


def implied_forecast(variable: str, day: date) -> dict | None:
    """The market's own forecast, distilled from the live contract ladder.

    Each bucket's mid YES price ≈ its implied probability; we normalize across
    buckets (removing the bid/ask overround) into a PMF, then report the implied
    expected temperature `ev` plus the bucket PMF and traded volume. This is what
    we log next to the model so we can later score the *market* against settlement
    the same way we score the model. None when no priced contracts are live.
    """
    rows = []
    for c in fetch_contracts(variable, day):
        mid = _bucket_mid(c)
        if mid is None:
            continue
        quotes = [p for p in (c.get("yes_bid"), c.get("yes_ask")) if p is not None]
        if not quotes:
            continue
        rows.append((c.get("floor"), c.get("cap"), mid,
                     sum(quotes) / len(quotes), c.get("volume") or 0.0))
    tot = sum(p for *_, p, _ in rows)
    if not rows or tot <= 0:
        return None
    return {
        "ev": round(sum(mid * p for _, _, mid, p, _ in rows) / tot, 2),
        "buckets": [[f, cap, round(p / tot, 4)] for f, cap, _, p, _ in rows],
        "volume": round(sum(v for *_, v in rows), 1),
    }


def implied_block(today: date, tomorrow: date) -> dict:
    """{which: {variable: implied_forecast}} for today/tomorrow — the market half
    of a logged snapshot. Empty branches are omitted (no live market)."""
    out: dict = {}
    for which, day in (("today", today), ("tomorrow", tomorrow)):
        day_block = {}
        for var in ("high", "low"):
            try:
                f = implied_forecast(var, day)
            except Exception:
                f = None
            if f:
                day_block[var] = f
        if day_block:
            out[which] = day_block
    return out
