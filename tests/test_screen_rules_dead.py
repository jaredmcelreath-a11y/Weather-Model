import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, variable="low", ask=0.35, strike="between"):
    return {"series": "KXLOWTDEN", "variable": variable,
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": strike,
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


def test_a_greater_tail_is_dead_at_the_boundary_the_strike_hides():
    # ">66" wins at 67+, so a realized low of 66 already kills it even though
    # its floor_strike equals the bound.
    got = sr.dead_candidate(_row(66, None, "low", strike="greater"), 66.0, _TS)
    assert got is not None and got["gap"] == 1.0


def test_a_less_tail_is_dead_at_the_boundary_the_strike_hides():
    # "<96" wins at 95 or below, so a realized high of 96 kills it.
    got = sr.dead_candidate(_row(None, 96, "high", strike="less"), 96.0, _TS)
    assert got is not None and got["gap"] == 1.0


def test_a_whole_celsius_bound_spans_the_celsius_step():
    # 71.6F is 22C exactly -- all a whole-degC station resolves. The true
    # temperature is anywhere in [21.5, 22.5)C, i.e. 70.7F to just under 72.5F,
    # so the climate report could round it to 71 OR to 72.
    assert sr.settled_range(71.6) == (71, 72)


def test_a_finer_bound_settles_to_a_single_degree():
    # 66.0F is 18.9C -- a reading no whole-degC station could produce, so it
    # carries tenths precision and only one whole-degF value is possible.
    assert sr.settled_range(66.0) == (66, 66)


def test_a_realized_low_that_could_still_settle_inside_the_bracket_is_not_dead():
    # Atlanta 2026-08-06: the realized minimum read 71.6F (22C) and the screen
    # called "72 to 73" dead while Kalshi had it at 91% YES. It settles on the
    # whole-degF climate report, where 71.6 rounds to 72 -- inside the bracket.
    assert sr.dead_candidate(_row(72, 73, "low"), 71.6, _TS) is None


def test_a_realized_high_that_could_still_settle_inside_the_bracket_is_not_dead():
    # Mirror: 89.6F is 32C, so the settled high could still be 89.
    assert sr.dead_candidate(_row(88, 89, "high"), 89.6, _TS) is None


def test_a_bracket_clear_of_the_settlement_band_is_still_dead():
    # The allowance is one degC step, not a blanket -- 74-75 remains impossible
    # against a 71.6F minimum however the report rounds it.
    got = sr.dead_candidate(_row(74, 75, "low"), 71.6, _TS)
    assert got is not None and got["gap"] == 2.4


def test_a_less_tail_can_never_be_dead_for_a_low():
    # "below 76" stays winnable however low the day goes.
    assert sr.dead_candidate(_row(None, 76, "low", strike="less"), 66.0, _TS) is None


def test_a_greater_tail_can_never_be_dead_for_a_high():
    assert sr.dead_candidate(_row(90, None, "high", strike="greater"), 96.0, _TS) is None
