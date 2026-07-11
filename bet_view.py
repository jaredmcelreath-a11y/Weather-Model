"""'My Bets' page — the user's real Kalshi bets on the Dallas temp markets since
BETS_START, with realized P&L, the model's read at bet time, and a cumulative-P&L
equity curve. Read-only. Fetch failures degrade to a warning, never a crash.
"""
from __future__ import annotations

import logging
import traceback
from datetime import date

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
    """Fetch + assemble + annotate. Returns (rows, summary, curve, balance). Cached
    ~60s. Raises KalshiCredentialsError when creds are absent (handled by caller)."""
    fills = kalshi_portfolio.fills(bet_history.BETS_START)
    settlements = kalshi_portfolio.settlements(bet_history.BETS_START)
    meta = {t: kalshi_portfolio.market_meta(t) for t in {f["ticker"] for f in fills}}
    rows = bet_history.build_rows(fills, settlements, meta)
    bet_history.annotate_rows(rows, betting_log.load(), consensus_log.load(),
                              calibration.get())
    # Mark open positions to market so the Exit/P&L columns and the live curve point
    # reflect what they're worth right now (updates each ~60s cache cycle as bids move).
    for r in rows:
        if r["status"] == "open":
            r["current_value"] = kalshi_portfolio.market_price(r["ticker"], r["side"])
    # Portfolio value = cash + the live market value of open positions (qty × current
    # bid), matching Kalshi's portfolio total — so it doesn't drop when you place a bet.
    open_mv = sum(r["qty"] * r["current_value"] for r in rows
                  if r["status"] == "open" and r.get("current_value") is not None)
    portfolio = (kalshi_portfolio.balance() or 0.0) + open_mv
    return (rows, bet_history.summary(rows),
            bet_history.equity_curve_live(rows, date.today()), portfolio)


def equity_chart(curve, color):
    """Stock-chart-style line of account balance (x=date, y=total) starting from the
    bankroll, on a transparent background so it follows the palette, with a dashed
    break-even rule at the starting bankroll. Tap/click a point to pin its readout —
    mobile-friendly, since touch devices don't fire the hover events Vega tooltips need
    (same tap-to-pin pattern as the consensus chart)."""
    df = pd.DataFrame(curve)
    labels = df.assign(label=df.apply(
        lambda r: f"{pd.to_datetime(r['date']).strftime('%b %-d')}\n${r['total']:.2f}",
        axis=1))
    enc = alt.Chart(df).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("total:Q", title="Trading balance ($)", scale=alt.Scale(zero=False)))
    line = enc.mark_line(strokeWidth=2.5, color=color)

    pick = alt.selection_point(on="click", nearest=True, fields=["date"],
                               empty=False, clear="dblclick")
    dots = enc.mark_point(filled=True, opacity=1, color=color).encode(
        size=alt.condition(pick, alt.value(150), alt.value(60)),
        tooltip=[alt.Tooltip("date:T", title="date"),
                 alt.Tooltip("total:Q", title="balance", format="$.2f")],
    ).add_params(pick)
    # Pinned readout for the tapped point, anchored top-left so it never clips off the
    # right edge; one line per field (lineBreak) keeps the full readout in view.
    pinned = alt.Chart(labels).mark_text(
        align="left", baseline="top", x=6, y=4, fontSize=13, fontWeight="bold",
        lineBreak="\n", lineHeight=15, color=color,
    ).encode(text="label:N").transform_filter(pick)

    rule = alt.Chart(pd.DataFrame({"y": [bet_history.STARTING_BANKROLL]})).mark_rule(
        strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
    return ((rule + line + dots + pinned).properties(height=260, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def _fmt_pnl(v):
    return "—" if v is None else (f"+${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}")


def _fmt_usd(v):
    return "—" if v is None else f"${v:,.2f}"


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
    st.title("History")

    try:
        rows, summ, curve, balance = _load_bets()
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


    # keyed 'top_metrics' so the same mobile CSS grids these 2-per-row (like the Forecast
    # page) instead of stacking; columns created in the container, filled just after.
    with st.container(key="top_metrics"):
        c = st.columns(6)
    _mc = market_view.metric_card
    c[0].markdown(_mc("Portfolio", _fmt_usd(balance),
                      "Your live Kalshi portfolio value — cash plus the current market "
                      "value of open positions (marked to market), matching Kalshi's "
                      "portfolio total. It doesn't drop when you place a bet, and it moves "
                      "as bids change."), unsafe_allow_html=True)
    c[1].markdown(_mc("Total % Gain", f"{summ['pct_gain']:+.0f}%",
                      f"Total profit — realized plus open positions marked to market "
                      f"(their live gain/loss) — as a percent of your starting bankroll "
                      f"(${bet_history.STARTING_BANKROLL:,.0f}). Moves with the market."),
                  unsafe_allow_html=True)
    c[2].markdown(_mc("Record (W–L)", f"{summ['wins']}–{summ['losses']}"),
                  unsafe_allow_html=True)
    c[3].markdown(_mc("Win rate", f"{summ['win_rate']:.0f}%"), unsafe_allow_html=True)
    c[4].markdown(_mc("Avg % Return", f"{summ['roi']:+.2f}%",
                      "Stake-weighted return — total profit (realized + open marked to "
                      "market) ÷ total staked. Buying near-certain contracts at high "
                      "prices (e.g. 97¢) yields small per-trade returns even on wins."),
                  unsafe_allow_html=True)
    c[5].markdown(_mc("Avg % / Trade", f"{summ['avg_trade_return']:+.2f}%",
                      "Simple (unweighted) average of each bet's own percent return "
                      "(realized, or open marked to market) — every bet counts equally, "
                      "unlike the stake-weighted Avg % Return."), unsafe_allow_html=True)

    if curve:
        st.altair_chart(equity_chart(curve, market_view._chart_colors()["kalshi"]),
                        use_container_width=True)
        st.caption(f"Trading performance: your ${bet_history.STARTING_BANKROLL:,.0f} "
                   "starting bankroll plus realized P&L on settled bets, and a final "
                   "**live** point that adds open positions' current unrealized P&L — so "
                   "the last point moves with the market. Not your account value (it "
                   "excludes deposits, withdrawals, and fees); see **Portfolio** above for "
                   "your live Kalshi total.")
    else:
        st.caption("The equity curve appears once a bet settles.")

    disp = []
    for r in rows:
        model = _model_cell(r)
        volume = r["qty"] * r["entry"] if r["entry"] is not None else None
        # Exit: realized sell/settlement price when closed; for an OPEN position, its
        # current market value (marked to market).
        exit_val = r.get("current_value") if r["status"] == "open" else r["exit"]
        # P&L: realized once closed; for an OPEN position, the LIVE unrealized P&L
        # (qty × (now − entry)) as a terracotta placeholder until it settles/sells.
        if r["status"] == "open":
            cv, en, qy = r.get("current_value"), r["entry"], r["qty"]
            u = qy * (cv - en) if (cv is not None and en is not None) else None
            pnl_cell = ("—" if u is None else
                        f'<span style="color:#C97B5E;font-weight:600">'
                        f'~{"+" if u >= 0 else "−"}${abs(u):,.2f}</span>')
        else:
            pnl_cell = _fmt_pnl(r["pnl"])
        disp.append({
            "Date": r["first_ts"].strftime("%b %-d"),
            "Contract": r["label"], "Side": r["side"].upper(),
            "Entry": market_view.cents(r["entry"]),
            "Exit": market_view.cents(exit_val),
            "Qty": f"{r['qty']:.2f}",
            "Volume": _fmt_usd(volume),
            "Model @ bet": model,
            "Settled": ("open" if r["status"] == "open"
                        else "sold" if r["status"] == "closed"
                        else r["result"].upper()),
            "P&L": pnl_cell,
        })
    market_view._html_table(pd.DataFrame(disp))
    st.caption("Model @ bet = the model's probability for the side you took, its "
               "edge vs your entry (pp), and whether you bet with or against it — "
               "reconstructed from the nearest logged snapshot to your fill (— if "
               "none). Exit = your sell/settlement price, or the current market value "
               "for an open position. P&L is realized (net of Kalshi fees) once "
               "closed; open positions show their live unrealized P&L in terracotta "
               "(the `~` values) until they settle or you sell. Read-only view of your "
               "Kalshi account; prices in ¢, amounts in $.")
