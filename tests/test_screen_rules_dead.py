import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, variable="low", ask=0.35):
    return {"series": "KXLOWTDEN", "variable": variable,
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": "between",
            "floor": floor, "cap": cap, "yes_bid": 0.33, "yes_ask": ask,
            "hours_to_close": 11.0}


def test_celsius_converts_to_fahrenheit():
    assert sr.c_to_f(0) == 32.0
    assert sr.c_to_f(32.8) == 91.0
    assert sr.c_to_f(None) is None


def test_realized_low_is_the_minimum_with_support():
    # Two readings at or below 66 corroborate 66 as the realized minimum.
    assert sr.realized_extreme([70.0, 66.0, 66.0, 72.0], "low") == 66.0


def test_realized_high_is_the_maximum_with_support():
    assert sr.realized_extreme([70.0, 96.0, 96.0, 72.0], "high") == 96.0


def test_a_lone_outlier_does_not_establish_an_extreme():
    # One spurious 40 must NOT make 40 the realized low -- it would wrongly
    # declare every bracket above it dead.
    assert sr.realized_extreme([70.0, 40.0, 71.0, 72.0], "low") == 70.0


def test_too_few_observations_establish_nothing():
    assert sr.realized_extreme([66.0], "low") is None
    assert sr.realized_extreme([], "low") is None


def test_a_low_bracket_above_the_realized_minimum_is_dead():
    # The low already touched 66, so 72-73 cannot settle YES at any price.
    got = sr.dead_candidate(_row(72, 73, "low"), 66.0, _TS)
    assert got["kind"] == "dead"
    assert got["gap"] == 6.0
    assert got["forecast"] == 66.0


def test_a_low_bracket_below_the_realized_minimum_is_still_live():
    assert sr.dead_candidate(_row(60, 61, "low"), 66.0, _TS) is None


def test_a_high_bracket_below_the_realized_maximum_is_dead():
    got = sr.dead_candidate(_row(90, 91, "high"), 96.0, _TS)
    assert got["kind"] == "dead" and got["gap"] == 5.0


def test_a_high_bracket_above_the_realized_maximum_is_still_live():
    assert sr.dead_candidate(_row(99, 100, "high"), 96.0, _TS) is None


def test_a_bracket_containing_the_bound_is_not_dead():
    assert sr.dead_candidate(_row(66, 67, "low"), 66.0, _TS) is None


def test_a_worthless_dead_bracket_is_not_reported():
    # Nothing to harvest below the price floor.
    assert sr.dead_candidate(_row(72, 73, "low", ask=0.01), 66.0, _TS) is None
