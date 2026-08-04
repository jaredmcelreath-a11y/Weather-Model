import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, ask=0.35, bid=0.33, strike="between"):
    return {"series": "KXLOWTDEN", "variable": "low",
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": strike,
            "floor": floor, "cap": cap, "yes_bid": bid, "yes_ask": ask,
            "hours_to_close": 11.0}


def test_gap_is_zero_when_the_bracket_contains_the_forecast():
    assert sr.bracket_gap(65, 66, 65.4) == 0.0


def test_gap_measures_to_the_nearest_edge():
    assert sr.bracket_gap(72, 73, 66.0) == 6.0
    assert sr.bracket_gap(60, 61, 66.0) == 5.0


def test_gap_handles_open_ended_tails():
    # 'greater than 107' with a forecast of 96 is 11 degrees away.
    assert sr.bracket_gap(107, None, 96.0) == 11.0
    # 'less than 97' with a forecast of 96 contains it.
    assert sr.bracket_gap(None, 97, 96.0) == 0.0


def test_gap_is_none_without_any_strike():
    assert sr.bracket_gap(None, None, 96.0) is None


def test_price_prefers_the_ask_because_that_is_what_you_pay():
    assert sr.price_of(_row(72, 73, ask=0.37, bid=0.33)) == 0.37
    assert sr.price_of(_row(72, 73, ask=None, bid=0.33)) == 0.33
    assert sr.price_of(_row(72, 73, ask=None, bid=None)) is None


def test_a_far_and_richly_priced_bracket_is_a_candidate():
    got = sr.forecast_candidate(_row(72, 73, ask=0.35), 66.0, _TS)
    assert got["kind"] == "forecast"
    assert got["gap"] == 6.0
    assert got["price"] == 0.35
    assert got["forecast"] == 66.0
    assert got["ticker"] == "KXLOWTDEN-26AUG03-B72.5"


def test_a_cheap_bracket_is_not_a_candidate():
    assert sr.forecast_candidate(_row(72, 73, ask=0.05), 66.0, _TS) is None


def test_a_near_bracket_is_not_a_candidate():
    assert sr.forecast_candidate(_row(68, 69, ask=0.35), 66.0, _TS) is None


def test_the_thresholds_are_inclusive_at_the_boundary():
    at_price = sr.forecast_candidate(_row(72, 73, ask=sr.MIN_CANDIDATE_PRICE),
                                     66.0, _TS)
    assert at_price is not None
    at_gap = sr.forecast_candidate(_row(70, 71, ask=0.35), 66.0, _TS)
    assert at_gap is not None and at_gap["gap"] == sr.MIN_CANDIDATE_GAP_F


def test_no_candidate_without_a_forecast():
    assert sr.forecast_candidate(_row(72, 73), None, _TS) is None
