"""Accuracy Scorecard page — how good the forecast itself is, the complement to
the betting-P&L History page. Reuses market_view._render_accuracy for the detailed
body and adds glanceable headline tiles on top."""
from __future__ import annotations

import streamlit as st

import calibration
import market_view
from markets import KALSHI


def _pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def _num(v) -> str:
    return f"{v:.2f}" if v is not None else "—"


def headline_tiles(live: dict) -> list[dict]:
    """Glanceable accuracy tiles from scoring.score()'s live dict: each metric
    paired High-then-Low (Exact-Bin, Within ±1, Brier), with the settled-day
    count last. Missing variables are skipped; None metrics render as an em dash."""
    by_var = live.get("by_variable") or {}
    tiles = []
    for key, name, fmt in (("exact_peak", "Exact-Bin", _pct),
                           ("within1", "Within ±1", _pct),
                           ("brier", "Brier", _num)):
        for var in ("high", "low"):
            m = by_var.get(var)
            if not m:
                continue
            tiles.append({"label": f"{var.capitalize()} {name}", "value": fmt(m.get(key))})
    tiles.append({"label": "Settled Days", "value": str(live.get("n_settled", 0) or 0)})
    return tiles


def render(load_accuracy, history_loader=None):
    """Draw the Accuracy Scorecard: headline tiles + the full self-scoring /
    reliability / calibration-drift body (market_view._render_accuracy).
    `load_accuracy` is the cached () -> (bt, live) callable; `history_loader`
    the cached () -> calibration-history rows."""
    market_view._theme_controls()  # sidebar Settings (theme picker) + injects theme
    st.title("Accuracy")

    try:
        _bt, live = load_accuracy()
    except Exception:
        live = None
    if live and live.get("n_settled"):
        tiles = headline_tiles(live)
        with st.container(key="metrics2_accuracy"):
            cols = st.columns(len(tiles))
        for col, t in zip(cols, tiles):
            col.markdown(market_view.metric_card(t["label"], t["value"]),
                         unsafe_allow_html=True)

    if KALSHI.accuracy_note:
        st.caption(KALSHI.accuracy_note)

    calib = None
    try:
        calib = calibration.get()
    except Exception:
        pass
    market_view._render_accuracy(load_accuracy, calib, history_loader=history_loader)
