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


def safety_rows() -> list[dict]:
    """One safety row per station for the both-at-once summary:
    {station, name, kill_switch, mode, armed}. `armed` = live AND kill switch off
    (the only state that can place real orders)."""
    import config
    out = []
    for code in config.STATION_CODES:
        p = trade_state.load_state(station=code)
        out.append({"station": code, "name": config.station(code).name,
                    "kill_switch": p["kill_switch"], "mode": p["mode"],
                    "armed": (not p["kill_switch"]) and p["mode"] == "live"})
    return out


def render() -> None:
    import streamlit as st

    import city_view
    import config
    import market_view
    market_view._theme_controls()   # inject theme CSS (+ mobile .wtbl-wrap) + Settings

    st.markdown("## Autonomous Trader")

    # --- Both-at-once safety summary: never hide a city's armed/halted state ---
    with st.container(key="metrics2_trader_safety"):
        scols = st.columns(len(config.STATION_CODES))
    for i, row in enumerate(safety_rows()):
        if row["armed"]:
            state, val = "red", "Live & Armed"
        elif row["kill_switch"]:
            state, val = "green", "Kill Switch On"
        else:
            state, val = "amber", "Shadow"
        scols[i].markdown(market_view.metric_card(
            row["name"], val,
            f"Mode: {row['mode'].capitalize()} · Kill switch "
            f"{'engaged' if row['kill_switch'] else 'off'}.", dot=state),
            unsafe_allow_html=True)

    # --- Per-city editor ---
    station = city_view.city_control("trader", arity=2)
    st.caption(f"Editing **{config.station(station).name}** — changes apply to this "
               "city's trader only.")
    params = trade_state.load_state(station=station)

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

    if st.button("Save Settings", use_container_width=True):
        params["kill_switch"] = killed
        params["mode"] = mode
        try:
            trade_state.save_state(trade_params.merge_params(params), station=station)
            st.success("Saved.")
        except Exception as e:
            st.error(f"Save Failed: {e}")

    # The SAVED mode (params is the loaded doc; the Save branch above updates it
    # in place), not the radio's current value — an unsaved toggle must not change
    # which source the panel reads.
    _render_positions(st, market_view, station, params["mode"])
    _render_log(st, station)


def position_rows(mode: str, held_truth: list, runtime: dict) -> list[dict]:
    """Display rows for Open Positions, from the same source the trader manages.

    Delegates to `trader._managed_positions` so the panel can never disagree with
    what the loop is actually holding: live reads the Kalshi account (the source
    of truth, enriched with our recorded entry ask), shadow reads our own runtime
    record because no real fill exists. Reading only the account printed "No Open
    Positions" under a logged shadow entry."""
    import trader

    out = []
    for p in trader._managed_positions(mode, held_truth, runtime):
        ask = p.get("entry_ask")
        out.append({"Ticker": p["ticker"],
                    "Side": (p.get("side") or "").capitalize(),
                    "Count": p.get("count"),
                    "Entry Ask": "—" if ask is None else f"{ask:.2f}"})
    return out


def _render_positions(st, market_view, station, mode) -> None:
    import pandas as pd
    from sources import kalshi, kalshi_portfolio

    st.markdown("### Open Positions")
    held, bal = [], None
    try:
        bal = kalshi_portfolio.balance()
        if mode == "live":
            held = [p for p in (kalshi_portfolio.positions() or [])
                    if kalshi.station_of_ticker(p["ticker"]) == station]
    except Exception as e:
        st.info(f"Positions Unavailable: {e}")
        return
    st.caption(f"Cash Balance: ${bal:.2f}" if bal is not None else "Balance Unavailable")
    try:
        runtime = trade_state.load_runtime(station=station)
    except Exception:
        runtime = {}
    rows = position_rows(mode, held, runtime)
    if not rows:
        st.caption("No Open Positions.")
        return
    if mode != "live":
        st.caption("Shadow holdings — simulated entries, no real fills. The loop "
                   "still manages these (stop-loss and reversal exits).")
    market_view._html_table(pd.DataFrame(rows))


def _render_log(st, station) -> None:
    st.markdown("### Recent Decisions")
    try:
        raw = trade_state.GitHubTransport().get(trade_state._path(trade_state.LOG_PATH, station))
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
