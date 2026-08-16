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


def _row(dt, temp=90, feels=98, dew=70, precip=5, cloud=40, hum=45, wind=8, wdir="S"):
    return {"time": dt, "temp": temp, "feels": feels, "dew": dew,
            "precip_pct": precip, "cloud_pct": cloud, "humidity": hum,
            "wind_mph": wind, "wind_dir": wdir}


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


def test_day_tables_splits_by_day_with_highlow_and_dew():
    import hourly_view
    today = date(2026, 7, 18)
    rows = [
        _row(datetime(2026, 7, 18, 23, tzinfo=_TZ), temp=95),
        _row(datetime(2026, 7, 19, 0, tzinfo=_TZ), temp=80),
        _row(datetime(2026, 7, 19, 1, tzinfo=_TZ), temp=79),
    ]
    tables = hourly_view._day_tables(rows, today)
    assert [t["label"] for t in tables] == ["Today", "Tomorrow"]
    today_t = tables[0]
    assert list(today_t["df"].columns) == ["Time", "Temp", "Feels", "Dew",
                                           "Rain %", "Cloud", "Wind", "Humidity"]
    assert "Day" not in today_t["df"].columns
    assert (today_t["high"], today_t["low"]) == (95, 95)
    assert len(today_t["df"]) == 1
    tomorrow_t = tables[1]
    assert (tomorrow_t["high"], tomorrow_t["low"]) == (80, 79)
    assert len(tomorrow_t["df"]) == 2


def test_day_tables_highlow_ignores_missing_temps():
    import hourly_view
    today = date(2026, 7, 18)
    rows = [
        _row(datetime(2026, 7, 18, 12, tzinfo=_TZ), temp=None),
        _row(datetime(2026, 7, 18, 13, tzinfo=_TZ), temp=98),
        _row(datetime(2026, 7, 18, 14, tzinfo=_TZ), temp=88),
    ]
    (t,) = hourly_view._day_tables(rows, today)
    assert (t["high"], t["low"]) == (98, 88)


def test_day_tables_highlow_none_when_all_temps_missing():
    import hourly_view
    today = date(2026, 7, 18)
    rows = [_row(datetime(2026, 7, 18, 13, tzinfo=_TZ), temp=None)]
    (t,) = hourly_view._day_tables(rows, today)
    assert t["high"] is None and t["low"] is None


def test_radar_html_contains_source_map_and_controls():
    import hourly_view
    html = hourly_view._radar_html(lat=32.90, lon=-97.04)
    # RainViewer client-side source + dark base map + Leaflet + a play/pause hook.
    assert "api.rainviewer.com/public/weather-maps.json" in html
    assert "leaflet" in html.lower()
    assert "basemaps.cartocdn.com/dark" in html
    assert "playpause" in html.lower() or "play/pause" in html.lower()
    # KDFW default center.
    assert "32.9" in html and "-97.04" in html


def test_radar_html_has_time_slider():
    import hourly_view
    html = hourly_view._radar_html(lat=32.90, lon=-97.04)
    assert 'type="range"' in html
    assert 'id="slider"' in html
    # zoom control moved to top-right so it doesn't overlap the top-left slider
    assert "zoomControl:false" in html.replace(" ", "")
    assert "topright" in html


def test_radar_html_honors_custom_center_and_zoom():
    import hourly_view
    html = hourly_view._radar_html(lat=40.0, lon=-105.0, zoom=9)
    assert "40.0" in html and "-105.0" in html
    assert "9" in html


def test_radar_html_defaults_to_charcoal_palette():
    import hourly_view
    import market_view
    html = hourly_view._radar_html(lat=32.90, lon=-97.04)
    ch = market_view.THEMES["Charcoal"]
    # charcoal background + green accent replace the old cool-black/amber
    assert ch["bg"] in html
    assert ch["accent"] in html
    assert "#f0b34a" not in html  # old amber accent gone


def test_radar_html_uses_supplied_palette():
    import hourly_view
    pal = {"bg": "#010203", "surface": "#040506", "ink": "#0a0b0c",
           "muted": "#0d0e0f", "accent": "#123456", "accent_strong": "#654321",
           "border": "rgba(9,8,7,0.2)"}
    html = hourly_view._radar_html(lat=32.90, lon=-97.04, palette=pal)
    assert "#010203" in html and "#123456" in html and "#654321" in html


def test_render_exposed_and_callable():
    import hourly_view
    assert callable(hourly_view.render)


def test_render_degrades_when_loader_raises(monkeypatch):
    import hourly_view
    # Loader failure must not raise out of render — the page warns instead.
    def boom():
        raise RuntimeError("twc down")
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    hourly_view.render(boom)  # should not raise


def test_render_accepts_a_modeled_city(monkeypatch):
    import hourly_cities
    import hourly_view
    # Austin: no PWS, empty rows — render must accept a city and not raise.
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    hourly_view.render(lambda: ([], None), cli_report=None,
                       city=hourly_cities.city("AUS"))


def test_current_reads_the_citys_station_and_converts_to_its_zone(monkeypatch):
    import hourly_cities
    import hourly_view
    from sources import metar_tgftp, nws_observations

    seen = {}

    def fake_latest(station_id, **kw):
        seen["station"] = station_id
        return {"temp": 86.0,
                "time": datetime(2026, 8, 7, 18, 53, tzinfo=ZoneInfo("UTC"))}

    monkeypatch.setattr(nws_observations, "latest", fake_latest)
    # _current now reads two feeds; silence the second so this test stays about
    # station threading and zone conversion (and stays off the network).
    monkeypatch.setattr(metar_tgftp, "latest_for_id", lambda *a, **k: None)
    got = hourly_view._current(hourly_cities.city("LAX"))
    assert seen["station"] == "KLAX"
    # 18:53Z is 11:53 in Los Angeles, not 13:53 Central.
    assert (got["time"].hour, got["time"].minute) == (11, 53)


def test_current_is_none_when_the_station_has_nothing(monkeypatch):
    import hourly_cities
    import hourly_view
    from sources import metar_tgftp, nws_observations
    monkeypatch.setattr(nws_observations, "latest", lambda *a, **k: None)
    monkeypatch.setattr(metar_tgftp, "latest_for_id", lambda *a, **k: None)
    assert hourly_view._current(hourly_cities.city("ATL")) is None


def test_render_accepts_a_reference_city(monkeypatch):
    import hourly_cities
    import hourly_view
    # Miami: no PWS, no rows — render must not raise and must not touch config.
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    hourly_view.render(lambda: ([], None), cli_report=None,
                       city=hourly_cities.city("MIA"))


def test_render_centers_the_radar_on_the_city(monkeypatch):
    import hourly_cities
    import hourly_view
    seen = {}
    monkeypatch.setattr(hourly_view, "_current", lambda city: None)
    monkeypatch.setattr(hourly_view, "_radar_html",
                        lambda lat, lon, palette=None: seen.update(lat=lat, lon=lon) or "")
    rows = [_row(datetime(2026, 8, 7, 13, tzinfo=ZoneInfo("America/Denver")))]
    hourly_view.render(lambda: (rows, None), city=hourly_cities.city("DEN"))
    assert (round(seen["lat"], 2), round(seen["lon"], 2)) == (39.86, -104.67)


# ---- The current-temperature box: two feeds, newest wins --------------------
#
# The Hourly page's 20 reference cities read api.weather.gov's 5-minute feed,
# which lands ~20 min late and in whole °C. The routine :53 METAR arrives via
# tgftp in ~2 min and in tenths. Same rule the Forecast page uses.

def _reading(hour, minute, temp):
    from datetime import timezone
    return {"temp": temp, "time": datetime(2026, 8, 16, hour, minute,
                                           tzinfo=timezone.utc)}


def test_hourly_box_prefers_the_metar_while_the_5_minute_feed_lags():
    import hourly_view
    got = hourly_view.freshest(_reading(21, 50, 100.4), _reading(21, 53, 102.02),
                               "America/Chicago")
    assert got["temp"] == 102.02
    assert got["time"].hour == 16          # localised to the city, not UTC


def test_hourly_box_keeps_the_5_minute_feed_once_it_is_ahead():
    import hourly_view
    got = hourly_view.freshest(_reading(22, 20, 103.1), _reading(21, 53, 102.02),
                               "America/Chicago")
    assert got["temp"] == 103.1


def test_hourly_box_localises_to_the_citys_own_zone_not_the_project_default():
    # A Miami reading stamped Central is the bug this page's module docstring
    # warns about: the page spans four timezones.
    import hourly_view
    got = hourly_view.freshest(None, _reading(21, 53, 90.0), "America/New_York")
    assert got["time"].hour == 17          # 21:53Z is 5:53pm EDT


def test_hourly_box_survives_either_feed_being_down():
    import hourly_view
    assert hourly_view.freshest(_reading(21, 50, 100.4), None,
                                "America/Chicago")["temp"] == 100.4
    assert hourly_view.freshest(None, None, "America/Chicago") is None
