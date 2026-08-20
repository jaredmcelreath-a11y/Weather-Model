"""The Screen's storm gate reads TWC as well as the NWS grid.

2026-08-19 at KPHX: the grid sat at 15-18% all evening while TWC had the same
hours at 99%. That evening produced a 59kt gust and a 27F crash in half an hour,
settling the low at 79 against a bracket the Screen was carrying as quiet.
"""
from datetime import datetime, timedelta, timezone

import scan_log
import screen

_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)     # 17:18 LST Phoenix
_MST = timezone(timedelta(hours=-7))

# The NWS grid as it actually read that evening: dust, and a thunder hour at 15%.
_PERIODS = [
    {"startTime": "2026-08-09T06:00:00-07:00", "temperature": 93,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Clear"},
    {"startTime": "2026-08-09T18:00:00-07:00", "temperature": 109,
     "probabilityOfPrecipitation": {"value": 16},
     "shortForecast": "Patchy Blowing Dust"},
    {"startTime": "2026-08-09T23:00:00-07:00", "temperature": 96,
     "probabilityOfPrecipitation": {"value": 15},
     "shortForecast": "Slight Chance Showers And Thunderstorms"},
]

# TWC's read of the same hours. Icon 3 is "Strong Storms" -- a convective hour
# whose phrase contains no "thunder" for the NWS word rule to match on.
_TWC_ROWS = [
    {"time": datetime(2026, 8, 9, 21, tzinfo=_MST), "temp": 86,
     "precip_pct": 99, "icon": 3, "phrase": "Strong Storms"},
    {"time": datetime(2026, 8, 9, 22, tzinfo=_MST), "temp": 84,
     "precip_pct": 88, "icon": 4, "phrase": "Thunderstorms"},
]


def _market():
    return {"ticker": "KXLOWTPHX-26AUG09-T91", "yes_bid_dollars": "0.3600",
            "yes_ask_dollars": "0.3900", "no_bid_dollars": "0.6100",
            "yes_sub_title": "92° or above", "floor_strike": 91,
            "cap_strike": None, "strike_type": "greater", "volume_fp": "9381",
            "close_time": "2026-08-10T07:00:00Z"}


def _obs(temp_c=33.9):        # 93.0F
    return [{"properties": {"timestamp": f"2026-08-09T{h:02d}:00:00+00:00",
                            "temperature": {"value": temp_c}}}
            for h in (13, 14)]


def _deps(written, **over):
    kwargs = dict(
        list_series=lambda: [{"ticker": "KXLOWTPHX"}],
        list_markets=lambda series, status=None: [_market()],
        resolve_point=lambda lat, lon: {
            "timezone": "America/Phoenix",
            "forecast_hourly": "https://example.test/hourly",
            "stations_url": "https://example.test/stations"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: _obs(),
        append_rows=lambda path, rows: written.setdefault(path, []).extend(rows) or len(rows),
        station_for=lambda url: "KPHX",
        sleep=lambda s: None,
    )
    kwargs.update(over)
    return screen.Deps(**kwargs)


def _storm_of(written):
    rows = written.get(scan_log.LOCKED_PATH) or []
    assert rows, "expected a YES row to carry the storm number"
    return rows[0]["storm"]


def test_the_screen_reports_twcs_storm_number_over_the_grids():
    written = {}
    screen.screen_pass(_NOW, _deps(written, fetch_twc=lambda lat, lon: _TWC_ROWS))
    assert _storm_of(written) == 99


def test_the_grid_alone_still_answers_when_twc_is_not_wired():
    # Every existing caller builds Deps without fetch_twc.
    written = {}
    screen.screen_pass(_NOW, _deps(written))
    assert _storm_of(written) == 15


def test_a_failing_twc_fetch_degrades_to_the_grid():
    # The feed is unofficial; losing it must cost the pass its second opinion,
    # not its rows.
    def boom(lat, lon):
        raise RuntimeError("twc down")

    written = {}
    screen.screen_pass(_NOW, _deps(written, fetch_twc=boom))
    assert _storm_of(written) == 15
