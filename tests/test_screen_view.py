import screen_view


def _c(ts, ticker, price, gap, kind="forecast"):
    return {"ts": ts, "series": "KXLOWTDEN", "variable": "low",
            "ticker": ticker, "floor": 72, "cap": 73, "price": price,
            "forecast": 66.0, "gap": gap, "kind": kind, "hours_to_close": 11.0}


def test_latest_firing_keeps_only_the_newest_timestamp():
    rows = [_c("2026-08-03T12:00:00Z", "a", 0.2, 5.0),
            _c("2026-08-03T18:00:00Z", "b", 0.3, 6.0),
            _c("2026-08-03T18:00:00Z", "c", 0.4, 7.0)]
    got = screen_view.latest_firing(rows)
    assert {r["ticker"] for r in got} == {"b", "c"}


def test_latest_firing_of_nothing_is_empty():
    assert screen_view.latest_firing([]) == []


def test_display_rows_rank_by_price_times_gap():
    rows = [_c("t", "small", 0.15, 4.0),      # 0.60
            _c("t", "big", 0.40, 8.0),        # 3.20
            _c("t", "mid", 0.30, 5.0)]        # 1.50
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["big", "mid", "small"]


def test_display_rows_tolerate_a_missing_gap():
    rows = [_c("t", "ok", 0.40, 8.0),
            dict(_c("t", "bad", 0.30, 5.0), gap=None)]
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["ok", "bad"]


def test_display_uses_the_city_name_not_the_series_ticker():
    row = _c("t", "x", 0.4, 5.0)
    row["series"] = "KXHIGHPHIL"
    assert screen_view.city_of(row) == "Philadelphia"


def test_display_falls_back_to_the_series_when_unmapped():
    row = _c("t", "x", 0.4, 5.0)
    row["series"] = "KXHIGHNOWHERE"
    assert screen_view.city_of(row) == "KXHIGHNOWHERE"


def _row_hrs(hours, variable="low", kind="forecast"):
    r = _c("t", "x", 0.3, 5.0, kind)
    r["hours_to_close"] = hours
    r["variable"] = variable
    return r


def test_side_is_always_no_because_the_screen_only_finds_overpriced_brackets():
    assert screen_view.side_of(_row_hrs(10.0)) == "NO"
    assert screen_view.side_of(_row_hrs(10.0, kind="dead")) == "NO"


def test_a_future_climate_day_is_not_settled():
    # >24h to close means the day has not begun; nothing is determined.
    assert screen_view.settled_of(_row_hrs(27.9, "high")) == "No"
    assert screen_view.settled_of(_row_hrs(28.9, "low")) == "No"


def test_a_low_is_settled_once_the_dawn_window_has_passed():
    # Day runs 24h to close -> 0. A low forms early, so by 6h left it is in.
    assert screen_view.settled_of(_row_hrs(5.9, "low")) == "Yes"
    assert screen_view.settled_of(_row_hrs(20.0, "low")) == "No"


def test_a_high_needs_the_afternoon_before_it_is_settled():
    # The high peaks late, so it stays unsettled far longer than the low.
    assert screen_view.settled_of(_row_hrs(12.0, "high")) == "No"
    assert screen_view.settled_of(_row_hrs(5.0, "high")) == "Yes"


def test_a_dead_row_is_settled_regardless_of_the_clock():
    # 'dead' means realized temperature already ruled it out -- hard evidence
    # beats the diurnal heuristic.
    assert screen_view.settled_of(_row_hrs(23.0, "low", kind="dead")) == "Yes"
