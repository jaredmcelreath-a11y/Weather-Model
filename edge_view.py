"""Edge Tracker page — forecast edge vs. the Kalshi market (from the betting-time
log) plus realized-edge P&L attribution (from your actual bets).

Two independent sections so one failing does not blank the other:
  A. Forecast edge — model vs. market at each betting slot, scored once settled.
  B. Realized edge — your bets split into with-market (bought the favorite) vs.
     against-market (bought the underdog), each with net P&L.
"""
from __future__ import annotations

import streamlit as st

import betting_log
import edge_report
import market_view
import settlements


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


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _offset_verdict(metrics: dict) -> list[str]:
    """One line per slot for the HIGH variable's 'all' subset, comparing the flat
    +0.9 settlement offset against the live-gap predictor (RMSE, lower better) —
    the one real edge lever. Low has no offset predictor, so it is skipped."""
    out = []
    for (slot, variable, subset), m in sorted(metrics.items()):
        if variable != "high" or subset != "all":
            continue
        if m.get("live_rmse") is None or m.get("flat_rmse") is None:
            continue
        verdict = ("live gap beats flat" if m["flat_rmse"] - m["live_rmse"] >= 0.15
                   else "no clear offset edge")
        out.append(f"{slot}: flat RMSE {m['flat_rmse']} vs live RMSE {m['live_rmse']} "
                   f"({verdict}); flips toward {m['flip_toward']} / away {m['flip_away']}")
    return out


def _edge_rows(metrics: dict) -> list[dict]:
    """Flatten metrics {(slot, variable, subset): stats} into display rows,
    boundary-first within each (slot, variable)."""
    order = {"boundary": 0, "all": 1, "mid_bin": 2}
    rows = []
    for (slot, variable, subset), m in sorted(
            metrics.items(), key=lambda kv: (kv[0][0], kv[0][1], order.get(kv[0][2], 9))):
        vol = m.get("market_volume")
        rows.append({
            "Slot": slot, "Variable": variable,
            "Day Type": ("⚠ " if m.get("thin") else "") + subset.replace("_", "-"),
            "Number": m["n"],
            "Model MAE": m["model_mae"], "Market MAE": m["market_mae"],
            "Volume": "—" if vol is None else f"{vol:g}",
            "Disagree": m["disagreements"],
            "Model Won": m["model_bin_wins"], "Market Won": m["market_bin_wins"],
        })
    return rows


def render():
    import pandas as pd

    market_view._inject_theme(market_view._seed_theme())
    st.title("Edge")

    # --- Part A: forecast edge vs. market (needs no credentials) ---
    st.subheader("Forecast Edge vs. Market")
    st.caption(
        "At each betting slot the model's consensus and the live Kalshi price are "
        "logged; once the day settles we score which was closer. The rows that "
        "matter are **boundary** days — consensus near a Kalshi bin edge — where a "
        "small error flips the bet.")
    try:
        rows = betting_log.load()
        data = assemble(rows, settlements.as_map("cli"), settlements.as_map("hourly"))
    except Exception:
        data = {"headline": {"n": 0}, "metrics": {}}
    head = data["headline"]
    if not head.get("n"):
        st.info("Accumulating — no settled betting-time rows yet. This fills in as "
                "days settle (one day's lead after each slot).")
    else:
        with st.container(key="metrics2_edge_a"):
            c = st.columns(4)
        c[0].markdown(market_view.metric_card("Settled slots", str(head["n"])),
                      unsafe_allow_html=True)
        c[1].markdown(market_view.metric_card(
            "Disagreements", str(head["disagreements"]),
            "Days the model and market pointed at different bins."),
            unsafe_allow_html=True)
        c[2].markdown(market_view.metric_card(
            "Model won", f"{head['model_wins']} ({_pct(head['model_wins'], head['disagreements'])})",
            "Of the disagreements, how often the model's bin was the settled one."),
            unsafe_allow_html=True)
        c[3].markdown(market_view.metric_card(
            "Market won", f"{head['market_wins']} ({_pct(head['market_wins'], head['disagreements'])})"),
            unsafe_allow_html=True)
        market_view._html_table(pd.DataFrame(_edge_rows(data["metrics"])))
        st.caption("Lower **MAE** (mean absolute error, °F) is the sharper forecast. "
                   "When the two disagree on the bin, **model won / market won** is who "
                   "the settlement proved right. Both sides are scored by where their "
                   "expected value lands. A ⚠ marks a thin-market subset (low traded "
                   "volume), where the market's 'opinion' is weak.")
        for line in _offset_verdict(data["metrics"]):
            st.caption("Settlement offset — " + line)

    # --- Part B: realized edge / P&L attribution (needs the [kalshi] secret) ---
    st.markdown("---")
    st.subheader("My Realized Edge")
    st.caption("Your settled bets split by the price you paid: **with-market** means "
               "you bought the favorite (entry ≥ 50¢); **against-market** means you "
               "bought the underdog. Against-market profit is edge the market didn't see.")
    import bet_view
    from sources import kalshi_auth
    try:
        bet_rows, _summ, _curve, _bal = bet_view._load_bets()
    except kalshi_auth.KalshiCredentialsError:
        st.info("Add your Kalshi API key to the app secrets (`[kalshi]`) to see "
                "realized-edge attribution.")
        return
    except Exception:
        st.warning("Couldn't load your Kalshi bets right now; the forecast-edge "
                   "section above is unaffected.")
        return

    attr = pnl_attribution(bet_rows)
    wm, am = attr["with_market"], attr["against_market"]
    with st.container(key="metrics2_edge_b"):
        c = st.columns(2)
    c[0].markdown(market_view.metric_card(
        "Against-market P&L", f"${am['net_pnl']:+.2f}",
        f"{am['wins']}–{am['losses']} on underdog bets — your true edge."),
        unsafe_allow_html=True)
    c[1].markdown(market_view.metric_card(
        "With-market P&L", f"${wm['net_pnl']:+.2f}",
        f"{wm['wins']}–{wm['losses']} riding the favorite."),
        unsafe_allow_html=True)
    market_view._html_table(pd.DataFrame([
        {"Bet Type": "against-market (underdog)", "Number": am["n"],
         "Wins": am["wins"], "Losses": am["losses"], "Net P&L": f"${am['net_pnl']:+.2f}"},
        {"Bet Type": "with-market (favorite)", "Number": wm["n"],
         "Wins": wm["wins"], "Losses": wm["losses"], "Net P&L": f"${wm['net_pnl']:+.2f}"},
    ]))
