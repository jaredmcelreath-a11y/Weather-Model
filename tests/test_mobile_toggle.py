"""Unit tests for the mobile High/Low floating toggle helpers in market_view.

Presentation-only helpers: they build the bar's HTML and the JS bridge string.
Pure functions (no Streamlit, no network), mirroring tests/test_prob_chart.py.
"""

import pytest

from market_view import mobile_toggle_bar_html, mobile_toggle_bridge_js


def test_bar_shows_both_consensus_values():
    html = mobile_toggle_bar_html({"consensus": 98}, {"consensus": 78})
    # both buttons present, tagged for the JS bridge to wire
    assert 'data-wx-sel="high"' in html
    assert 'data-wx-sel="low"' in html
    # live values rendered on the bar
    assert "98°F" in html
    assert "78°F" in html
    assert "wx-toggle-bar" in html


def test_bar_handles_missing_blocks():
    # None block, or a block with no consensus, renders an em dash — not a crash
    html = mobile_toggle_bar_html(None, {"consensus": None})
    assert html.count("—") == 2
    assert "°F" not in html


def test_bridge_embeds_default_and_reaches_parent():
    js = mobile_toggle_bridge_js("low")
    # default selection is embedded for the no-hash first paint
    assert '"low"' in js
    # both hash tokens the bar toggles between
    assert "wxhigh" in js and "wxlow" in js
    # reaches the same-origin parent document (the Streamlit component pattern)
    assert "window.parent" in js
    # it's a script block ready for components.html
    assert js.strip().startswith("<script>")
    assert js.strip().endswith("</script>")


def test_bridge_rejects_bad_default():
    with pytest.raises(ValueError):
        mobile_toggle_bridge_js("middle")
