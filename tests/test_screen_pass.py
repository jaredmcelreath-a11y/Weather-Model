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


# ---- Storm context on the candidate row ------------------------------------

_STORMY_PERIODS = [
    {"startTime": "2026-08-03T02:00:00-06:00", "temperature": 66,
     "probabilityOfPrecipitation": {"value": 20},
     "shortForecast": "Chance Rain Showers"},
    {"startTime": "2026-08-03T15:00:00-06:00", "temperature": 94,
     "probabilityOfPrecipitation": {"value": 70},
     "shortForecast": "Chance Showers And Thunderstorms"},
]


def test_a_candidate_carries_the_storm_chance():
    # Denver low, _NOW is 12:00 LST: the 15:00 storm hour is still ahead and a
    # low's window runs to midnight, so it counts.
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    d.fetch_forecast = lambda url: _STORMY_PERIODS
    screen.screen_pass(_NOW, d)
    assert sink[0]["storm"] == 70


def test_a_dead_candidate_carries_it_too():
    # The hard screen's rows need the same caution as the soft screen's.
    obs = [{"properties": {"timestamp": "2026-08-03T08:00:00-06:00",
                           "temperature": {"value": 18.9}}},
           {"properties": {"timestamp": "2026-08-03T09:00:00-06:00",
                           "temperature": {"value": 18.9}}}]
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)], obs=obs)
    d.fetch_forecast = lambda url: _STORMY_PERIODS
    screen.screen_pass(_NOW, d)
    assert [c["kind"] for c in sink] == ["forecast", "dead"]
    assert all(c["storm"] == 70 for c in sink)


def test_a_storm_free_day_records_zero_not_a_missing_field():
    sink = []
    d = _deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)])
    screen.screen_pass(_NOW, d)          # _PERIODS carry no shortForecast
    assert sink[0]["storm"] == 0


# 11:00 / 12:00 / 15:00 MDT, i.e. 17:00Z / 18:00Z / 21:00Z. _NOW is 18:00Z.
_DRIFT_PERIODS = [
    {"startTime": "2026-08-03T11:00:00-06:00", "temperature": 90},
    {"startTime": "2026-08-03T12:00:00-06:00", "temperature": 92},
    {"startTime": "2026-08-03T15:00:00-06:00", "temperature": 94},
]

# Two readings of 30.0C = 86.0F at 10 and 5 minutes before _NOW.
_DRIFT_OBS = [
    {"properties": {"timestamp": "2026-08-03T17:50:00+00:00",
                    "temperature": {"value": 30.0}}},
    {"properties": {"timestamp": "2026-08-03T17:55:00+00:00",
                    "temperature": {"value": 30.0}}},
]


def _drift_deps(sink, markets, obs):
    d = _deps(sink, markets, obs=obs)
    d.fetch_forecast = lambda url: _DRIFT_PERIODS
    return d


def test_a_forecast_candidate_carries_the_drift_and_the_implied_reference():
    sink = []
    d = _drift_deps(sink, [_market("KXLOWTDEN-26AUG03-B72.5", 72, 73)],
                    _DRIFT_OBS)
    screen.screen_pass(_NOW, d)
    c = [r for r in sink if r["kind"] == "forecast"][0]
    # Anchor 86.0F at 17:52:30Z; the forecast interpolated there is 91.75.
    assert c["drift"] == -5.75
    # Re-folded, NOT Ref + drift: fold(90 - 5.75, [86, 86], "low") = 84.25.
    assert c["drift_ref"] == 84.25
    assert c["forecast"] == 86.0          # Ref itself is untouched


def test_a_dead_candidate_carries_no_drift():
    # A dead row's Ref is the realized bound, a fact -- there is no forecast
    # drifting, and an arrow off a different number would just confuse.
    #
    # Denver's HIGH series is KXHIGHDEN, with no 'T' -- Kalshi's naming is
    # inconsistent and KXHIGHTDEN is not in scan_cities._SERIES_CITY. Using it
    # makes screen_pass skip the city as unmapped and the test passes vacuously.
    sink = []
    d = _drift_deps(sink, [_market("KXHIGHDEN-26AUG03-T72.5", 72, 73)],
                    _DRIFT_OBS)
    d.list_series = lambda: [{"ticker": "KXHIGHDEN", "title": "Denver high"}]
    screen.screen_pass(_NOW, d)
    # Realized max 86.0F could settle at 85-87, so a 72-73 high bracket is dead.
    dead = [r for r in sink if r["kind"] == "dead"]
    assert dead, "fixture must actually produce a dead row"
    for r in dead:
        assert r["drift"] is None and r["drift_ref"] is None


def test_a_day_not_yet_started_has_no_drift():
    sink = []
    # Aug 4 markets on an Aug 3 pass: no observations exist to compare against.
    d = _drift_deps(sink, [_market("KXLOWTDEN-26AUG04-B72.5", 72, 73)], [])
    d.fetch_forecast = lambda url: [
        {"startTime": "2026-08-04T02:00:00-06:00", "temperature": 66}]
    screen.screen_pass(_NOW, d)
    assert sink, "fixture must actually produce a row"
    for r in sink:
        assert r["drift"] is None and r["drift_ref"] is None
