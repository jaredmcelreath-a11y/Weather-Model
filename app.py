"""KDFW high/low probability dashboard.

Run with:  streamlit run app.py
Two pages — Kalshi (default) and Robinhood (ForecastEx) — switchable via the sidebar nav.
Each page auto-refreshes; switch Today/Tomorrow and the safe-hold floor in the
sidebar. All rendering lives in market_view; exchange differences in markets.
"""

from __future__ import annotations

import os

import streamlit as st

import accuracy_view
import bet_view
import calibration
import edge_view
import forecast_log
import hourly_view
import market_view
import model
from markets import KALSHI, ROBINHOOD

st.set_page_config(page_title="Dallas Daily High & Low", layout="wide")

# On Streamlit Cloud, point the forward log at the GitHub-hosted copy maintained
# by the scheduled Action, so live self-scoring and per-lead sigma persist across
# the platform's ephemeral restarts. Configured via dashboard secrets [github];
# absent locally, where the log is just a file.
try:
    _gh = dict(st.secrets["github"]) if "github" in st.secrets else None
except Exception:
    _gh = None
if _gh:
    os.environ.setdefault("FORECAST_LOG_GH_REPO", _gh.get("repo", ""))
    os.environ.setdefault("FORECAST_LOG_GH_REF", _gh.get("ref", "data"))
    os.environ.setdefault("FORECAST_LOG_GH_TOKEN", _gh.get("token", ""))

# Kalshi read-only API key for the "My Bets" page — seeded from dashboard secrets
# [kalshi] the same way [github] is above; absent locally/on Cloud without the
# secret, where bet_view degrades to an enable-note rather than crashing.
try:
    _kal = dict(st.secrets["kalshi"]) if "kalshi" in st.secrets else None
except Exception:
    _kal = None
if _kal:
    os.environ.setdefault("KALSHI_ACCESS_KEY_ID", _kal.get("access_key_id", ""))
    os.environ.setdefault("KALSHI_PRIVATE_KEY", _kal.get("private_key", ""))

# TTL matches the page's 60s autorefresh and the Kalshi market cache (30s) so the
# model snapshot and the market-implied EV are recomputed on the same cycle — a
# 120s model cache next to a 30s market cache let the model lag up to ~2 min behind
# the market, which read on-screen as a (false) model-vs-market disagreement. The
# raw forecast/obs HTTP calls stay cheap: they're backed by the 600s disk cache in
# sources.common, so a tighter st.cache TTL only re-blends, it doesn't refetch.
@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot():
    calib = calibration.get(refresh=True)
    return model.snapshot(calib), calib


@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot_kalshi():
    """Snapshot shifted to the Kalshi/CLI settlement basis via the calibrated
    settlement_offset (absent offset -> behaves like the hourly snapshot)."""
    calib = calibration.get(refresh=True)
    snap = model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"),
                          continuous_obs=True, include_candidate=True)
    return snap, calib


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_accuracy():
    """Backtest (immediate) + live self-scoring (grows as days settle)."""
    import backtest
    import scoring
    bt = live = None
    try:
        bt = backtest.run()
    except Exception:
        pass
    try:
        live = scoring.score()
    except Exception:
        pass
    return bt, live


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_accuracy_kalshi():
    """Backtest + live self-scoring on the Kalshi/CLI settlement basis."""
    import backtest
    import scoring
    calib = calibration.get(refresh=True) or {}
    off = calib.get("settlement_offset")
    bt = live = None
    try:
        bt = backtest.run(cli=True, settle_offset=off)
    except Exception:
        pass
    try:
        live = scoring.score(basis="cli")
    except Exception:
        pass
    try:
        market = scoring.market_accuracy()
        if market and market.get("n"):
            live = dict(live or {})
            live["market"] = market
    except Exception:
        pass
    return bt, live


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_recap():
    """Yesterday's scorecard for the Morning Recap card (CLI/Kalshi settlement
    basis), including realized bet P&L. Changes at most once a day, so a long TTL
    is fine. None on any error or before yesterday settles."""
    from datetime import date
    import forecast_log
    import recap
    import settlements
    # Realized bet P&L for the scorecard — best-effort (needs the Kalshi portfolio
    # API; absent locally/without the [kalshi] secret, the P&L line just omits).
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
    except Exception:
        bet_rows = None
    try:
        return recap.yesterday_scorecard(date.today(), settlements.as_map("cli"),
                                          forecast_log.load(), bet_rows=bet_rows)
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_calibration_history():
    """Calibration recompute history for the drift sparklines. Changes ~1×/day."""
    import calibration_history
    try:
        return calibration_history.load()
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_portfolio_value():
    """Total Kalshi portfolio worth = cash + open positions marked to market
    (matches the My Bets page's Portfolio figure), the Kelly bankroll default.
    Short TTL so it tracks the live total. None if the portfolio API isn't set up."""
    try:
        import bet_history
        from sources import kalshi_portfolio
        cash = kalshi_portfolio.balance() or 0.0
        rows = bet_history.fetch_rows(bet_history.BETS_START)
        open_mv = 0.0
        for r in rows:
            if r["status"] == "open":
                cv = kalshi_portfolio.market_price(r["ticker"], r["side"])
                if cv is not None:
                    open_mv += r["qty"] * cv
        return cash + open_mv
    except Exception:
        return None


def _page(adapter, snapshot_loader, accuracy_loader, record_basis):
    snap, calib = snapshot_loader()
    dropped = snap.get("dropped_sources") or []
    if dropped:
        st.warning(
            "Running on a reduced model set — these feeds were unreachable and "
            "were skipped: " + ", ".join(dropped) + ". The consensus and "
            "probabilities reflect the remaining sources.")
    if record_basis == "cli":
        # Attach the live market's implied forecast so the CLI log can later score
        # market-vs-model (the scheduled Action does the same 24/7).
        try:
            from datetime import date
            from sources import kalshi
            snap["market"] = kalshi.implied_block(
                date.fromisoformat(snap["today"]["day"]),
                date.fromisoformat(snap["tomorrow"]["day"]))
        except Exception:
            pass
    try:
        forecast_log.record(snap, basis=record_basis)  # per-basis upsert
    except Exception:
        pass  # logging must never break the dashboard
    try:
        import consensus_log
        consensus_log.record(snap, basis=record_basis)  # intraday time series
    except Exception:
        pass
    bankroll = load_portfolio_value() if record_basis == "cli" else None
    market_view.render_page(snap, calib, adapter, accuracy_loader,
                             recap_loader=load_recap,
                             history_loader=load_calibration_history,
                             bankroll=bankroll)


def robinhood_page():
    _page(ROBINHOOD, load_snapshot, load_accuracy, "hourly")


def kalshi_page():
    _page(KALSHI, load_snapshot_kalshi, load_accuracy_kalshi, "cli")


@st.cache_data(ttl=60, show_spinner="Fetching Wunderground hourly forecast…")
def load_hourly():
    """Wunderground/TWC hourly forecast + Euless PWS current temp for the Hourly
    page. 60s TTL matches the page autorefresh; the source layer's own TTLs
    (300s hourly, 60s PWS) keep this from refetching every cycle."""
    from sources import wunderground
    return wunderground.hourly(), wunderground.pws_current()


def hourly_page():
    hourly_view.render(load_hourly)


def edge_page():
    edge_view.render()


def accuracy_page():
    accuracy_view.render(load_accuracy_kalshi, load_calibration_history)


# Robinhood (hourly-basis) page retired from the live site — the model is now
# Kalshi/CLI-only. robinhood_page() and its hourly loaders are kept below,
# unreferenced, so re-listing it here is a one-line revert if ever needed.
st.navigation([
    st.Page(kalshi_page, title="Forecast", default=True),
    st.Page(hourly_page, title="Hourly"),
    st.Page(accuracy_page, title="Accuracy"),
    st.Page(edge_page, title="Edge"),
    st.Page(bet_view.render, title="History"),
]).run()
