"""HTML builders for the Morning Recap card and Storm Watch panel."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

from market_view import (briefing_bridge_js, briefing_overlay_html,
                         morning_recap_html, storm_watch_html)


def test_storm_watch_all_clear_title_case():
    s = {"level": "clear", "pop": 4.0, "sigma": 0.0,
         "upstream": {"active": False, "county": None, "direction": None}}
    html = storm_watch_html(s)
    assert "STORM WATCH" in html and "All Clear" in html
    assert "4%" in html
    assert "No Active Severe Warnings" in html
    assert "No Convective Downside" in html


def test_storm_watch_active_with_warning_title_case():
    s = {"level": "active", "pop": 55.0, "sigma": 3.0,
         "upstream": {"active": True, "county": "Johnson", "direction": "SW"}}
    html = storm_watch_html(s)
    assert "Active" in html
    assert "SVR Warning: Johnson Co (SW)" in html
    assert "Low at Risk" in html and "±3°F" in html
    assert "55%" in html


def test_storm_watch_empty_when_none():
    assert storm_watch_html(None) == ""


_TODAY = {"date": "2026-07-18",
          "high": {"consensus": 99.2, "top_bin": ["99", 0.41], "market_ev": 98.9,
                   "locked": False},
          "low": {"observed": 78.0, "consensus": 78.0, "market_ev": 78.1,
                  "locked": True}}
_YDAY = {"date": "2026-07-17",
         "high": {"settled": 100.0, "model": 99.0, "exact": False, "diff": -1.0,
                  "market": 98.5, "market_closer": False},
         "low": {"settled": 77.0, "model": 77.0, "exact": True, "diff": 0.0,
                 "market": None, "market_closer": None},
         "pnl": {"net": 42.0, "pct": 18.0, "n": 3, "wins": 2, "losses": 1}}


def test_morning_recap_title_case_and_pnl():
    html = morning_recap_html(_TODAY, _YDAY)
    assert "MORNING RECAP" in html
    assert "2026-07-17" in html and "2026-07-18" in html
    assert "High Settled 100 · Model 99 (Miss +1)" in html
    assert "Low Settled 77 · Model 77 (Exact ✓)" in html
    assert "Market: Model Closer on the High" in html   # relabeled + title case
    assert "+$42" in html and "+18%" in html and "3 Settled Bets" in html
    # lowercase content should not leak through
    assert "settled 100" not in html and "model closer" not in html


def test_morning_recap_without_yesterday():
    html = morning_recap_html(_TODAY, None)
    assert "MORNING RECAP" in html and "Yesterday" not in html
    assert "Today" in html


def test_morning_recap_without_pnl_omits_line():
    yday = {k: v for k, v in _YDAY.items() if k != "pnl"}
    html = morning_recap_html(_TODAY, yday)
    assert "Settled Bet" not in html


def test_briefing_overlay_wraps_cards_with_fab_and_close():
    html = briefing_overlay_html("<div>CARDS</div>")
    assert 'class="wx-fab"' in html and 'data-wx-briefing="open"' in html
    assert 'data-wx-briefing="close"' in html            # backdrop + ✕
    assert "Daily Briefing" in html
    assert "<div>CARDS</div>" in html


def test_briefing_overlay_handles_empty_cards():
    assert "wx-briefing-panel" in briefing_overlay_html("")


def test_briefing_bridge_toggles_body_class_and_hash():
    js = briefing_bridge_js()
    assert "wx-briefing-open" in js
    assert "data-wx-briefing" in js
    assert "#briefing" in js and "replaceState" in js
