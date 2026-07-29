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
import re
from datetime import datetime

import trade_params
import trade_state


def summarize_log(records: list[dict], limit: int = 20) -> list[dict]:
    """Newest-first, capped view rows for the decision log."""
    rows = sorted(records, key=lambda r: r.get("ts", ""), reverse=True)[:limit]
    return [{"ts": r.get("ts", ""), "kind": r.get("kind", ""),
             "ticker": r.get("ticker", ""), "reason": r.get("reason", "")}
            for r in rows]


_ACTION_KINDS = ("entry", "exit", "halt")


def partition_decisions(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """(actions, skips), each newest-first.

    Every run logs one record per variable per city, so the two or three real
    actions of a day are buried under dozens of repetitive skips. Splitting them
    lets the page show what the trader DID and keep what it merely checked behind
    a toggle."""
    rows = sorted(records, key=lambda r: r.get("ts") or "", reverse=True)
    actions = [r for r in rows if r.get("kind") in _ACTION_KINDS]
    skips = [r for r in rows if r.get("kind") == "skip"]
    return actions, skips


def _local_hm(ts: str) -> str:
    import config
    from zoneinfo import ZoneInfo
    try:
        return (datetime.fromisoformat(ts)
                .astimezone(ZoneInfo(config.TIMEZONE)).strftime("%-I:%M %p"))
    except (TypeError, ValueError):
        return ts or DASH


def action_rows(actions: list[dict]) -> list[dict]:
    """Compact table rows for entries and exits — the day's real decisions."""
    out = []
    for r in actions:
        kind = r.get("kind", "")
        if kind == "entry":
            ask = r.get("entry_ask")
            detail = (f"{(r.get('side') or '').lower()} {r.get('count') or 1} "
                      f"@ {ask:.2f}" if ask is not None else r.get("reason", ""))
        else:
            detail = _short_reason(r.get("reason", ""))
            pnl = r.get("pnl")
            if pnl is not None:
                detail = f"{detail} · {pnl:+.2f}" if detail else f"{pnl:+.2f}"
        out.append({"Time": _local_hm(r.get("ts", "")), "Kind": kind.capitalize(),
                    "Contract": _contract_label(r), "Detail": detail})
    return out


def status_strip(records: list[dict], variables: list[str]) -> dict:
    """One line per variable describing where the trader currently stands, so the
    page answers "what is it doing right now" without reading the whole log."""
    actions, skips = partition_decisions(records)
    out = {}
    for var in variables:
        act = next((r for r in actions if r.get("variable") == var), None)
        if act and act.get("kind") == "entry":
            out[var] = f"holding {_contract_label(act)}"
            continue
        if act:
            out[var] = f"no position ({_short_reason(act.get('reason', 'closed'))})"
            continue
        skip = next((r for r in skips if r.get("variable") == var), None)
        out[var] = (_short_reason(skip.get("reason", "no trade")) if skip
                    else "no activity yet")
    return out


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
    _render_log(st, market_view, station, params)
    _render_pnl(st, market_view)


DASH = "—"


def _money(v, signed=False) -> str:
    if v is None:
        return DASH
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _contract_label(pos: dict) -> str:
    """The bracket as a compact range — "99-100", matching the History page's
    contract naming rather than Kalshi's "B99.5" ticker suffix.

    Prefers the recorded floor/cap, then the Kalshi label ("99° to 100°"), then
    the B<mid> ticker suffix, where mid sits half a degree inside each edge. The
    T-prefixed tails are open-ended and do NOT follow that rule, so with no label
    they keep their raw suffix instead of being widened into a wrong range."""
    floor, cap = pos.get("floor"), pos.get("cap")
    if floor is not None and cap is not None:
        return f"{floor:g}-{cap:g}"
    label = pos.get("label") or ""
    nums = re.findall(r"-?\d+(?:\.\d+)?", label)
    if "to" in label and len(nums) >= 2:
        return f"{float(nums[0]):g}-{float(nums[1]):g}"
    if label:
        return label.replace("°", "")
    suffix = (pos.get("ticker") or "").split("-")[-1]
    if suffix.startswith("B"):
        try:
            mid = float(suffix[1:])
            return f"{mid - 0.5:g}-{mid + 0.5:g}"
        except ValueError:
            pass
    return suffix or DASH


_TICKER_RE = re.compile(r"\b(KX[A-Z]+-\d{2}[A-Z]{3}\d{2}-\S+)")


def _short_reason(reason: str) -> str:
    """Compact a logged reason for display. The audit record keeps the full text
    (a bare ticker is what you want when reconstructing a decision); the table
    shows the part a human reads."""
    if not reason:
        return ""
    m = _TICKER_RE.search(reason)
    if m and "target moved" in reason:
        return f"reversal → {_contract_label({'ticker': m.group(1)})}"
    if reason.startswith("reversal: safety gate"):
        return "reversal · gate fired"
    if reason.startswith("stop-loss:"):
        ask = re.search(r"ask (\d+\.\d+)", reason)
        return f"stop-loss @ {ask.group(1)}" if ask else "stop-loss"
    # Raw param names leak into the log as skip reasons; say what they mean.
    return {"max_open_per_variable": "position already open",
            "re-entry into just-stopped bracket": "just stopped out here",
            }.get(reason, reason)


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


_CACHED = {}


def _probs_for(station: str) -> dict:
    """{variable: probabilities} on the Kalshi/CLI basis, cached 60s per station.

    A full `model.snapshot` is expensive, so it is wrapped in `st.cache_data` —
    created once per process and reused, since decorating on every rerun would
    make a fresh cache each time and defeat it. The 60s TTL matches the Forecast
    page's; the raw HTTP underneath is disk-cached 600s, so a miss re-blends
    rather than refetches."""
    import streamlit as st

    fn = _CACHED.get("probs")
    if fn is None:
        @st.cache_data(ttl=60, show_spinner=False)
        def fn(code: str) -> dict:
            import calibration
            import model
            calib = calibration.get(refresh=True, station=code)
            if not calib:
                return {}
            snap = model.snapshot(calib,
                                  settle_offset=calib.get("settlement_offset"),
                                  continuous_obs=True, station=code)
            return {v: ((snap.get("today") or {}).get(v) or {}).get("probabilities")
                    for v in ("high", "low")}
        _CACHED["probs"] = fn
    try:
        return fn(station) or {}
    except Exception:
        return {}


def _quoted_asks(positions: list[dict], station: str) -> dict:
    """{ticker: quoted ask} for the held side. One `fetch_contracts` call per
    (variable, day) — cached 30s — covers every position, so this costs at most
    two requests however many rows the table has."""
    import trader
    from sources import kalshi

    out, seen = {}, {}
    for p in positions:
        var, day = p.get("variable"), trader._position_day(p)
        if not var or day is None:
            continue
        key = (var, day)
        if key not in seen:
            try:
                seen[key] = kalshi.fetch_contracts(var, day, station=station)
            except Exception:
                seen[key] = []
        side = p.get("side") or "yes"
        for c in seen[key]:
            if c.get("ticker") == p["ticker"]:
                out[p["ticker"]] = c.get(f"{side}_ask")
                break
    return out


def _live_marks(mode, held_truth, runtime, station, with_model=True) -> dict:
    """{ticker: {"bid", "ask", "model"}} for every managed position — the live
    book, plus the model's current probability for that bracket when `with_model`.

    Network work, so it is kept out of `position_rows`; one failed ticker must not
    blank the table, and a failed snapshot must not blank the prices. The P&L
    curve passes `with_model=False`: it marks positions on the bid alone, and a
    snapshot per station would make the chart cost a full forecast fetch."""
    import trader
    from sources import kalshi

    positions = trader._managed_positions(mode, held_truth, runtime)
    probs = _probs_for(station) if (positions and with_model) else {}

    out = {}
    quotes = _quoted_asks(positions, station)
    for p in positions:
        entry = {"bid": None, "ask": None, "model": None}
        try:
            book = kalshi.fetch_orderbook(p["ticker"])
            side = p.get("side") or "yes"
            bids = book.get(side) or []
            entry["bid"] = max((px for px, _s in bids), default=None)
            ladder = kalshi.ask_ladder(book, side)
            # The book-derived ask is undefined when nothing rests on the opposite
            # side (buying YES sells into NO bids). Fall back to the market's own
            # quote so `Current` isn't a dash next to a live P&L.
            entry["ask"] = ladder[0][0] if ladder else quotes.get(p["ticker"])
        except Exception:
            entry["ask"] = quotes.get(p["ticker"])
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


def pnl_frame(curves: dict) -> "object":
    """Long-form frame for the P&L chart: one row per point per city.

    Dates are parsed with `pd.to_datetime` — bare date strings on an Altair `:T`
    axis render a day early (the same UTC trap already fixed in the Lab and equity
    charts, 684f7a6)."""
    import pandas as pd

    rows = [{"date": p["date"], "total": p["total"], "city": city}
            for city, curve in (curves or {}).items() for p in (curve or [])]
    df = pd.DataFrame(rows, columns=["date", "total", "city"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def record_card_text(summary: dict) -> tuple[str, str]:
    """(value, help) for the win/loss box — pure so the wording is testable."""
    if not summary["trades"]:
        return DASH, "No round trip has closed yet."
    val = f"{summary['wins']}–{summary['losses']}"
    rate = summary["win_rate"]
    bits = ["Wins–losses over closed round trips."]
    if rate is not None:
        bits.append(f"Win rate {rate:.0%}.")
    if summary["pushes"]:
        bits.append(f"{summary['pushes']} break-even (not counted either way).")
    bits.append(f"Realized {summary['realized']:+.2f}.")
    return val, " ".join(bits)


def trades_card_text(summary: dict, open_count: int) -> tuple[str, str]:
    """(value, help) for the trade-count box. Counts CLOSED round trips — open
    positions are named in the help rather than added in, so the number always
    agrees with the win/loss box."""
    bits = ["Closed round trips (an entry paired with its exit)."]
    if open_count:
        bits.append(f"{open_count} still open, not counted yet.")
    bits.append("Unpriced pre-2026-07-28 market exits are excluded.")
    return str(summary["trades"]), " ".join(bits)


def _settled_for(station: str) -> dict:
    """{day: (high, low)} on the CLI basis, cached 1h per station.

    The settlement log is a whole-file GitHub read that changes about once a day,
    and this page reruns on every widget touch. Wrapped the same way `_probs_for`
    is — created once per process, because decorating on each rerun would build a
    fresh cache every time and defeat it."""
    import streamlit as st

    fn = _CACHED.get("settled")
    if fn is None:
        @st.cache_data(ttl=3600, show_spinner=False)
        def fn(code: str) -> dict:
            import settlements
            return settlements.as_map("cli", station=code)
        _CACHED["settled"] = fn
    try:
        return fn(station) or {}
    except Exception:
        return {}


def _station_pnl(station, mode) -> tuple[dict, str | None]:
    """({curve, summary, open}, error) for one station, from its own decision log.

    Passes the CLI settlement map into `closed_trades` so positions the loop
    retired at settlement before `exit_price` was logged are still scored — that
    history is what makes the chart span more than the current day."""
    import settlement
    import trade_pnl
    empty = {"curve": [], "summary": trade_pnl.record_summary([]), "open": 0}
    try:
        records = load_log(station)
        runtime = trade_state.load_runtime(station=station)
    except Exception as e:
        return empty, str(e)
    settled = _settled_for(station)
    marks = (_live_marks(mode, [], runtime, station, with_model=False)
             if runtime.get("entries") else {})
    today = settlement.climate_day_of(datetime.now(), station)
    trades = trade_pnl.closed_trades(records, settled)
    return {"curve": trade_pnl.equity_curve(
                trades, today, open_marks_for_curve(runtime, marks)),
            "summary": trade_pnl.record_summary(trades),
            "open": len(runtime.get("entries") or {})}, None


def _render_pnl(st, market_view) -> None:
    import altair as alt
    import pandas as pd

    import city_view
    import config

    st.markdown("### Shadow P&L")
    _sel, codes = city_view.city_sections("trader_pnl", arity=3)
    curves, errs, stats = {}, [], {}
    for code in codes:
        try:
            mode = trade_state.load_state(station=code)["mode"]
        except Exception:
            mode = "shadow"
        got, err = _station_pnl(code, mode)
        if err:
            errs.append(f"{config.station(code).name}: {err}")
        name = config.station(code).name
        curves[name] = got["curve"]
        stats[name] = got

    # Record + trade count, one pair per selected city. The `metrics2_` container
    # key is what grids these 2-per-row on phones (see market_view's ≤640px rules),
    # so Both lands as a tidy 2x2 instead of four stacked boxes.
    with st.container(key="metrics2_trader_record"):
        cols = st.columns(2 * max(1, len(stats)))
    for i, (name, got) in enumerate(stats.items()):
        prefix = f"{name} " if len(stats) > 1 else ""
        rec_v, rec_h = record_card_text(got["summary"])
        cnt_v, cnt_h = trades_card_text(got["summary"], got["open"])
        cols[2 * i].markdown(market_view.metric_card(
            f"{prefix}Record (W–L)", rec_v, rec_h), unsafe_allow_html=True)
        cols[2 * i + 1].markdown(market_view.metric_card(
            f"{prefix}Trades", cnt_v, cnt_h), unsafe_allow_html=True)

    df = pnl_frame(curves)
    if df.empty:
        st.caption("The Curve Appears Once A Position Closes Or Settles. "
                   "Market Exits Logged Before 2026-07-28 Carry No Exit Price And "
                   "Are Not Scored.")
        if errs:
            st.caption(" · ".join(errs))
        return
    colors = market_view._chart_colors()
    palette = [colors.get("kalshi", "#7aa2f7"), colors.get("consensus", "#e0af68")]
    zero = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(
        strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
    # Day ticks explicitly while the history is short: with only a couple of points
    # Altair otherwise picks an hourly scale and prints "01 AM, 02 AM…" across a
    # single day. Past ~2 weeks a tick per day overlaps into mush on a phone, so
    # hand the count back to Vega and let it thin the labels.
    ticks = "day" if df["date"].nunique() <= 12 else 6
    line = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("date:T", title=None,
                axis=alt.Axis(format="%b %-d", tickCount=ticks, labelAngle=0)),
        y=alt.Y("total:Q", title="Cumulative $", axis=alt.Axis(format="$.2f")),
        color=alt.Color("city:N", title=None,
                        scale=alt.Scale(range=palette[:max(1, len(curves))])),
        tooltip=["city:N", alt.Tooltip("date:T", format="%b %-d"),
                 alt.Tooltip("total:Q", format="$.2f")])
    st.altair_chart(zero + line, use_container_width=True)
    # Dollar signs are ESCAPED: Streamlit's markdown reads a second, unescaped `$`
    # in the same string as closing a LaTeX span and renders the text between them
    # as math. Two amounts in one caption is enough to trigger it.
    st.caption(r"Cumulative shadow P&L from \$0 across every weather day traded, "
               r"oldest first. Positions held to settlement are scored at \$1.00 "
               r"or \$0.00 against the CLI high/low; the last point marks "
               r"still-open positions to the current bid, so it moves intraday.")
    if errs:
        st.caption(" · ".join(errs))


def load_log(station) -> list[dict]:
    """Every decision record for `station` from the trade-data branch."""
    raw = trade_state.GitHubTransport().get(
        trade_state._path(trade_state.LOG_PATH, station))
    lines = raw[0].splitlines() if raw else []
    return [json.loads(x) for x in lines if x.strip()]


def _render_log(st, market_view, station, params) -> None:
    import pandas as pd

    st.markdown("### Recent Decisions")
    try:
        records = load_log(station)
    except Exception as e:
        st.info(f"Log Unavailable: {e}")
        return
    if not records:
        st.caption("No Decisions Logged Yet.")
        return

    strip = status_strip(records, params.get("enabled_variables") or ["high", "low"])
    st.caption(" · ".join(f"**{v.upper()}** → {s}" for v, s in strip.items()))

    actions, skips = partition_decisions(records)
    if actions:
        market_view._html_table(pd.DataFrame(action_rows(actions[:20])))
    else:
        st.caption("No Entries Or Exits Yet.")
    if skips:
        with st.expander(f"Show Skipped Checks ({len(skips)})", expanded=False):
            for r in summarize_log(skips, limit=40):
                parts = [_fmt_ts(r["ts"])]
                if r["ticker"]:
                    parts.append(r["ticker"])
                if r["reason"]:
                    parts.append(r["reason"])
                st.caption(" · ".join(parts))
