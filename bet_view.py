"""'My Bets' page — the user's real Kalshi bets on the Dallas temp markets since
BETS_START, with realized P&L, the model's read at bet time, and a cumulative-P&L
equity curve. Read-only. Fetch failures degrade to a warning, never a crash.
"""
from __future__ import annotations

import logging
import traceback

import altair as alt
import pandas as pd
import streamlit as st

_log = logging.getLogger("bet_view")

import bet_history
import betting_log
import calibration
import consensus_log
import market_view
from sources import kalshi_auth, kalshi_portfolio


@st.cache_data(ttl=60, show_spinner="Loading your Kalshi bets…")
def _load_bets():
    """Fetch + assemble + annotate. Returns (rows, summary, curve). Cached ~60s.
    Raises KalshiCredentialsError when creds are absent (handled by the caller)."""
    fills = kalshi_portfolio.fills(bet_history.BETS_START)
    settlements = kalshi_portfolio.settlements(bet_history.BETS_START)
    meta = {t: kalshi_portfolio.market_meta(t) for t in {f["ticker"] for f in fills}}
    rows = bet_history.build_rows(fills, settlements, meta)
    bet_history.annotate_rows(rows, betting_log.load(), consensus_log.load(),
                              calibration.get())
    return rows, bet_history.summary(rows), bet_history.equity_curve(rows)


def equity_chart(curve, color):
    """Stock-chart-style line of cumulative P&L (x=date, y=total) on a transparent
    background so it follows the palette, with a zero baseline rule."""
    df = pd.DataFrame(curve)
    line = (alt.Chart(df).mark_line(point=True, strokeWidth=2.5, color=color)
            .encode(x=alt.X("date:T", title=None),
                    y=alt.Y("total:Q", title="Cumulative P&L ($)"),
                    tooltip=[alt.Tooltip("date:T", title="date"),
                             alt.Tooltip("total:Q", title="total", format="$.2f")]))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
    return ((zero + line).properties(height=260, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def _fmt_pnl(v):
    return "—" if v is None else (f"+${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}")


def _model_cell(r):
    """The 'Model @ bet' display string. entry can be None (a resolved side with
    zero matching BUY fills), which leaves edge/agree unset even though model_prob
    is present — so probability and edge/agreement are handled independently to
    avoid a TypeError on `None * 100` crashing the whole page."""
    if r.get("model_prob") is None:
        return "—"
    if r.get("edge") is None:
        return f"{r['model_prob']*100:.0f}%"
    return (f"{r['model_prob']*100:.0f}% · {r['edge']*100:+.0f} · "
            + ("with" if r["agree"] else "against"))


def render():
    market_view._inject_theme(market_view._seed_theme())
    st.title("My Bets")

    try:
        rows, summ, curve = _load_bets()
    except kalshi_auth.KalshiCredentialsError:
        st.info("Add your Kalshi API key to the app secrets to enable this page — "
                "a `[kalshi]` section with `access_key_id` and `private_key`. "
                "The key is read only from secrets and used for read-only requests.")
        return
    except Exception as e:                       # never crash the dashboard
        # Full traceback to the server log (Streamlit Cloud "Manage app" → logs) so a
        # live failure is diagnosable; the key is never logged (it lives in request
        # headers, not the traceback/URL/body).
        _log.error("My Bets load failed:\n%s", traceback.format_exc())
        # Also surface the HTTP status + endpoint + Kalshi's error body inline when
        # the exception carries a response (a 4xx/5xx from requests).
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            path = resp.url.split("/trade-api/v2", 1)[-1].split("?", 1)[0]
            body = (resp.text or "").strip().replace("\n", " ")[:200]
            detail = f" — {resp.status_code} on {path}: {body}"
        st.warning(f"Couldn't load your Kalshi bets right now ({type(e).__name__}{detail}). "
                   "The rest of the dashboard is unaffected; try again shortly.")
        return

    if not rows:
        st.caption(f"No Dallas-temp bets found since {bet_history.BETS_START:%b %-d, %Y}.")
        return

    c = st.columns(5)
    c[0].metric("Record (W–L)", f"{summ['wins']}–{summ['losses']}")
    c[1].metric("Win rate", f"{summ['win_rate']:.0f}%")
    c[2].metric("Net P&L", _fmt_pnl(summ["net_pnl"]))
    c[3].metric("ROI", f"{summ['roi']:+.0f}%")
    c[4].metric("Bets with model",
                "—" if summ["with_model_pct"] is None else f"{summ['with_model_pct']:.0f}%")

    if curve:
        st.altair_chart(equity_chart(curve, market_view._chart_colors()["kalshi"]),
                        use_container_width=True)
    else:
        st.caption("The equity curve appears once a bet settles.")

    disp = []
    for r in rows:
        model = _model_cell(r)
        disp.append({
            "Date": r["first_ts"].strftime("%b %-d"),
            "Contract": r["label"], "Side": r["side"].upper(),
            "Entry": market_view.cents(r["entry"]), "Qty": r["qty"],
            "Model @ bet": model,
            "Settled": "open" if r["status"] == "open" else r["result"].upper(),
            "P&L": _fmt_pnl(r["pnl"]),
        })
    market_view._html_table(pd.DataFrame(disp))
    st.caption("Model @ bet = the model's probability for the side you took, its "
               "edge vs your entry (pp), and whether you bet with or against it — "
               "reconstructed from the nearest logged snapshot to your fill (— if "
               "none). P&L is realized on settlement. Read-only view of your Kalshi "
               "account; prices in ¢, amounts in $.")
