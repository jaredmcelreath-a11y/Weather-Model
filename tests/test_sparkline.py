"""Unicode-block sparkline for the calibration-drift view."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

from market_view import sparkline


def test_sparkline_maps_series_low_to_high():
    s = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert len(s) == 8
    assert s[0] == "▁" and s[-1] == "█"


def test_sparkline_flat_series_is_uniform():
    assert len(set(sparkline([3, 3, 3]))) == 1


def test_sparkline_skips_none_gaps():
    s = sparkline([1, None, 8])
    assert len(s) == 2 and s[0] == "▁" and s[-1] == "█"


def test_sparkline_empty_and_single():
    assert sparkline([]) == ""
    assert sparkline([None]) == ""
    assert len(sparkline([5])) == 1
