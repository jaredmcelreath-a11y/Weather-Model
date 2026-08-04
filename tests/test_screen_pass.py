from datetime import datetime, timezone

import screen

_NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)

_PERIODS = [
    {"startTime": "2026-08-03T02:00:00-06:00", "temperature": 66},
    {"startTime": "2026-08-03T15:00:00-06:00", "temperature": 94},
]


def _market(ticker, floor, cap, ask="0.3500"):
    return {"ticker": ticker, "status": "active", "strike_type": "between",
            "floor_strike": floor, "cap_strike": cap,
            "yes_bid_dollars": "0.3300", "yes_ask_dollars": ask,
            "volume_fp": "50.00", "close_time": "2026-08-04T05:59:00Z"}


def _deps(sink, markets, obs=None):
    return screen.Deps(
        list_series=lambda: [{"ticker": "KXLOWTDEN", "title": "Denver low"}],
        list_markets=lambda s, status=None: markets,
        resolve_point=lambda lat, lon: {"timezone": "America/Denver",
                                        "forecast_hourly": "f",
                                        "stations_url": "s"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: obs or [],
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        station_for=lambda url: "KDEN",
        sleep=lambda s: None,
    )


def test_a_far_richly_priced_bracket_is_flagged_from_the_forecast():
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    out = screen.screen_pass(_NOW, d)
    assert out["candidates"] == 1
    c = sink[0]
    assert c["kind"] == "forecast"
    assert c["gap"] == 6.0            # forecast low 66, bracket floor 72
    assert c["price"] == 0.35


def test_a_bracket_near_the_forecast_is_not_flagged():
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B66.5", 66, 67)])
    assert screen.screen_pass(_NOW, d)["candidates"] == 0
    assert sink == []


def test_realized_observations_flag_a_dead_bracket():
    sink = []
    # Two readings of 18.9C = 66.0F establish the realized low.
    obs = [{"properties": {"timestamp": "2026-08-03T09:00:00+00:00",
                           "temperature": {"value": 18.9}}},
           {"properties": {"timestamp": "2026-08-03T10:00:00+00:00",
                           "temperature": {"value": 18.9}}}]
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)], obs=obs)
    screen.screen_pass(_NOW, d)
    kinds = {c["kind"] for c in sink}
    assert "dead" in kinds


def test_an_unmapped_city_is_skipped():
    sink = []
    d = screen.Deps(
        list_series=lambda: [{"ticker": "KXHIGHNOWHERE", "title": "?"}],
        list_markets=lambda s, status=None: [_market("X-26AUG03-B1.5", 1, 2)],
        resolve_point=lambda lat, lon: {},
        fetch_forecast=lambda url: [],
        fetch_obs=lambda station, start, end: [],
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        station_for=lambda url: None,
        sleep=lambda s: None,
    )
    out = screen.screen_pass(_NOW, d)
    assert out["candidates"] == 0
    assert out["cities"] == 0


def test_one_failing_city_does_not_kill_the_pass():
    sink = []

    def boom(url):
        raise RuntimeError("nws down")

    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    d.fetch_forecast = boom
    out = screen.screen_pass(_NOW, d)
    assert out["errors"] == 1
    assert out["candidates"] == 0


def test_main_returns_nonzero_for_an_unknown_command():
    assert screen.main(["nope"], deps=_deps([], []), now=_NOW) == 2
