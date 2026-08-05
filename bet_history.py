"""Assemble the user's Kalshi fills + settlements into per-market bet rows, with
realized P&L, summary stats, and the cumulative equity curve. Pure functions over
the normalized dicts from sources.kalshi_portfolio — no network, no Streamlit.

Model-at-bet-time annotation lives in the same module (added in Task 4) but is a
separate pass (annotate_rows) so assembly stays model-free.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import median

# Fresh-start reset (2026-07-26): the trading history was rebased to a clean slate on
# this date with a new bankroll. Fills/settlements before it are no longer fetched or
# shown; the prior 2026-07-23 → 2026-07-25 run ($11.99 start) is archived in memory.
BETS_START = date(2026, 7, 26)
# Starting bankroll ($): the equity curve's baseline and the "Total % Gain"
# denominator (net realized profit as a percent of this). Change this one value if
# the real starting figure differs.
STARTING_BANKROLL = 10.71


def _split_episodes(group: list[dict]) -> list[list[dict]]:
    """Partition one ticker's fills into independent position episodes. A YES stake and a
    NO stake on the same bracket — or a fresh entry after you've sold a position out — are
    economically separate trades, so each becomes its own row. Walk the fills oldest→newest:
    a buy opens/adds to the running position, a sell reduces it (side-agnostic — Kalshi may
    record a close on either outcome, see build_rows), and when the size returns to flat the
    episode closes and the next buy starts a fresh one. The trailing episode (one that never
    sold flat) is the position held to settlement. A single-position ticker yields exactly
    one episode = the whole group, so this is a no-op for the common case."""
    episodes: list[list[dict]] = []
    cur: list[dict] = []
    size, opened = 0.0, False
    for f in sorted(group, key=lambda f: f["ts"]):
        cur.append(f)
        if f["action"] == "buy":
            size += f["count"]
            opened = True
        else:
            size -= f["count"]
        if opened and size <= 1e-9:              # position back to flat -> episode done
            episodes.append(cur)
            cur, size, opened = [], 0.0, False
    if cur:                                      # trailing position still held (-> settlement)
        episodes.append(cur)
    return episodes


def build_rows(fills: list[dict], settlements: dict, meta: dict) -> list[dict]:
    by_ticker: dict[str, list] = {}
    for f in fills:
        by_ticker.setdefault(f["ticker"], []).append(f)

    rows = []
    for ticker, ticker_fills in by_ticker.items():
        episodes = _split_episodes(ticker_fills)
        for i, group in enumerate(episodes):
            # A ticker's settlement (held-to-expiry payout) belongs ONLY to the trailing,
            # still-open episode; earlier episodes were sold flat before expiry, so they hit
            # the 'closed' branch below and any settle would be ignored anyway.
            settle = settlements.get(ticker) if i == len(episodes) - 1 else None
            rows.append(_position_row(ticker, group, settle, meta))
    rows.sort(key=lambda r: r["first_ts"], reverse=True)  # newest first
    return rows


def _position_row(ticker: str, group: list[dict], settle: dict | None,
                  meta: dict) -> dict:
    m = meta.get(ticker, {})
    buys_yes = sum(f["count"] for f in group if f["side"] == "yes" and f["action"] == "buy")
    sells_yes = sum(f["count"] for f in group if f["side"] == "yes" and f["action"] == "sell")
    buys_no = sum(f["count"] for f in group if f["side"] == "no" and f["action"] == "buy")
    sells_no = sum(f["count"] for f in group if f["side"] == "no" and f["action"] == "sell")
    # The side you're net-long is whichever you BOUGHT more of. Kalshi records
    # closing a position via the other outcome (a "sell YES" that actually closes
    # a NO), so a sell realizes at the DOMINANT side's price, not the fill's own
    # `side`. Realized P&L = sells (dominant price) + settlement revenue − buys.
    side = "yes" if buys_yes >= buys_no else "no"
    total_buy = sum(f["count"] * f["price"] for f in group if f["action"] == "buy")
    sell_cash = sum(f["count"] * (f["yes_price"] if side == "yes" else f["no_price"])
                    for f in group if f["action"] == "sell")
    sell_ct = sum(f["count"] for f in group if f["action"] == "sell")
    buy_cost = sum(f["count"] * f["price"] for f in group
                   if f["side"] == side and f["action"] == "buy")
    buy_ct = sum(f["count"] for f in group
                 if f["side"] == side and f["action"] == "buy")
    # Net contracts still held = dominant-side buys minus ALL sells. Kalshi records a
    # close on the OPPOSITE outcome (a YES position is closed via a 'sell NO'), so a
    # sell must reduce the position even though its own `side` differs from what you
    # hold — subtracting only same-side sells left a sold-out position looking open.
    qty = buy_ct - sell_ct
    entry = round(buy_cost / buy_ct, 4) if buy_ct else None
    settle_rev = settle.get("revenue") if settle else None
    # Exit = avg realized sell price (dominant side); else the settlement value
    # $1.00 (side won) / $0.00 (lost) for held-to-settlement; None while open.
    if sell_ct:
        exit_price = round(sell_cash / sell_ct, 4)
    elif settle:
        exit_price = 1.0 if settle["result"] == side else 0.0
    else:
        exit_price = None

    # Fees (per fill + the settlement) come straight out of realized P&L, so the
    # total matches Kalshi's actual account change (the user's net figure is
    # fee-inclusive).
    total_fee = sum(f.get("fee", 0) or 0 for f in group)
    if buy_ct and abs(qty) < 1e-6:
        # Closed early by selling the whole position before the market settled: the
        # P&L is realized from the sells (nothing was held to settlement). Kalshi STILL
        # returns a settlement record for such a market (revenue 0 — you held nothing
        # at expiry), so this MUST be checked before the settle branch below, or the
        # bet is mislabeled 'settled' with qty 0 (buy_ct - sell_ct) instead of 'sold'.
        # Also fixes a perpetual OPEN bet whose profit was missing from the totals
        # (Total % Gain / roi under-counted) when no settlement record exists.
        pnl = sell_cash - total_buy - total_fee
        status, result = "closed", None
        settled_ts = max(f["ts"] for f in group if f["action"] == "sell")
        qty = buy_ct   # net is 0 once closed; show the size actually traded
    elif settle:
        total_fee += settle.get("fee", 0) or 0
        pnl = sell_cash + (settle_rev or 0.0) - total_buy - total_fee
        status, result, settled_ts = "settled", settle["result"], settle["ts"]
    else:
        pnl, status, result, settled_ts = None, "open", None, None

    return {
        "ticker": ticker, "label": m.get("label", ticker),
        "variable": m.get("variable"), "floor": m.get("floor"),
        "cap": m.get("cap"), "strike_type": m.get("strike_type"),
        "side": side, "entry": entry, "exit": exit_price, "qty": qty,
        "first_ts": min(f["ts"] for f in group),
        "status": status, "result": result, "settled_ts": settled_ts,
        "pnl": pnl, "staked": total_buy,
        # Kalshi's own fees on this position (per fill, plus the settlement's).
        # Already subtracted from `pnl` — carried separately so a view can show
        # gross-from-prices against net, which is otherwise a silent gap between
        # what the Entry/Exit columns imply and what the P&L column says.
        "fee": total_fee,
    }


def _pnl_mtm(r: dict):
    """A bet's P&L: realized once settled/sold, else marked to market from its live
    `current_value` (None if an open bet has no live price yet)."""
    if r["status"] in ("settled", "closed"):
        return r["pnl"]
    if r.get("current_value") is not None and r["entry"] is not None:
        return r["qty"] * (r["current_value"] - r["entry"])
    return None


def summary(rows: list[dict]) -> dict:
    # Realized = held to settlement OR closed early by selling. Wins/losses/win-rate are
    # realized-only (an open bet hasn't won or lost yet). The %-gain figures, however, are
    # marked to market — they include open positions' live unrealized P&L (gain OR loss),
    # so they move with the market like Kalshi's portfolio total.
    realized = [r for r in rows if r["status"] in ("settled", "closed")]
    wins = sum(1 for r in realized if r["pnl"] > 0)
    losses = sum(1 for r in realized if r["pnl"] <= 0)
    graded = [r for r in rows if _pnl_mtm(r) is not None]   # realized + open marked-to-market
    net_pnl = sum(_pnl_mtm(r) for r in graded)
    staked = sum(r["staked"] for r in graded)
    annotated = [r for r in realized if r.get("agree") is not None]
    with_model = sum(1 for r in annotated if r["agree"])
    # Median of each bet's own % return — the typical trade, every bet counting
    # equally (unlike stake-weighted `roi`). The MEDIAN, not the mean: favorite-buying
    # pairs many small wins (~+5%) with rare -100% losses, and the mean of those washes
    # to ~0 even when you're net profitable. The median ignores that -100% tail and
    # reports what a typical trade actually returns.
    per_trade = [100.0 * _pnl_mtm(r) / r["staked"] for r in graded if r["staked"]]
    median_trade_return = median(per_trade) if per_trade else 0.0
    return {
        "n_settled": len(realized), "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / len(realized)) if realized else 0.0,
        "net_pnl": net_pnl, "realized_pnl": sum(r["pnl"] for r in realized),
        "staked": staked,
        "roi": (100.0 * net_pnl / staked) if staked else 0.0,
        "median_trade_return": median_trade_return,
        # Account growth: profit (realized + open marked to market) as a percent of the
        # starting bankroll (e.g. +$20 on a $10 start = +200%).
        "pct_gain": 100.0 * net_pnl / STARTING_BANKROLL if STARTING_BANKROLL else 0.0,
        "with_model_pct": (100.0 * with_model / len(annotated)) if annotated else None,
    }


def row_day(row: dict, today=None):
    """The WEATHER (target) day a row is filed under — the day its market is
    *about*, parsed from the ticker, with the settlement date as a fallback.

    THE one dating rule. Every view that shows or sums a day's P&L reads it here,
    because two rules is how a table and a chart come to disagree: dating rows by
    the fill instead put a bracket bought Aug 3 for the Aug 4 market in one group
    and counted it in the other. Kalshi temp markets also settle the NEXT morning
    (~1-2am, after the final CLI), so bucketing by settlement time plots every
    day's result a day late.

    `today` dates a still-open position whose ticker carries no readable date;
    without it such a row has no day at all."""
    parsed = _ticker_date(row.get("ticker"))
    if parsed:
        return date.fromisoformat(parsed)
    settled = row.get("settled_ts")
    if settled:
        return settled.date()
    return today if row.get("status") == "open" else None


def daily_pnl(rows: list[dict], today=None) -> dict:
    """{weather day: [realized, unrealized]} across `rows`.

    The single aggregation behind the equity curve, the per-period tables and the
    reconciliation view, so those three cannot drift apart. Open positions are
    marked to market and counted on THEIR OWN day, not on today: a bracket bought
    for tomorrow belongs to tomorrow. A row with no P&L to state (open, no live
    bid) is skipped rather than counted flat."""
    out: dict = {}
    for r in rows:
        pnl = _pnl_mtm(r)
        if pnl is None:
            continue
        day = row_day(r, today)
        if day is None:
            continue
        realized = r["status"] in ("settled", "closed")
        bucket = out.setdefault(day, [0.0, 0.0])
        bucket[0 if realized else 1] += pnl
    return out


def curve(rows: list[dict], today=None, base: float = 0.0) -> list[dict]:
    """Cumulative P&L by weather day, oldest first, running from `base`.

    One point per day with trades: `total` (running), `unrealized` (how much of
    that day's step is still a live mark) and `open` (whether any of it is) — the
    last two are what let a chart draw the unrealized stretch dashed instead of
    implying the money is banked. Led by an ANCHOR at `base` dated the day before
    the first trade, so a single trading day still has a slope to read.

    `base` is the only difference between the two pages' lines: the account
    balance for History, $0 for the Screen's earnings."""
    daily = daily_pnl(rows, today)
    out, total = [], base
    for d in sorted(daily):
        realized, unreal = daily[d]
        total += realized + unreal
        out.append({"date": d, "total": total, "unrealized": unreal,
                    "open": unreal != 0.0})
    if out:
        out = [{"date": out[0]["date"] - timedelta(days=1), "total": base,
                "unrealized": 0.0, "open": False}] + out
    return out


def with_steps(points: list[dict]) -> list[dict]:
    """`points` with each one's own day's gain added as `step`.

    A line only ever shows a running total, so reading what a day contributed
    means subtracting two points by eye — and then arguing with a table about the
    result. State the step instead.

    Fills in `unrealized`/`open` when a caller hands over a bare {date, total}
    curve, so a chart can encode those fields unconditionally. The first point has
    no prior day, so its step is 0 — with the anchor leading a curve, that is the
    opening balance, which nothing was won or lost to reach."""
    out, prev = [], None
    for p in points:
        out.append({"unrealized": 0.0, "open": False, **p,
                    "step": round(p["total"] - prev, 4)
                    if prev is not None else 0.0})
        prev = p["total"]
    return out


def line_parts(points: list[dict]):
    """(realized stretch, unrealized stretch) for two line layers.

    Split at the LAST fully-realized point; everything after it is a live mark and
    should be drawn dashed. The stretches share that point so the dashes continue
    the line rather than starting after a gap, and the anchor is never open, so a
    split point always exists. Once one day's step is a mark, every cumulative
    total past it is one too — which is why the tail runs to the end."""
    last_real = 0
    for i, p in enumerate(points):
        if not p.get("open"):
            last_real = i
    return points[:last_real + 1], points[last_real:]


def day_breakdown(rows: list[dict], today=None) -> list[dict]:
    """Every day's number with the trades behind it — newest day first.

    Instrumentation, kept on the page. Two rounds of "the chart says −11¢, the
    table says +5¢" were argued from arithmetic done by eye, without the one thing
    that settles it: each trade's gross price move, the fees Kalshi actually took,
    and whether the curve could place the trade at all.

    Per day: `step` (what the chart draws), `subtotal` (its trades summed — equal
    to `step` unless something is wrong), `on_chart` (False when no day could be
    determined), and per trade `gross` = net + fee, so a hand-count from the
    Entry/Exit columns has somewhere to land."""
    steps = {p["date"]: p["step"] for p in with_steps(curve(rows, today))}
    by_day: dict = {}
    for r in rows:
        net = _pnl_mtm(r)
        if net is None:
            continue
        realized = r["status"] in ("settled", "closed")
        fee = r.get("fee") or 0.0
        by_day.setdefault(row_day(r, today), []).append({
            "ticker": r.get("ticker"), "label": r.get("label"),
            "status": r.get("status"), "realized": realized,
            "qty": r.get("qty"), "entry": r.get("entry"),
            "exit": r.get("current_value") if not realized else r.get("exit"),
            "staked": r.get("staked"), "fee": fee, "net": net,
            # What the prices alone imply, before Kalshi's cut — the number a
            # hand-count from Entry/Exit produces.
            "gross": round(net + fee, 4),
            "first_ts": r.get("first_ts"),
        })
    out = []
    for day in sorted(by_day, key=lambda d: (d is not None, d), reverse=True):
        trades = by_day[day]
        out.append({
            "day": day,
            "on_chart": day in steps,
            "step": steps.get(day),
            "subtotal": round(sum(t["net"] for t in trades), 4),
            "fees": round(sum(t["fee"] for t in trades), 4),
            "trades": sorted(trades, key=lambda t: t["first_ts"] or 0),
        })
    return out


def equity_curve(rows: list[dict]) -> list[dict]:
    """Cumulative REALIZED P&L by weather day, from the starting bankroll — the
    settled-and-sold line, with no open marks in it. `equity_curve_live` is the one
    the History page draws; this is kept for callers that want banked money only."""
    realized = [r for r in rows if r["status"] in ("settled", "closed")]
    return [p for p in curve(realized, base=STARTING_BANKROLL)][1:]  # no anchor


def open_unrealized(rows: list[dict]) -> float:
    """Total live unrealized P&L of OPEN positions: qty × (current market value −
    entry). Rows must carry `current_value` (the live bid); rows without it are
    skipped."""
    return sum(r["qty"] * (r["current_value"] - r["entry"]) for r in rows
               if r["status"] == "open" and r.get("current_value") is not None
               and r["entry"] is not None)


def equity_curve_live(rows: list[dict], today) -> list[dict]:
    """The line the History page draws: account balance by weather day, from the
    starting bankroll, with open positions marked to market ON THEIR OWN DAY and an
    opening-balance anchor the day before the first trade.

    Open marks used to be summed into a single point dated `today`. That put them
    on a different day from the Daily table, which has always bucketed by weather
    day — so the chart and the table disagreed about a bracket bought for tomorrow,
    and neither said why. One aggregation now feeds both (`daily_pnl`)."""
    return curve(rows, today, base=STARTING_BANKROLL)


def period_table(rows: list[dict], period: str) -> list[dict]:
    """P&L aggregated by 'day', 'week' (Monday-start), or 'month'. One entry per period,
    oldest→newest: {label(date), pct = period gain / period staked, port_pct = period gain
    / portfolio balance ENTERING the period (a per-period return on the whole account),
    gain ($), total = running end-of-period balance from the $STARTING_BANKROLL base}. Dated by the
    WEATHER day (same as the equity curve). Includes realized bets (settled or sold) AND
    open positions marked to market (via `_pnl_mtm`), so today's still-open trades show up
    as the current period — an open bet with no live price yet is skipped.

    Dated through `row_day`, the same rule the equity curve uses, so a period's
    gain is exactly the sum of that period's steps on the line."""
    def bucket(d: date) -> date:
        if period == "week":
            return d - timedelta(days=d.weekday())
        if period == "month":
            return date(d.year, d.month, 1)
        return d

    agg: dict = {}
    for r in rows:
        pnl = _pnl_mtm(r)
        if pnl is None:                              # open with no live price -> can't place
            continue
        d = row_day(r)
        if d is None:                                # open + unparsable ticker -> can't date
            continue
        b = agg.setdefault(bucket(d), [0.0, 0.0])   # [gain, staked]
        b[0] += pnl
        b[1] += r["staked"]
    out, total = [], STARTING_BANKROLL
    for k in sorted(agg):
        gain, staked = agg[k]
        start = total                                # portfolio balance entering the period
        total += gain
        out.append({"label": k, "pct": (gain / staked) if staked else 0.0,
                    "port_pct": (gain / start) if start else 0.0,
                    "gain": gain, "total": total})
    return out


def period_summary(entries: list[dict], pct_gain: float) -> dict | None:
    """Per-tab summary stats over the `period_table` output. `entries` is that list
    ({label, pct, gain, total}); `pct_gain` is the marked-to-market total % from
    `summary()`, passed straight through so the "Portfolio %" card matches the
    top-of-page metric. Cards 1-5 are realized-only (like `entries` itself); a period
    is "green" only when gain > 0 (a flat $0 counts as not-green, matching the
    losses = pnl <= 0 convention in `summary`). Returns None for an empty table."""
    if not entries:
        return None
    gains = [e["gain"] for e in entries]
    n = len(entries)
    green = sum(1 for g in gains if g > 0)
    return {
        "count": n,
        "avg_gain": sum(gains) / n,
        "avg_pct": sum(e["pct"] for e in entries) / n,
        # Unweighted mean of each period's return on the WHOLE account (port_pct),
        # parallel to avg_pct but on the full portfolio rather than just what was staked.
        "avg_port_pct": sum(e.get("port_pct", 0.0) for e in entries) / n,
        "green_count": green,
        "green_rate": green / n,
        "best_gain": max(gains),
        "worst_gain": min(gains),
        "pct_gain": pct_gain,
    }


def _phi(x: float, mu: float, sigma: float) -> float:
    """Normal CDF Φ((x−mu)/sigma) via erf (no scipy dependency)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _contract_yes_prob(consensus, sigma, floor, cap, strike_type):
    """Model P(contract settles YES) under N(consensus, sigma), with a ±0.5°F
    continuity correction. Returns None when a bound the strike needs is missing."""
    if strike_type == "greater":
        if floor is None:
            return None
        return 1.0 - _phi(floor - 0.5, consensus, sigma)
    if strike_type == "less":
        if cap is None:
            return None
        return _phi(cap + 0.5, consensus, sigma)
    if floor is None or cap is None:
        return None
    return _phi(cap + 0.5, consensus, sigma) - _phi(floor - 0.5, consensus, sigma)


def _nearest(fill_ts, variable, betting_rows, consensus_rows, tol_min, day):
    """(consensus, sigma_or_None) of the snapshot nearest fill_ts for this
    (day, variable), preferring betting_log (has sigma); None if none within tol.
    Snapshots with a None consensus value are skipped."""
    best, best_gap = None, tol_min * 60 + 1
    for r in betting_rows:
        if (r.get("target_date") != day or r.get("variable") != variable
                or r.get("cli_consensus") is None):
            continue
        gap = abs((datetime.fromisoformat(r["captured_at"]) - fill_ts).total_seconds())
        if gap <= tol_min * 60 and gap < best_gap:
            best, best_gap = (r["cli_consensus"], r.get("sigma_used")), gap
    if best is not None:
        return best
    for r in consensus_rows:
        if (r.get("target_date") != day or r.get("variable") != variable
                or r.get("basis") != "cli" or r.get("consensus") is None):
            continue
        gap = abs((datetime.fromisoformat(r["captured_at"]) - fill_ts).total_seconds())
        if gap <= tol_min * 60 and gap < best_gap:
            best, best_gap = (r["consensus"], None), gap
    return best


def model_at_bet(fill_ts, variable, floor, cap, strike_type, side, entry,
                 betting_rows, consensus_rows, calib, tol_min=45, target_date=None):
    day = target_date or fill_ts.date().isoformat()
    snap = _nearest(fill_ts, variable, betting_rows, consensus_rows, tol_min, day)
    if snap is None:
        return (None, None, None)
    consensus, sigma = snap
    if sigma is None:
        sigma = ((calib or {}).get("sigma", {}) or {}).get(variable)
    if not sigma or consensus is None:
        return (None, None, None)
    yes_p = _contract_yes_prob(consensus, sigma, floor, cap, strike_type)
    if yes_p is None:
        return (None, None, None)
    yes_p = min(max(yes_p, 0.0), 1.0)
    side_p = yes_p if side == "yes" else 1.0 - yes_p
    edge = side_p - entry if entry is not None else None
    return (side_p, edge, (edge > 0) if edge is not None else None)


def _ticker_date(ticker):
    """Event date (ISO) parsed from a Kalshi ticker like 'KXHIGHTDAL-26JUN22-B97',
    or None if it can't be parsed."""
    try:
        return datetime.strptime(ticker.split("-")[1], "%y%b%d").date().isoformat()
    except (IndexError, ValueError):
        return None


def _station_by_series():
    """{series_prefix -> station_code} built from config, e.g. 'KXHIGHTDAL' -> 'KDFW',
    'KXHIGHAUS' -> 'KAUS'. Read from config (not hardcoded) so a third city's markets
    are attributable the moment it's added there."""
    import config
    out = {}
    for code in config.STATION_CODES:
        s = config.station(code)
        out[s.kalshi_high_series] = code
        out[s.kalshi_low_series] = code
    return out


def ticker_station(ticker: str) -> str | None:
    """Station code (e.g. 'KDFW'/'KAUS') a Kalshi ticker belongs to, by matching its
    series prefix (the part before the first '-'). None for an unrecognized prefix —
    such a bet then shows only in the combined ('Both') view, never in a single city."""
    prefix = (ticker or "").split("-")[0]
    return _station_by_series().get(prefix)


def filter_by_station(rows: list[dict], codes: list[str]) -> list[dict]:
    """The subset of `rows` whose ticker belongs to one of `codes`. Order-preserving,
    so the newest-first sort from build_rows carries through. Passing every station
    code returns every recognized row (unrecognized-prefix rows are dropped)."""
    keep = set(codes)
    return [r for r in rows if ticker_station(r["ticker"]) in keep]


def annotate_rows(rows, betting_rows, consensus_rows, calib) -> None:
    for r in rows:
        p, edge, agree = model_at_bet(
            r["first_ts"], r["variable"], r["floor"], r["cap"],
            r["strike_type"], r["side"], r["entry"],
            betting_rows, consensus_rows, calib,
            target_date=_ticker_date(r["ticker"]))
        r["model_prob"], r["edge"], r["agree"] = p, edge, agree


def fetch_rows(start: date) -> list[dict]:
    """Live bet rows straight from the Kalshi portfolio API since `start`,
    annotated with 'target_date' (ISO string — the WEATHER day the ticker
    settles on) for per-day attribution. The single shared builder for the
    Morning Recap, the portfolio-value card and the Journal page. Lazy import:
    the Kalshi client needs `cryptography`, which local test envs lack. Raises
    on missing creds or network failure — callers decide best-effort."""
    from sources import kalshi_portfolio
    fills = kalshi_portfolio.fills(start)
    setts = kalshi_portfolio.settlements(start)
    meta = {t: kalshi_portfolio.market_meta(t) for t in {f["ticker"] for f in fills}}
    rows = build_rows(fills, setts, meta)
    for r in rows:
        r["target_date"] = _ticker_date(r["ticker"])
    return rows
