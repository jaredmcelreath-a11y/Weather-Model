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


from datetime import date


def test_assemble_headline_rolls_up_all_subset():
    import edge_view
    rows = [
        {"target_date": "2026-07-01", "variable": "high", "capture_slot": "15:30",
         "cli_consensus": 97.9, "flat_offset": 0.89, "live_gap": 1.2,
         "market_ev": 96.0, "market_buckets": [[None, 96, 0.6], [97, 98, 0.4]]},
    ]
    cli_map = {date(2026, 7, 1): (98.0, 79.0)}       # actual high 98 -> bucket (97,98)
    hourly_map = {date(2026, 7, 1): (97.0, 79.0)}
    out = edge_view.assemble(rows, cli_map, hourly_map)
    h = out["headline"]
    # model 97.9 -> (97,98) == actual; market top bucket (None,96) != actual -> model wins
    assert h == {"n": 1, "disagreements": 1, "model_wins": 1, "market_wins": 0}
    assert ("15:30", "high", "all") in out["metrics"]


def test_assemble_empty_is_zeroed():
    import edge_view
    out = edge_view.assemble([], {}, {})
    assert out["headline"] == {"n": 0, "disagreements": 0, "model_wins": 0, "market_wins": 0}
    assert out["metrics"] == {}


def test_assemble_surfaces_low_slot():
    import edge_view
    rows = [
        {"target_date": "2026-07-02", "variable": "low", "capture_slot": "sr",
         "cli_consensus": 76.2, "flat_offset": -0.36, "live_gap": None,
         "market_ev": 76.0, "market_buckets": [[75, 76, 0.6], [77, 78, 0.4]]},
    ]
    cli_map = {date(2026, 7, 2): (95.0, 76.0)}       # (high, low); low settles 76
    hourly_map = {date(2026, 7, 2): (94.0, 75.0)}
    out = edge_view.assemble(rows, cli_map, hourly_map)
    assert ("sr", "low", "all") in out["metrics"]
    assert out["headline"]["n"] == 1


def test_offset_verdict_high_all_subset_only():
    import edge_view
    metrics = {
        ("15:30", "high", "all"): {
            "flat_rmse": 0.90, "live_rmse": 0.60, "flip_toward": 3, "flip_away": 1,
            "n": 5, "model_mae": 1.0, "market_mae": 1.2,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
        ("15:30", "high", "boundary"): {  # ignored: not the 'all' subset
            "flat_rmse": 0.5, "live_rmse": 0.5, "flip_toward": 0, "flip_away": 0,
            "n": 1, "model_mae": 1.0, "market_mae": 1.2,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
        ("09:00", "low", "all"): {  # ignored: low has no offset predictor
            "flat_rmse": None, "live_rmse": None, "flip_toward": None, "flip_away": None,
            "n": 5, "model_mae": 1.0, "market_mae": 1.1,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
    }
    lines = edge_view._offset_verdict(metrics)
    assert len(lines) == 1
    assert "15:30" in lines[0] and "live gap beats flat" in lines[0]


def test_edge_view_exposes_render():
    import edge_view
    assert hasattr(edge_view, "render")
    assert callable(edge_view.render)


def test_edge_rows_shows_volume_and_thin_marker():
    import edge_view
    metrics = {
        ("15:30", "high", "all"): {
            "n": 4, "model_mae": 1.0, "market_mae": 1.2, "disagreements": 2,
            "model_bin_wins": 1, "market_bin_wins": 1,
            "market_volume": 7.5, "thin": True},
        ("15:30", "high", "mid_bin"): {
            "n": 3, "model_mae": 1.0, "market_mae": 1.2, "disagreements": 1,
            "model_bin_wins": 1, "market_bin_wins": 0,
            "market_volume": None, "thin": False},
    }
    rows = edge_view._edge_rows(metrics)
    by_type = {r["day type"]: r for r in rows}
    assert "⚠ all" in by_type                     # thin subset flagged
    assert by_type["⚠ all"]["volume"] == "7.5"
    assert "mid-bin" in by_type                    # not thin, no marker
    assert by_type["mid-bin"]["volume"] == "—"     # unknown volume


def test_render_imports_kalshi_auth_from_sources_package():
    # render() can't be executed in this env (no streamlit/cryptography), so its
    # runtime imports aren't exercised by the smoke test. kalshi_auth lives in the
    # `sources` package — a bare `import kalshi_auth` is a ModuleNotFoundError on
    # deploy. Guard the correct path by inspecting the source.
    import inspect
    import edge_view
    src = inspect.getsource(edge_view.render)
    assert "from sources import kalshi_auth" in src
    assert "\n    import kalshi_auth" not in src  # never the bare top-level form
