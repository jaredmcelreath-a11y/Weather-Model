"""guarded_candidate: realized already inside, and the forecast protects it."""
import screen_rules

_NOW = "2026-08-09T20:00:00Z"


def _row(variable, strike_type, floor=None, cap=None, yes_ask=0.39):
    return {"series": "KXLOWTPHX", "variable": variable, "ticker": "KX-26AUG09-X",
            "strike_type": strike_type, "floor": floor, "cap": cap,
            "label": "92° or above", "yes_ask": yes_ask, "yes_bid": 0.36,
            "volume": 9381, "hours_to_close": 7.0}


def test_the_phoenix_case_fires():
    # THE regression case, live 2026-08-09 17:18 LST. Realized low 93.2 (raw min
    # 93.0), bracket "92 or above", remaining forecast bottoming at 96 at
    # midnight, quoted 39c while the market's implied low was 91.
    row = _row("low", "greater", floor=91)          # winning range (92, None)
    got = screen_rules.guarded_candidate(row, 93.2, 96.0, _NOW)
    assert got is not None
    assert got["kind"] == "guarded" and got["side"] == "YES"
    assert got["margin"] == 4.0                     # 96 - 92, just clearing the bar


def test_a_thinner_margin_than_the_bar_does_not_fire():
    row = _row("low", "greater", floor=91)
    assert screen_rules.guarded_candidate(row, 93.2, 95.9, _NOW) is None


def test_a_low_is_only_ever_threatened_from_below():
    # A low cannot rise out of the top of a bracket, so a bounded bracket is
    # judged on its lo alone -- here 90, against a remaining forecast of 96.
    row = _row("low", "between", floor=90, cap=94)
    got = screen_rules.guarded_candidate(row, 92.0, 96.0, _NOW)
    assert got["margin"] == 6.0


def test_a_high_is_only_ever_threatened_from_above():
    row = _row("high", "between", floor=88, cap=95)
    got = screen_rules.guarded_candidate(row, 90.0, 91.0, _NOW)
    assert got["margin"] == 4.0                     # 95 - 91


def test_an_unbounded_tail_is_locked_work_not_guarded_work():
    # The two rules partition: nothing threatens this side, so guarded declines
    # and locked_candidate owns it.
    row = _row("low", "less", cap=84)               # winning range (None, 83)
    assert screen_rules.guarded_candidate(row, 80.0, 96.0, _NOW) is None


def test_a_realized_extreme_outside_the_bracket_does_not_fire():
    # This is a forecast bet, not a bracket the day has already won.
    row = _row("low", "greater", floor=91)          # winning range (92, None)
    assert screen_rules.guarded_candidate(row, 89.0, 96.0, _NOW) is None


def test_inside_is_judged_on_the_settled_basis():
    # 90.5F could be reported as 90, which loses a 91-and-above bracket, so the
    # realized extreme does not yet "already sit inside" it.
    row = _row("high", "greater", floor=90)         # winning range (91, None)
    assert screen_rules.settled_range(90.5) == (90, 91)
    assert screen_rules.guarded_candidate(row, 90.5, 120.0, _NOW) is None


def test_no_remaining_forecast_means_no_guard():
    row = _row("low", "greater", floor=91)
    assert screen_rules.guarded_candidate(row, 93.2, None, _NOW) is None


def test_the_yes_band_applies_here_too():
    assert screen_rules.guarded_candidate(
        _row("low", "greater", floor=91, yes_ask=0.95), 93.2, 96.0, _NOW) is None
    assert screen_rules.guarded_candidate(
        _row("low", "greater", floor=91, yes_ask=0.04), 93.2, 96.0, _NOW) is None
