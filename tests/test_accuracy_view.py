"""Accuracy Scorecard — pure tile builder + import smoke. accuracy_view imports
streamlit, absent in this dev env, so stub it before importing."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())


def test_headline_tiles_formats_values():
    import accuracy_view
    live = {"n_settled": 22, "by_variable": {
        "high": {"n": 22, "brier": 0.12, "exact_peak": 82.0, "within1": 95.0},
        "low": {"n": 22, "brier": 0.15, "exact_peak": 74.0, "within1": 90.0},
    }}
    tiles = accuracy_view.headline_tiles(live)
    by = {t["label"]: t["value"] for t in tiles}
    assert by["Settled days"] == "22"
    assert by["High exact-bin"] == "82%"
    assert by["Low exact-bin"] == "74%"
    assert by["High within ±1"] == "95%"
    assert by["High Brier"] == "0.12"


def test_headline_tiles_handles_missing_and_none():
    import accuracy_view
    # No settled data at all -> just the count tile, no crash.
    tiles = accuracy_view.headline_tiles({"n_settled": 0, "by_variable": {}})
    assert tiles == [{"label": "Settled days", "value": "0"}]
    # None metric renders as an em dash, not a crash.
    tiles = accuracy_view.headline_tiles(
        {"n_settled": 3, "by_variable": {"high": {"n": 3, "brier": None,
                                                  "exact_peak": None, "within1": None}}})
    by = {t["label"]: t["value"] for t in tiles}
    assert by["High exact-bin"] == "—"
    assert by["High Brier"] == "—"


def test_accuracy_view_exposes_render():
    import accuracy_view
    assert hasattr(accuracy_view, "render")
    assert callable(accuracy_view.render)
