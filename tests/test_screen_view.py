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
