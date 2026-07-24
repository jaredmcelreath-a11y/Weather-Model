"""Streamlit control page for the autonomous trader.

The ONLY writer of trade_state.json (the cron reads it). Exposes the kill switch,
shadow/live mode, and every parameter as live controls, plus a live positions
panel and the recent decision log. Pure display shaping lives in summarize_log so
it is testable without Streamlit. Uses market_view's themed, mobile-friendly table
helper (.wtbl-wrap scrolls horizontally on phones) and its theme injection so the
page matches the rest of the dashboard.
"""
from __future__ import annotations

import json

import trade_params
import trade_state


def summarize_log(records: list[dict], limit: int = 20) -> list[dict]:
    """Newest-first, capped view rows for the decision log."""
    rows = sorted(records, key=lambda r: r.get("ts", ""), reverse=True)[:limit]
    return [{"ts": r.get("ts", ""), "kind": r.get("kind", ""),
             "ticker": r.get("ticker", ""), "reason": r.get("reason", "")}
            for r in rows]


def _fmt_ts(ts: str) -> str:
    """ISO timestamp -> compact 'MM-DD HH:MM' for narrow screens."""
    return ts[5:16].replace("T", " ") if len(ts) >= 16 else ts


def render() -> None:
    import streamlit as st

    import market_view
    market_view._theme_controls()   # inject theme CSS (+ mobile .wtbl-wrap) + Settings

    st.markdown("## Autonomous Trader")
    params = trade_state.load_state()

    # --- Master switches ---
    killed = st.toggle("Kill Switch (Engaged = No Trading)",
                       value=params["kill_switch"])
    mode = st.radio("Mode", ["shadow", "live"],
                    index=0 if params["mode"] == "shadow" else 1,
                    horizontal=True, format_func=str.capitalize)
    if mode == "live" and not killed:
        st.warning("⚠️ Live And Armed — Real Orders Will Be Placed.")
    elif killed:
        st.info("Kill Switch Engaged — No Orders Will Be Placed.")

    # --- Parameters ---
    with st.expander("Parameters", expanded=False):
        params["min_resolved"] = st.slider("Min Resolved", 0.0, 1.0,
                                            float(params["min_resolved"]), 0.05)
        params["agreement_tol"] = st.slider("Agreement Tol (°F)", 0.0, 3.0,
                                             float(params["agreement_tol"]), 0.5)
        params["max_price"] = st.slider("Max Price", 0.5, 0.99,
                                        float(params["max_price"]), 0.01)
        params["per_market_cap"] = st.number_input(
            "Per-Market Cap ($)", value=float(params["per_market_cap"]), step=0.25)
        params["stop_loss"] = st.slider("Stop-Loss (Ask Drop)", 0.05, 0.5,
                                        float(params["stop_loss"]), 0.05)
        params["kelly_fraction"] = st.slider("Kelly Fraction", 0.05, 1.0,
                                             float(params["kelly_fraction"]), 0.05)

    if st.button("Save Settings", use_container_width=True):
        params["kill_switch"] = killed
        params["mode"] = mode
        try:
            trade_state.save_state(trade_params.merge_params(params))
            st.success("Saved.")
        except Exception as e:
            st.error(f"Save Failed: {e}")

    _render_positions(st, market_view)
    _render_log(st)


def _render_positions(st, market_view) -> None:
    import pandas as pd
    from sources import kalshi_portfolio

    st.markdown("### Open Positions")
    try:
        pos = kalshi_portfolio.positions()
        bal = kalshi_portfolio.balance()
    except Exception as e:
        st.info(f"Positions Unavailable: {e}")
        return
    st.caption(f"Cash Balance: ${bal:.2f}" if bal is not None else "Balance Unavailable")
    if not pos:
        st.caption("No Open Positions.")
        return
    df = pd.DataFrame([{"Ticker": p["ticker"], "Side": p["side"].capitalize(),
                        "Count": p["count"]} for p in pos])
    market_view._html_table(df)


def _render_log(st) -> None:
    st.markdown("### Recent Decisions")
    try:
        raw = trade_state.GitHubTransport().get(trade_state.LOG_PATH)
        lines = raw[0].splitlines() if raw else []
        records = [json.loads(x) for x in lines if x.strip()]
    except Exception as e:
        st.info(f"Log Unavailable: {e}")
        return
    if not records:
        st.caption("No Decisions Logged Yet.")
        return
    for r in summarize_log(records):
        parts = [f"**{r['kind'].title()}**", _fmt_ts(r["ts"])]
        if r["ticker"]:
            parts.append(r["ticker"])
        if r["reason"]:
            parts.append(r["reason"])
        st.caption(" · ".join(parts))
