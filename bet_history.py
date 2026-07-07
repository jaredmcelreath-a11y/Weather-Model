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
        cash_flow = sum((f["count"] * f["price"]) * (1 if f["action"] == "sell" else -1)
                        for f in group)
        net_yes, net_no = buys_yes - sells_yes, buys_no - sells_no
        side = "yes" if net_yes >= net_no else "no"
        qty = net_yes if side == "yes" else net_no
        buy_cost = sum(f["count"] * f["price"] for f in group
                       if f["side"] == side and f["action"] == "buy")
        buy_ct = sum(f["count"] for f in group
                     if f["side"] == side and f["action"] == "buy")
        entry = round(buy_cost / buy_ct, 4) if buy_ct else None

        settle = settlements.get(ticker)
        if settle:
            payout = net_yes if settle["result"] == "yes" else net_no
            pnl = cash_flow + payout
            status, result, settled_ts = "settled", settle["result"], settle["ts"]
        else:
            pnl, status, result, settled_ts = None, "open", None, None

        rows.append({
            "ticker": ticker, "label": m.get("label", ticker),
            "variable": m.get("variable"), "floor": m.get("floor"),
            "cap": m.get("cap"), "strike_type": m.get("strike_type"),
            "side": side, "entry": entry, "qty": qty,
            "first_ts": min(f["ts"] for f in group),
            "status": status, "result": result, "settled_ts": settled_ts,
            "pnl": pnl, "staked": buy_cost,
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
        "with_model_pct": (100.0 * with_model / len(annotated)) if annotated else None,
    }


def equity_curve(rows: list[dict]) -> list[dict]:
    settled = sorted((r for r in rows if r["status"] == "settled"),
                     key=lambda r: r["settled_ts"])
    out, total = [], 0.0
    for r in settled:
        total += r["pnl"]
        out.append({"date": r["settled_ts"].date(), "total": total})
    return out


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
