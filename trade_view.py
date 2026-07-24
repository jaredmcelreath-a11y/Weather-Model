"""Streamlit control page for the autonomous trader.

The ONLY writer of trade_state.json (the cron reads it). Exposes the kill switch,
shadow/live mode, and every parameter as live controls, plus a live positions
panel and the recent decision log. Pure display shaping lives in summarize_log so
it is testable without Streamlit.
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


def render() -> None:
    import streamlit as st

    st.markdown("## Autonomous Trader")
    params = trade_state.load_state()

    # --- Master switches ---
    killed = st.toggle("Kill switch (engaged = no trading)",
                       value=params["kill_switch"])
    mode = st.radio("Mode", ["shadow", "live"],
                    index=0 if params["mode"] == "shadow" else 1, horizontal=True)
    if mode == "live" and not killed:
        st.warning("⚠️ LIVE and armed — real orders will be placed.")
    elif killed:
        st.info("Kill switch engaged — no orders will be placed.")

    # --- Parameters ---
    with st.expander("Parameters", expanded=False):
        params["min_resolved"] = st.slider("Min resolved", 0.0, 1.0,
                                            float(params["min_resolved"]), 0.05)
        params["agreement_tol"] = st.slider("Agreement tol (°F)", 0.0, 3.0,
                                             float(params["agreement_tol"]), 0.5)
        params["max_price"] = st.slider("Max price", 0.5, 0.99,
                                        float(params["max_price"]), 0.01)
        params["per_market_cap"] = st.number_input(
            "Per-market cap ($)", value=float(params["per_market_cap"]), step=0.25)
        params["stop_loss"] = st.slider("Stop-loss (ask drop)", 0.05, 0.5,
                                        float(params["stop_loss"]), 0.05)
        params["kelly_fraction"] = st.slider("Kelly fraction", 0.05, 1.0,
                                             float(params["kelly_fraction"]), 0.05)

    if st.button("Save settings"):
        params["kill_switch"] = killed
        params["mode"] = mode
        try:
            trade_state.save_state(trade_params.merge_params(params))
            st.success("Saved.")
        except Exception as e:
            st.error(f"Save failed: {e}")

    _render_positions(st)
    _render_log(st)


def _render_positions(st) -> None:
    from sources import kalshi_portfolio
    st.markdown("### Open positions")
    try:
        pos = kalshi_portfolio.positions()
        bal = kalshi_portfolio.balance()
    except Exception as e:
        st.info(f"Positions unavailable: {e}")
        return
    st.caption(f"Cash balance: ${bal:.2f}" if bal is not None else "Balance unavailable")
    if not pos:
        st.caption("No open positions.")
        return
    rows = "".join(f"<tr><td>{p['ticker']}</td><td>{p['side']}</td>"
                   f"<td>{p['count']}</td></tr>" for p in pos)
    st.markdown(f"<table class='wxtable'><tr><th>Ticker</th><th>Side</th>"
                f"<th>Count</th></tr>{rows}</table>", unsafe_allow_html=True)


def _render_log(st) -> None:
    st.markdown("### Recent decisions")
    try:
        raw = trade_state.GitHubTransport().get(trade_state.LOG_PATH)
        lines = raw[0].splitlines() if raw else []
        records = [json.loads(x) for x in lines if x.strip()]
    except Exception as e:
        st.info(f"Log unavailable: {e}")
        return
    if not records:
        st.caption("No decisions logged yet.")
        return
    for r in summarize_log(records):
        st.caption(f"{r['ts']} · {r['kind']} · {r['ticker']} · {r['reason']}")
