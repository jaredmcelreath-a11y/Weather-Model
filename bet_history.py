"""Assemble the user's Kalshi fills + settlements into per-market bet rows, with
realized P&L, summary stats, and the cumulative equity curve. Pure functions over
the normalized dicts from sources.kalshi_portfolio — no network, no Streamlit.

Model-at-bet-time annotation lives in the same module (added in Task 4) but is a
separate pass (annotate_rows) so assembly stays model-free.
"""
from __future__ import annotations

from datetime import date

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
