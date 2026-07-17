"""HTML builders for the Morning Recap card and Storm Watch panel."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

from market_view import morning_recap_html, storm_watch_html


def test_storm_watch_all_clear():
    s = {"level": "clear", "pop": 4.0, "sigma": 0.0,
         "upstream": {"active": False, "county": None, "direction": None}}
    html = storm_watch_html(s)
    assert "STORM WATCH" in html and "all clear" in html
    assert "4%" in html
    assert "No active severe warnings" in html
    assert "no convective downside" in html


def test_storm_watch_active_with_warning():
    s = {"level": "active", "pop": 55.0, "sigma": 3.0,
         "upstream": {"active": True, "county": "Johnson", "direction": "SW"}}
    html = storm_watch_html(s)
    assert "active" in html
    assert "Johnson Co (SW)" in html
    assert "±3°F" in html
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
                 "market": None, "market_closer": None}}


def test_morning_recap_renders_both_sections():
    html = morning_recap_html(_TODAY, _YDAY)
    assert "MORNING RECAP" in html
    assert "2026-07-17" in html and "2026-07-18" in html
    assert "exact" in html                          # low hit
    assert "model closer on the high" in html       # market_closer False -> model closer


def test_morning_recap_without_yesterday():
    html = morning_recap_html(_TODAY, None)
    assert "MORNING RECAP" in html and "Yesterday" not in html
    assert "Today" in html
