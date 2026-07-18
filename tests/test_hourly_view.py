"""Hourly page — pure chart/table helpers + render smoke. hourly_view imports
streamlit (and market_view), absent/heavy in this dev env, so stub streamlit
before importing."""
import sys
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _row(dt, temp=90, feels=98, precip=5, cloud=40, hum=45, wind=8, wdir="S"):
    return {"time": dt, "temp": temp, "feels": feels, "precip_pct": precip,
            "cloud_pct": cloud, "humidity": hum, "wind_mph": wind, "wind_dir": wdir}


def test_chart_frame_two_series_per_hour():
    import hourly_view
    rows = [_row(datetime(2026, 7, 18, 13, tzinfo=_TZ), temp=96, feels=104)]
    frame = hourly_view.chart_frame(rows)
    by = {r["series"]: r["degF"] for r in frame}
    assert by == {"Temp": 96, "Feels": 104}
    assert all(r["time"] == rows[0]["time"] for r in frame)


def test_chart_frame_skips_none_temps():
    import hourly_view
    rows = [_row(datetime(2026, 7, 18, 13, tzinfo=_TZ), temp=None, feels=None)]
    assert hourly_view.chart_frame(rows) == []


def test_day_label_today_tomorrow_then_weekday():
    import hourly_view
    today = date(2026, 7, 18)
    assert hourly_view.day_label(datetime(2026, 7, 18, 5, tzinfo=_TZ), today) == "Today"
    assert hourly_view.day_label(datetime(2026, 7, 19, 5, tzinfo=_TZ), today) == "Tomorrow"
    # two days out falls back to the weekday name
    assert hourly_view.day_label(datetime(2026, 7, 20, 5, tzinfo=_TZ), today) == "Monday"


def test_cell_formatters():
    import hourly_view
    assert hourly_view.fmt_temp(96) == "96°"
    assert hourly_view.fmt_temp(None) == "—"
    assert hourly_view.fmt_pct(5) == "5%"
    assert hourly_view.fmt_pct(None) == "—"
    assert hourly_view.fmt_wind(8, "S") == "S 8"
    assert hourly_view.fmt_wind(None, None) == "—"


def test_render_exposed_and_callable():
    import hourly_view
    assert callable(hourly_view.render)


def test_render_degrades_when_loader_raises(monkeypatch):
    import hourly_view
    # Loader failure must not raise out of render — the page warns instead.
    def boom():
        raise RuntimeError("twc down")
    monkeypatch.setattr(hourly_view, "_kdfw_current", lambda: None)
    hourly_view.render(boom)  # should not raise
