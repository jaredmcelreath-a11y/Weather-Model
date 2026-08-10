"""locked_candidate: brackets the realized extreme has made impossible to LOSE."""
import screen_rules

_NOW = "2026-08-09T20:00:00Z"


def _row(variable, strike_type, floor=None, cap=None, yes_ask=0.40):
    return {"series": "KXLOWTPHX", "variable": variable, "ticker": "KX-26AUG09-X",
            "strike_type": strike_type, "floor": floor, "cap": cap,
            "label": "test", "yes_ask": yes_ask, "yes_bid": 0.36,
            "volume": 100, "hours_to_close": 7.0}


def test_a_low_tail_open_downward_is_locked_once_the_low_reaches_it():
    # "83 or below" (less, cap 84 -> winning range (None, 83)). A low can only
    # fall, so once it touches 83 the bracket can never be taken away.
    row = _row("low", "less", cap=84)
    got = screen_rules.locked_candidate(row, 80.0, _NOW)
    assert got is not None
    assert got["kind"] == "locked" and got["side"] == "YES"


def test_a_high_tail_open_upward_is_locked_once_the_high_reaches_it():
    # "91 or above" (greater, floor 90 -> winning range (91, None)).
    row = _row("high", "greater", floor=90)
    got = screen_rules.locked_candidate(row, 95.0, _NOW)
    assert got is not None and got["kind"] == "locked"


def test_the_phoenix_bracket_is_NOT_locked_because_a_low_can_still_fall():
    # "92 or above" for a LOW is unbounded in the direction the extreme CANNOT
    # move. Six hours of climate day remain in which a downdraft could crash it.
    row = _row("low", "greater", floor=91)
    assert screen_rules.locked_candidate(row, 93.2, _NOW) is None


def test_a_bounded_bracket_is_never_locked():
    # "90 to 91" can always be lost -- a low can keep falling out the bottom.
    row = _row("low", "between", floor=90, cap=91)
    assert screen_rules.locked_candidate(row, 90.5, _NOW) is None


def test_a_reading_that_could_round_either_way_cannot_claim_a_lock():
    # "91 or above" (greater, floor 90 -> winning range (91, None)) against a
    # realized high of 90.5, which the climate report can state as 90 or as 91.
    # The raw reading looks like a win; half the values it could settle at lose.
    row = _row("high", "greater", floor=90)
    assert screen_rules.settled_range(90.5) == (90, 91)
    assert screen_rules.locked_candidate(row, 90.5, _NOW) is None


def test_a_whole_celsius_reading_carries_its_full_slack():
    # 93.2F is exactly 34C, so it comes from a station reporting WHOLE degrees
    # Celsius and pins the settled value only to 92-94. Against "93 or above"
    # that is not a lock, however comfortably 93.2 clears 93 on the thermometer.
    # This is the reasoning that stopped a false 'dead' on Atlanta 2026-08-06.
    row = _row("high", "greater", floor=92)    # winning range (93, None)
    assert screen_rules.settled_range(93.2) == (92, 94)
    assert screen_rules.locked_candidate(row, 93.2, _NOW) is None


def test_margin_says_how_deep_the_lock_is():
    row = _row("low", "less", cap=84)          # winning range (None, 83)
    got = screen_rules.locked_candidate(row, 80.0, _NOW)
    assert got["margin"] == 83.0 - screen_rules.settled_range(80.0)[1]


def test_no_realized_bound_means_no_lock():
    assert screen_rules.locked_candidate(_row("low", "less", cap=84), None, _NOW) is None


def test_a_price_the_market_already_agrees_with_is_not_flagged():
    row = _row("low", "less", cap=84, yes_ask=0.95)
    assert screen_rules.locked_candidate(row, 80.0, _NOW) is None


def test_a_suspiciously_cheap_lock_is_not_flagged():
    row = _row("low", "less", cap=84, yes_ask=0.04)
    assert screen_rules.locked_candidate(row, 80.0, _NOW) is None


def test_a_locked_row_never_looks_like_a_fade_row():
    # The two logs are separate, but a YES row must be unmistakable even if they
    # are ever read together: no 'gap', no 'forecast'.
    got = screen_rules.locked_candidate(_row("low", "less", cap=84), 80.0, _NOW)
    assert "gap" not in got and "forecast" not in got
    assert got["reference"] == 80.0
