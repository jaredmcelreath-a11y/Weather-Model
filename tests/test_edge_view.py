"""Edge Tracker page — pure aggregation tests + import smoke. edge_view imports
streamlit, absent in this dev env, so stub it before importing (see test_recap_render)."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())


def test_pnl_attribution_splits_by_entry_price():
    import edge_view
    rows = [
        {"status": "settled", "entry": 0.70, "pnl": 3.0},    # with-market win
        {"status": "settled", "entry": 0.55, "pnl": -5.5},   # with-market loss
        {"status": "closed",  "entry": 0.30, "pnl": 7.0},    # against-market win
        {"status": "open",    "entry": 0.40, "pnl": 1.0},    # skipped: not realized
        {"status": "settled", "entry": None, "pnl": 2.0},    # skipped: no entry price
    ]
    out = edge_view.pnl_attribution(rows)
    assert out["with_market"] == {"n": 2, "wins": 1, "losses": 1, "net_pnl": -2.5}
    assert out["against_market"] == {"n": 1, "wins": 1, "losses": 0, "net_pnl": 7.0}


def test_pnl_attribution_entry_exactly_half_is_with_market():
    import edge_view
    out = edge_view.pnl_attribution([{"status": "settled", "entry": 0.50, "pnl": 1.0}])
    assert out["with_market"]["n"] == 1
    assert out["against_market"]["n"] == 0


def test_pnl_attribution_empty():
    import edge_view
    out = edge_view.pnl_attribution([])
    assert out["with_market"] == {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
    assert out["against_market"] == {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
