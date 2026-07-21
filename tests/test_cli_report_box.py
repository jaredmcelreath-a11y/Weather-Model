"""The on-page CLIDFW confirmation box."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import market_view

_TZ = ZoneInfo(TIMEZONE)


def _cli():
    return {
        "report_date": datetime(2026, 7, 20).date(),
        "high_f": 100, "low_f": 80,
        "high_time": "254 PM", "low_time": "615 AM",
        "issued": datetime(2026, 7, 20, 16, 41, tzinfo=_TZ),
    }


def test_cli_box_shows_high_low_and_issued():
    html = market_view.cli_report_html(_cli())
    assert "100" in html
    assert "80" in html
    assert "4:41" in html  # localized issuance time
    assert "CLIMATE REPORT" in html.upper()
