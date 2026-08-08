"""screen_pass publishes the small reference the fast alert loop reads."""
from datetime import datetime, timezone

import screen

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)

_PERIODS = [
    {"startTime": "2026-08-07T06:00:00-06:00", "temperature": 61,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Clear"},
    {"startTime": "2026-08-07T15:00:00-06:00", "temperature": 94,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Sunny"},
]


def _market(ticker):
    return {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.35",
            "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
            "strike_type": "greater", "volume_fp": "100",
            "close_time": "2026-08-08T05:59:00Z"}


def _deps(published):
    return screen.Deps(
        list_series=lambda: [{"ticker": "KXLOWTDEN"}],
        list_markets=lambda series, status=None: [_market("KXLOWTDEN-26AUG07-T71")],
        resolve_point=lambda lat, lon: {
            "timezone": "America/Denver",
            "forecast_hourly": "https://example.test/hourly",
            "stations_url": "https://example.test/stations"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: [],
        append_rows=lambda path, rows: len(rows),
        station_for=lambda url: "KDEN",
        sleep=lambda s: None,
        write_reference=lambda obj: published.append(obj),
    )


def test_reference_carries_station_timezone_and_extreme():
    published = []
    screen.screen_pass(_NOW, _deps(published))
    city = published[0]["cities"]["KXLOWTDEN"]
    assert city["station"] == "KDEN"
    assert city["timezone"] == "America/Denver"
    # The LOW series publishes the day's low, unfolded by realized temperature —
    # the alerter re-folds it against its own fresher observations.
    assert city["days"]["2026-08-07"] == 61.0


def test_reference_is_stamped_with_the_pass_time():
    published = []
    screen.screen_pass(_NOW, _deps(published))
    assert published[0]["generated"] == "2026-08-07T18:30:00Z"


def test_a_failed_reference_write_does_not_lose_the_candidates():
    # Candidates are the product; the reference is a convenience for the alerter.
    def boom(obj):
        raise RuntimeError("contents API down")

    deps = _deps([])
    deps.write_reference = boom
    got = screen.screen_pass(_NOW, deps)
    assert got["cities"] == 1
