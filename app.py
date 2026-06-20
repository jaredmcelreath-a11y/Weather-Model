"""KDFW high/low probability dashboard.

Run with:  streamlit run app.py
Two pages — Robinhood (ForecastEx) and Kalshi — switchable via the sidebar nav.
Each page auto-refreshes; switch Today/Tomorrow and the safe-hold floor in the
sidebar. All rendering lives in market_view; exchange differences in markets.
"""

from __future__ import annotations

import os

import streamlit as st

import calibration
import forecast_log
import market_view
import model
from markets import KALSHI, ROBINHOOD

st.set_page_config(page_title="KDFW Temp Markets", layout="wide")

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


@st.cache_data(ttl=120, show_spinner="Fetching forecasts and observations…")
def load_snapshot():
    calib = calibration.get(refresh=True)
    return model.snapshot(calib), calib


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


def _page(adapter):
    snap, calib = load_snapshot()
    try:
        forecast_log.record(snap)  # forward log for self-scoring; upsert, idempotent
    except Exception:
        pass  # logging must never break the dashboard
    market_view.render_page(snap, calib, adapter, load_accuracy)


def robinhood_page():
    _page(ROBINHOOD)


def kalshi_page():
    _page(KALSHI)


st.navigation([
    st.Page(robinhood_page, title="Robinhood", icon="🪶", default=True),
    st.Page(kalshi_page, title="Kalshi", icon="📈"),
]).run()
