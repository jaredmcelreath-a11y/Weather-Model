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
    # The default is embedded as the literal DEFAULT constant, for the no-hash
    # first paint. Asserted on the exact literal — a bare '"low"' also matches
    # wx-show-low / #wxlow / (h === "wxlow"), so it would pass for ANY default
    # and guard nothing.
    assert 'var DEFAULT = "low";' in js
    assert 'var DEFAULT = "high";' not in js
    # both hash tokens the bar toggles between
    assert "wxhigh" in js and "wxlow" in js
    # reaches the same-origin parent document (the Streamlit component pattern)
    assert "window.parent" in js
    # it's a script block ready for components.html
    assert js.strip().startswith("<script>")
    assert js.strip().endswith("</script>")


def test_bridge_embeds_high_default():
    # The discriminating counterpart: 'high' embeds its own DEFAULT literal and
    # not the 'low' one — together with the test above this proves the argument
    # actually drives the emitted default.
    js = mobile_toggle_bridge_js("high")
    assert 'var DEFAULT = "high";' in js
    assert 'var DEFAULT = "low";' not in js


def test_bridge_rejects_bad_default():
    with pytest.raises(ValueError):
        mobile_toggle_bridge_js("middle")
