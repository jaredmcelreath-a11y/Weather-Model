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

import config
from config import MARKET_MIN_BUCKET_PRICE
from sources.common import get_json

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def series_for(variable: str, station: str = config.DEFAULT_STATION) -> str | None:
    """Kalshi series ticker for `variable` at `station`, or None for an unknown
    variable. Austin's high/low tickers are asymmetric, so these come from the
    station config, not a pattern (see the KAUS StationConfig)."""
    s = config.station(station)
    return {"high": s.kalshi_high_series, "low": s.kalshi_low_series}.get(variable)


def station_of_ticker(ticker: str) -> str | None:
    """The station code whose Kalshi series prefixes `ticker` (e.g.
    'KXHIGHAUS-26JUL27-T96' -> 'KAUS'), or None. The trader uses this to keep
    each city's position management to its own markets."""
    t = (ticker or "").upper()
    for code in config.STATION_CODES:
        s = config.station(code)
        if t.startswith(s.kalshi_high_series) or t.startswith(s.kalshi_low_series):
            return code
    return None


def variable_of_ticker(ticker: str) -> str | None:
    """'high'/'low' from the ticker's Kalshi series across all stations, or None."""
    t = (ticker or "").upper()
    for code in config.STATION_CODES:
        s = config.station(code)
        if t.startswith(s.kalshi_high_series):
            return "high"
        if t.startswith(s.kalshi_low_series):
            return "low"
    return None


def _event_suffix(day: date) -> str:
    return day.strftime("%y%b%d").upper()  # e.g. date(2026,6,16) -> "26JUN16"


def _f(x):
    """Kalshi returns prices/sizes as strings (e.g. "0.5900"); cast to float."""
    return None if x is None or x == "" else float(x)


def fetch_contracts(variable: str, day: date,
                    station: str = config.DEFAULT_STATION) -> list[dict]:
    """Live range-bucket contracts for `variable` ('high'/'low') on `day`.

    Each dict: label, strike_type/floor/cap (for model mapping), and current
    yes/no bid/ask, last price, volume (all prices in dollars 0–1).
    """
    series = series_for(variable, station)
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


def fetch_orderbook(ticker: str, fetch=None) -> dict:
    """Live resting-bid order book for `ticker`, normalized to
    {"yes": [[price_dollars, size], ...], "no": [...]} with float prices/sizes.

    Kalshi returns the book under `orderbook_fp` with `yes_dollars` /
    `no_dollars` arrays of [price_str, size_str] — prices already in dollars,
    sizes possibly fractional, sorted ascending. `fetch` is injectable for
    tests; the default hits GET /markets/{ticker}/orderbook, cached ~15s."""
    fetch = fetch or (lambda t: get_json(f"{BASE}/markets/{t}/orderbook",
                                         {"depth": 100}, ttl=15))
    ob = (fetch(ticker) or {}).get("orderbook_fp") or {}

    def _side(key):
        return [[_f(p), _f(sz)] for p, sz in (ob.get(key) or [])]

    return {"yes": _side("yes_dollars"), "no": _side("no_dollars")}


def ask_ladder(orderbook: dict, side: str) -> list:
    """Ascending ask ladder in dollars for the side being BOUGHT, reconstructed
    from the opposite side's resting bids: buying YES sells against NO bids
    (yes_ask = 1 - no_bid), and vice-versa. Levels are (price_dollars, size)
    with size floored to whole contracts; zero-size levels dropped. Prices are
    rounded to the cent (Kalshi trades in whole cents)."""
    opp = "no" if side == "yes" else "yes"
    ladder = []
    for bid, sz in (orderbook.get(opp) or []):
        size = int(sz)
        if size <= 0:
            continue
        ladder.append((round(1.0 - bid, 2), size))
    ladder.sort(key=lambda lvl: lvl[0])
    return ladder


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


def implied_forecast(variable: str, day: date,
                     station: str = config.DEFAULT_STATION) -> dict | None:
    """The market's own forecast, distilled from the live contract ladder.

    Each bucket's mid YES price ≈ its implied probability; we normalize across
    buckets (removing the bid/ask overround) into a PMF, then report the implied
    expected temperature `ev` plus the bucket PMF and traded volume. This is what
    we log next to the model so we can later score the *market* against settlement
    the same way we score the model. None when no priced contracts are live.
    """
    rows = []
    for c in fetch_contracts(variable, day, station):
        mid = _bucket_mid(c)
        if mid is None:
            continue
        quotes = [p for p in (c.get("yes_bid"), c.get("yes_ask")) if p is not None]
        if not quotes:
            continue
        rows.append((c.get("floor"), c.get("cap"), mid,
                     sum(quotes) / len(quotes), c.get("volume") or 0.0))
    if not rows:
        return None
    all_volume = sum(v for *_, v in rows)   # total across every priced contract
    # Trim bid/ask-noise tails before normalizing so they don't drag the EV/PMF;
    # keep all if the floor would empty the ladder (flat/illiquid market).
    kept = [r for r in rows if r[3] >= MARKET_MIN_BUCKET_PRICE]
    priced = kept or rows
    tot = sum(p for *_, p, _ in priced)
    if tot <= 0:
        return None
    return {
        "ev": round(sum(mid * p for _, _, mid, p, _ in priced) / tot, 2),
        "buckets": [[f, cap, round(p / tot, 4)] for f, cap, _, p, _ in priced],
        "volume": round(all_volume, 1),
        # Per-bracket traded volume for EVERY priced contract (pre-PMF-trim), so
        # the Edge report can later recover the settled bracket's own liquidity —
        # the whole-market `volume` above is always in the thousands and can't.
        "bucket_volume": [[f, cap, v] for f, cap, _, _, v in rows],
    }


def implied_block(today: date, tomorrow: date,
                  station: str = config.DEFAULT_STATION) -> dict:
    """{which: {variable: implied_forecast}} for today/tomorrow — the market half
    of a logged snapshot. Empty branches are omitted (no live market)."""
    out: dict = {}
    for which, day in (("today", today), ("tomorrow", tomorrow)):
        day_block = {}
        for var in ("high", "low"):
            try:
                f = implied_forecast(var, day, station)
            except Exception:
                f = None
            if f:
                day_block[var] = f
        if day_block:
            out[which] = day_block
    return out


def ask_rows(variable: str, day: date,
             station: str = config.DEFAULT_STATION) -> list:
    """[[floor, cap, yes_bid, yes_ask], ...] — the untouched contract ladder.

    `implied_forecast`'s PMF is normalized (the bid/ask overround removed), so it
    cannot say what a bracket would actually have COST. The close-slot capture
    logs these raw quotes so the last-hour question — was the already-settled
    bracket still buyable under a dollar? — is answerable after the fact.
    """
    return [[c.get("floor"), c.get("cap"), c.get("yes_bid"), c.get("yes_ask")]
            for c in fetch_contracts(variable, day, station)]


# ---- Multi-city scanner support -------------------------------------------
# Read-only discovery for the price scanner. Kept here (not in a station config)
# because the scanner deliberately has no per-city setup: city identity IS the
# series ticker.

WEATHER_CATEGORY = "Climate and Weather"

# Not daily city high/low markets: national aggregate, and an hourly directional
# NYC series that shares the KXHIGH prefix.
EXCLUDED_SERIES = {"KXHIGHUS", "KXHIGHNYD"}


def parse_kalshi_ts(value):
    """Kalshi's ISO-8601 timestamps ('2026-08-04T06:00:00Z') as aware UTC
    datetimes. None (not an exception) for missing or unparseable input, so a
    single malformed market never kills a scan pass."""
    from datetime import datetime as _dt
    if not value:
        return None
    try:
        return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def list_weather_series(fetch=None) -> list:
    """Daily city high/low temperature series, as [{"ticker", "title"}, ...].

    Discovered from the live category rather than hardcoded: the real list is
    full of legacy duplicates (KXHIGHDEN beside KXHIGHTEMPDEN, three Houston
    variants) and the naming is inconsistent even within a city (KXHIGHAUS but
    KXLOWTAUS). A hardcoded table would encode today's mess and rot. Callers
    filter to live ones with `is_series_active`."""
    fetch = fetch or (lambda: get_json(f"{BASE}/series",
                                       {"category": WEATHER_CATEGORY}, ttl=3600))
    out = []
    for s in (fetch() or {}).get("series") or []:
        ticker = (s.get("ticker") or "").upper()
        if ticker in EXCLUDED_SERIES:
            continue
        if not (ticker.startswith("KXHIGH") or ticker.startswith("KXLOW")):
            continue
        out.append({"ticker": ticker, "title": s.get("title") or ""})
    return sorted(out, key=lambda s: s["ticker"])


def list_series_markets(series_ticker: str, status=None, fetch=None) -> list:
    """Every market under `series_ticker`, optionally filtered by status.

    One call returns the whole bracket ladder, which is why the scanner needs no
    per-bracket requests. `fetch` takes the params dict, for tests."""
    params = {"series_ticker": series_ticker, "limit": 200}
    if status:
        params["status"] = status
    fetch = fetch or (lambda p: get_json(f"{BASE}/markets", p, ttl=60))
    return (fetch(params) or {}).get("markets") or []


def is_series_active(markets: list, now, window_days: int = 7) -> bool:
    """True when the series has a live market or one that closed recently.

    Drops the dead legacy series without hardcoding which ones they are."""
    from datetime import timedelta as _td
    cutoff = now - _td(days=window_days)
    for m in markets:
        if (m.get("status") or "").lower() in ("open", "active"):
            return True
        closed = parse_kalshi_ts(m.get("close_time"))
        if closed is not None and closed >= cutoff:
            return True
    return False
