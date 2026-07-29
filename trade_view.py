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
from datetime import datetime

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


DASH = "—"


def _money(v, signed=False) -> str:
    if v is None:
        return DASH
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _contract_label(pos: dict) -> str:
    """The human bracket name, falling back to the ticker's bracket suffix for
    positions recorded before the label was logged."""
    if pos.get("label"):
        return pos["label"]
    parts = (pos.get("ticker") or "").split("-")
    return parts[-1] if len(parts) >= 3 else (pos.get("ticker") or DASH)


def _entry_when(pos: dict) -> tuple[str, str]:
    """(date, time) of entry in local time. The date falls back to the position's
    climate day (recoverable from the ticker) when no timestamp was recorded."""
    import config
    from zoneinfo import ZoneInfo

    ts = pos.get("ts")
    if ts:
        try:
            local = datetime.fromisoformat(ts).astimezone(ZoneInfo(config.TIMEZONE))
            return local.strftime("%m-%d"), local.strftime("%-I:%M %p")
        except (TypeError, ValueError):
            pass
    import trader
    day = trader._position_day(pos)
    return (day.strftime("%m-%d") if day else DASH), DASH


def position_rows(mode: str, held_truth: list, runtime: dict,
                  marks: dict | None = None, params: dict | None = None) -> list[dict]:
    """Display rows for Open Positions, from the same source the trader manages.

    Delegates to `trader._managed_positions` so the panel can never disagree with
    what the loop is actually holding: live reads the Kalshi account (the source
    of truth, enriched with our recorded entry ask), shadow reads our own runtime
    record because no real fill exists. Reading only the account printed "No Open
    Positions" under a logged shadow entry.

    `marks` is {ticker: {"bid", "ask", "model"}}, passed in by the renderer so
    this stays pure. `Current` is the ASK — the price `stop_loss_hit` references —
    while `P&L` uses the BID, the price a sale would actually fill into; they are
    separate columns because collapsing them hides which one drives the stop.
    """
    import trader

    marks = marks or {}
    out = []
    for p in trader._managed_positions(mode, held_truth, runtime):
        m = marks.get(p["ticker"]) or {}
        ask, bid, count = p.get("entry_ask"), m.get("bid"), p.get("count") or 1
        stop = params.get("stop_loss") if params else None
        model = m.get("model")
        day, clock = _entry_when(p)
        out.append({
            "Date": day,
            "Time": clock,
            "Variable": (p.get("variable") or "").capitalize() or DASH,
            "Contract": _contract_label(p),
            "Side": (p.get("side") or "").capitalize(),
            "Count": count,
            "Entry": _money(ask),
            "Current": _money(m.get("ask")),
            "P&L": DASH if (bid is None or ask is None)
                   else _money((bid - ask) * count, signed=True),
            "Stop-out": DASH if (ask is None or stop is None)
                        else _money(ask - stop),
            "Model %": DASH if model is None else f"{model:.0%}",
        })
    return out


def open_marks_for_curve(runtime: dict, marks: dict | None) -> list[dict]:
    """Open positions in the shape `trade_pnl.unrealized` consumes. Positions with
    no live bid are still emitted; `unrealized` skips them rather than marking
    them at zero."""
    marks = marks or {}
    return [{"ticker": tkr, "entry_ask": e.get("entry_ask"),
             "count": e.get("count") or 1,
             "bid": (marks.get(tkr) or {}).get("bid")}
            for tkr, e in (runtime.get("entries") or {}).items()]


def _live_marks(mode, held_truth, runtime, station) -> dict:
    """{ticker: {"bid", "ask", "model"}} for every managed position — the live
    book plus the model's current probability for that bracket. Network work, so
    it is kept out of `position_rows`; one failed ticker must not blank the table,
    and a failed snapshot must not blank the prices."""
    import trader
    from sources import kalshi

    positions = trader._managed_positions(mode, held_truth, runtime)
    probs = {}
    if positions:
        try:
            import calibration
            import model
            calib = calibration.get(station=station)
            snap = (model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"),
                                   continuous_obs=True, station=station)
                    if calib else {})
            probs = {v: ((snap.get("today") or {}).get(v) or {}).get("probabilities")
                     for v in ("high", "low")}
        except Exception:
            probs = {}

    out = {}
    for p in positions:
        entry = {"bid": None, "ask": None, "model": None}
        try:
            book = kalshi.fetch_orderbook(p["ticker"])
            side = p.get("side") or "yes"
            bids = book.get(side) or []
            entry["bid"] = max((px for px, _s in bids), default=None)
            ladder = kalshi.ask_ladder(book, side)
            entry["ask"] = ladder[0][0] if ladder else None
        except Exception:
            pass
        pr = probs.get(p.get("variable"))
        if pr and p.get("floor") is not None and p.get("cap") is not None:
            try:
                import model
                entry["model"] = model.prob_for_strike(pr, "between",
                                                       p["floor"], p["cap"])
            except Exception:
                pass
        out[p["ticker"]] = entry
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
    params = trade_state.load_state(station=station)
    marks = _live_marks(mode, held, runtime, station)
    rows = position_rows(mode, held, runtime, marks, params)
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
