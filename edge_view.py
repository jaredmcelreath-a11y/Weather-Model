"""Edge Tracker page — forecast edge vs. the Kalshi market (from the betting-time
log) plus realized-edge P&L attribution (from your actual bets).

Two independent sections so one failing does not blank the other:
  A. Forecast edge — model vs. market at each betting slot, scored once settled.
  B. Realized edge — your bets split into with-market (bought the favorite) vs.
     against-market (bought the underdog), each with net P&L.
"""
from __future__ import annotations

import edge_report


def assemble(betting_rows: list[dict], cli_map: dict, hourly_map: dict) -> dict:
    """Join betting-log rows to settlements and compute the forecast-edge metrics
    (edge_report.metrics), plus a headline roll-up summed across the 'all' subset
    of every (slot, variable) group. Empty/unsettled input -> zeroed headline,
    empty metrics."""
    joined = edge_report.join(betting_rows, cli_map, hourly_map)
    metrics = edge_report.metrics(joined)
    head = {"n": 0, "disagreements": 0, "model_wins": 0, "market_wins": 0}
    for (_slot, _var, subset), m in metrics.items():
        if subset != "all":
            continue
        head["n"] += m["n"]
        head["disagreements"] += m["disagreements"]
        head["model_wins"] += m["model_bin_wins"]
        head["market_wins"] += m["market_bin_wins"]
    return {"metrics": metrics, "headline": head}


def pnl_attribution(bet_rows: list[dict]) -> dict:
    """Split realized bets by entry price: with-market (entry >= 0.50, you bought
    the market favorite) vs against-market (entry < 0.50, you bought the underdog).
    Realized = settled or closed; open bets and rows without an entry are skipped.
    Returns {bucket: {n, wins, losses, net_pnl}} with net_pnl rounded to cents."""
    buckets = {
        "with_market": {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
        "against_market": {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
    }
    for r in bet_rows:
        if r.get("status") not in ("settled", "closed"):
            continue
        entry = r.get("entry")
        if entry is None:
            continue
        b = buckets["with_market" if entry >= 0.50 else "against_market"]
        b["n"] += 1
        pnl = r.get("pnl") or 0.0
        b["wins" if pnl > 0 else "losses"] += 1
        b["net_pnl"] += pnl
    for b in buckets.values():
        b["net_pnl"] = round(b["net_pnl"], 2)
    return buckets
