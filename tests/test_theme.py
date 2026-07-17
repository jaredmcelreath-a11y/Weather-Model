"""Regression tests for theme persistence.

Bug: the theme was stored under `wx_theme`, which was ALSO the st.radio widget
key. Streamlit purges widget-keyed session state when the widget isn't rendered
(e.g. the History page injects the theme but never draws the picker). On the next
render the radio, lacking an explicit index, fell back to option 0 = "Deep slate"
and wrote it back — so the palette silently reverted after a page switch / rerun.

The fix keeps `wx_theme` as a plain (non-widget) store and always derives the
radio's index from the stored theme. These guard the two invariants.
"""
import sys

try:
    import streamlit  # noqa: F401
except ModuleNotFoundError:
    from unittest.mock import MagicMock
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import market_view as mv


def test_resolve_prefers_valid_session_theme():
    assert mv._resolve_theme("Charcoal", None) == "Charcoal"
    assert mv._resolve_theme("Deep slate", "Charcoal") == "Deep slate"


def test_resolve_falls_back_to_query_then_default():
    assert mv._resolve_theme(None, "Charcoal") == "Charcoal"
    assert mv._resolve_theme(None, None) == mv.DEFAULT_THEME
    assert mv._resolve_theme("bogus", "bogus") == mv.DEFAULT_THEME


def test_theme_index_reflects_current_theme_never_zero_fallback():
    # The bug was a purged radio re-initializing to index 0 ("Deep slate").
    # The index helper must return the CURRENT theme's index for every theme.
    themes = list(mv.THEMES)
    for t in themes:
        assert themes[mv._theme_index(t)] == t
    # An invalid/absent theme resolves to the DEFAULT's index (Charcoal), not 0.
    assert themes[mv._theme_index("bogus")] == mv.DEFAULT_THEME
