"""The on-page CLIDFW confirmation box (Hourly page)."""
import sys
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from config import TIMEZONE

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import hourly_view

_TZ = ZoneInfo(TIMEZONE)


def _cli():
    return {
        "report_date": datetime(2026, 7, 20).date(),
        "high_f": 100, "low_f": 80,
        "high_time": "254 PM", "low_time": "615 AM",
        "issued": datetime(2026, 7, 20, 16, 41, tzinfo=_TZ),
    }


def test_cli_box_value_shows_high_low_and_issued():
    value, issued = hourly_view.cli_report_box(_cli())
    assert value == "100° / 80°"
    assert issued == "4:41 PM"


def test_cli_box_none_when_no_report():
    assert hourly_view.cli_report_box(None) is None
