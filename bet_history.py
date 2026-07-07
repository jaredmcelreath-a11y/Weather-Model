"""Assemble the user's Kalshi fills + settlements into per-market bet rows, with
realized P&L, summary stats, and the cumulative equity curve. Pure functions over
the normalized dicts from sources.kalshi_portfolio — no network, no Streamlit.

Model-at-bet-time annotation lives in the same module (added in Task 4) but is a
separate pass (annotate_rows) so assembly stays model-free.
"""
from __future__ import annotations

import math
from datetime import date, datetime

BETS_START = date(2026, 6, 22)
# Starting bankroll ($): the equity curve's baseline and the "Total % Gain"
# denominator (net realized profit as a percent of this). Change this one value if
# the real starting figure differs.
STARTING_BANKROLL = 10.0


def build_rows(fills: list[dict], settlements: dict, meta: dict) -> list[dict]:
    by_ticker: dict[str, list] = {}
    for f in fills:
        by_ticker.setdefault(f["ticker"], []).append(f)

    rows = []
    for ticker, group in by_ticker.items():
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
        net_yes, net_no = buys_yes - sells_yes, buys_no - sells_no
        qty = net_yes if side == "yes" else net_no
        buy_cost = sum(f["count"] * f["price"] for f in group
                       if f["side"] == side and f["action"] == "buy")
        buy_ct = sum(f["count"] for f in group
                     if f["side"] == side and f["action"] == "buy")
        entry = round(buy_cost / buy_ct, 4) if buy_ct else None
        settle = settlements.get(ticker)
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
        if settle:
            total_fee += settle.get("fee", 0) or 0
            pnl = sell_cash + (settle_rev or 0.0) - total_buy - total_fee
            status, result, settled_ts = "settled", settle["result"], settle["ts"]
        else:
            pnl, status, result, settled_ts = None, "open", None, None

        rows.append({
            "ticker": ticker, "label": m.get("label", ticker),
            "variable": m.get("variable"), "floor": m.get("floor"),
            "cap": m.get("cap"), "strike_type": m.get("strike_type"),
            "side": side, "entry": entry, "exit": exit_price, "qty": qty,
            "first_ts": min(f["ts"] for f in group),
            "status": status, "result": result, "settled_ts": settled_ts,
            "pnl": pnl, "staked": total_buy,
        })
    rows.sort(key=lambda r: r["first_ts"], reverse=True)  # newest first
    return rows


def summary(rows: list[dict]) -> dict:
    settled = [r for r in rows if r["status"] == "settled"]
    wins = sum(1 for r in settled if r["pnl"] > 0)
    losses = sum(1 for r in settled if r["pnl"] <= 0)
    net_pnl = sum(r["pnl"] for r in settled)
    staked = sum(r["staked"] for r in settled)
    annotated = [r for r in settled if r.get("agree") is not None]
    with_model = sum(1 for r in annotated if r["agree"])
    return {
        "n_settled": len(settled), "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / len(settled)) if settled else 0.0,
        "net_pnl": net_pnl, "staked": staked,
        "roi": (100.0 * net_pnl / staked) if staked else 0.0,
        # Account growth: net realized profit as a percent of the starting bankroll
        # (e.g. +$20 on a $10 start = +200%).
        "pct_gain": 100.0 * net_pnl / STARTING_BANKROLL if STARTING_BANKROLL else 0.0,
        "with_model_pct": (100.0 * with_model / len(annotated)) if annotated else None,
    }


def equity_curve(rows: list[dict]) -> list[dict]:
    """Cumulative realized P&L, one point per SETTLEMENT DAY (end-of-day running
    total). Same-day bets are summed into a single point, so the line advances one
    step per day instead of jumping vertically when several bets settle at once."""
    daily: dict = {}
    for r in rows:
        if r["status"] == "settled":
            d = r["settled_ts"].date()
            daily[d] = daily.get(d, 0.0) + r["pnl"]
    out, total = [], STARTING_BANKROLL   # curve tracks account balance, not P&L from 0
    for d in sorted(daily):
        total += daily[d]
        out.append({"date": d, "total": total})
    return out


def open_unrealized(rows: list[dict]) -> float:
    """Total live unrealized P&L of OPEN positions: qty × (current market value −
    entry). Rows must carry `current_value` (the live bid); rows without it are
    skipped."""
    return sum(r["qty"] * (r["current_value"] - r["entry"]) for r in rows
               if r["status"] == "open" and r.get("current_value") is not None
               and r["entry"] is not None)


def equity_curve_live(rows: list[dict], today) -> list[dict]:
    """The realized `equity_curve`, extended with a final LIVE point at `today` that
    adds open positions' current unrealized P&L — so the line reflects live
    mark-to-market and moves as bids change. If `today` already has a realized point,
    the unrealized is folded into it rather than duplicating the x."""
    curve = equity_curve(rows)
    unreal = open_unrealized(rows)
    if not curve and not unreal:
        return curve
    if curve and curve[-1]["date"] == today:
        return curve[:-1] + [{"date": today, "total": curve[-1]["total"] + unreal}]
    base = curve[-1]["total"] if curve else STARTING_BANKROLL
    return curve + [{"date": today, "total": base + unreal}]


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


def annotate_rows(rows, betting_rows, consensus_rows, calib) -> None:
    for r in rows:
        p, edge, agree = model_at_bet(
            r["first_ts"], r["variable"], r["floor"], r["cap"],
            r["strike_type"], r["side"], r["entry"],
            betting_rows, consensus_rows, calib,
            target_date=_ticker_date(r["ticker"]))
        r["model_prob"], r["edge"], r["agree"] = p, edge, agree
