import screen_rules as sr

_TS = "2026-08-03T18:00:00Z"


def _row(floor, cap, ask=0.35, bid=0.33, strike="between"):
    return {"series": "KXLOWTDEN", "variable": "low",
            "ticker": "KXLOWTDEN-26AUG03-B72.5", "strike_type": strike,
            "floor": floor, "cap": cap, "yes_bid": bid, "yes_ask": ask,
            "hours_to_close": 11.0}


def test_winning_range_of_a_between_bracket_is_inclusive():
    # Kalshi labels floor=89 cap=90 as "89 to 90".
    assert sr.winning_range(_row(89, 90)) == (89, 90)


def test_a_greater_tail_starts_one_degree_above_its_strike():
    # Kalshi labels floor=90 greater as "91 or above" -- NOT 90.
    assert sr.winning_range(_row(90, None, strike="greater")) == (91, None)


def test_a_less_tail_ends_one_degree_below_its_strike():
    # Kalshi labels cap=83 less as "82 or below" -- NOT 83.
    assert sr.winning_range(_row(None, 83, strike="less")) == (None, 82)


def test_gap_is_zero_when_the_bracket_contains_the_forecast():
    assert sr.bracket_gap(_row(65, 66), 65.4) == 0.0


def test_gap_measures_to_the_nearest_edge():
    assert sr.bracket_gap(_row(72, 73), 66.0) == 6.0
    assert sr.bracket_gap(_row(60, 61), 66.0) == 5.0


def test_gap_uses_the_tails_real_winning_edge_not_its_strike():
    # ">90" wins at 91, so a forecast of 85 is 6 away, not 5.
    assert sr.bracket_gap(_row(90, None, strike="greater"), 85.0) == 6.0
    # "<83" wins at 82, so a forecast of 85 is 3 away, not 2.
    assert sr.bracket_gap(_row(None, 83, strike="less"), 85.0) == 3.0


def test_a_forecast_inside_a_tail_still_has_no_gap():
    assert sr.bracket_gap(_row(90, None, strike="greater"), 95.0) == 0.0
    assert sr.bracket_gap(_row(None, 83, strike="less"), 70.0) == 0.0


def test_gap_is_none_without_any_strike():
    assert sr.bracket_gap(_row(None, None), 96.0) is None


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


def test_an_effectively_settled_bracket_is_never_a_candidate():
    # A bracket bid at/above SETTLED_PRICE is the resolved outcome, not a
    # mispricing. A live pass flagged KXLOWTOKC 65-66 at $1.00 as "16F from the
    # forecast" -- it was simply the low that already happened.
    assert sr.forecast_candidate(_row(72, 73, ask=1.00), 66.0, _TS) is None
    assert sr.forecast_candidate(_row(72, 73, ask=sr.SETTLED_PRICE),
                                 66.0, _TS) is None
    assert sr.dead_candidate(_row(72, 73, ask=0.99), 66.0, _TS) is None


# ---- Fields the outcome scoring needs --------------------------------------

def test_a_candidate_carries_the_bid_and_volume_for_later_scoring():
    # `price` is the YES ASK, so it cannot say what fading actually cost:
    # buying NO sells against the BID. Without the bid, screen_score has to
    # fall back to 1 - ask, which is cheaper than reality and biases the
    # measured edge in the screen's own favour. Volume says whether the fill
    # was ever realistic. Both are already on the snapshot row -- free.
    row = {"ticker": "KXLOWTDEN-26AUG03-B72.5", "series": "KXLOWTDEN",
           "variable": "low", "strike_type": "between", "floor": 72, "cap": 73,
           "yes_bid": 0.31, "yes_ask": 0.35, "volume": 250.0,
           "hours_to_close": 9.0, "label": "72° to 73°"}
    got = sr.forecast_candidate(row, 66.0, _TS)
    assert got["price"] == 0.35            # unchanged: what backing YES costs
    assert got["yes_bid"] == 0.31          # what fading it actually sells into
    assert got["volume"] == 250.0


# ---- Lead-adjusted strength ------------------------------------------------
# The flat 4F bar treats a 30-hour gap like a 3-hour one, but measured error
# growth says otherwise. Only the RATIO of day-ahead to same-day error is used
# (high 1.87/0.70 = 2.7x, low 1.97/1.70 = 1.16x) — error growth with lead is a
# general property of forecasting, while the absolute level varies by city, and
# claiming a per-city sigma is the season-readiness phantom-edge bug.

def test_a_same_day_bracket_is_measured_against_the_plain_bar():
    assert sr.lead_multiplier(6.0, "high") == 1.0
    assert sr.lead_multiplier(12.0, "low") == 1.0


def test_a_day_ahead_high_must_clear_far_more():
    # Highs decay fastest with lead: 2.7x the same-day requirement.
    assert round(sr.lead_multiplier(36.0, "high"), 2) == 2.67


def test_a_day_ahead_low_barely_moves():
    # Same-day lows are already convective-noisy, so the day-ahead penalty is
    # small. The asymmetry is the point — do not average the two.
    assert round(sr.lead_multiplier(36.0, "low"), 2) == 1.16


def test_the_multiplier_interpolates_rather_than_stepping():
    # A row must not jump in strength the moment it crosses a boundary.
    mid = sr.lead_multiplier(24.0, "high")
    assert 1.0 < mid < 2.67
    assert sr.lead_multiplier(18.0, "high") < mid < sr.lead_multiplier(30.0, "high")


def test_the_multiplier_is_clamped_beyond_the_measured_range():
    assert sr.lead_multiplier(200.0, "high") == sr.lead_multiplier(36.0, "high")
    assert sr.lead_multiplier(0.0, "high") == 1.0


def test_strength_is_the_gap_over_the_bar_for_its_lead():
    # 6F at 3 hours out: 1.5x the 4F bar.
    row = {"gap": 6.0, "hours_to_close": 3.0, "variable": "high"}
    assert round(sr.strength(row), 2) == 1.5


def test_the_same_gap_is_much_weaker_a_day_ahead():
    near = sr.strength({"gap": 6.0, "hours_to_close": 3.0, "variable": "high"})
    far = sr.strength({"gap": 6.0, "hours_to_close": 36.0, "variable": "high"})
    assert far < 0.6 < 1.5 <= near        # 6F day-ahead does not clear the bar

def test_strength_needs_a_gap_and_a_lead():
    assert sr.strength({"gap": None, "hours_to_close": 3.0}) is None
    assert sr.strength({"gap": 6.0, "hours_to_close": None}) is None


# ---- Strength is comparable ACROSS variables too ---------------------------
# The flat 4F bar is variable-blind as well as lead-blind: measured same-day
# sigma is 0.70 for a high but 1.70 for a low, so 4F is ~5.7 sigma of evidence
# on a high and only ~2.4 on a low. Without this, a low at 1.0x looked as
# convincing as a high at 1.0x when it was less than half as strong.

def test_a_same_day_low_needs_a_far_bigger_gap_for_the_same_strength():
    # 1.70/0.70 = 2.43x the gap, because the low forecast is that much noisier.
    high_bar = sr.required_gap(3.0, "high")
    low_bar = sr.required_gap(3.0, "low")
    assert round(low_bar / high_bar, 2) == 2.43


def test_the_reference_bar_is_unchanged_for_a_same_day_high():
    # The anchor: a same-day high still reads exactly 1.0x at the 4F threshold,
    # so nothing about the original scale moves.
    assert sr.required_gap(3.0, "high") == sr.MIN_CANDIDATE_GAP_F
    assert sr.strength({"gap": 4.0, "hours_to_close": 3.0,
                        "variable": "high"}) == 1.0


def test_equal_strength_now_means_equal_evidence():
    # The point of the whole column: one number, one meaning. A high and a low
    # reading 1.0x are the same multiple of their own forecast error.
    high = sr.strength({"gap": 4.0, "hours_to_close": 3.0, "variable": "high"})
    low = sr.strength({"gap": 4.0 * 1.70 / 0.70, "hours_to_close": 3.0,
                       "variable": "low"})
    assert round(high, 2) == round(low, 2)


def test_strength_converts_to_sigma_by_one_constant_everywhere():
    # 1.0x == SIGMAS_AT_BAR sigma, for every variable and every lead.
    for variable in ("high", "low"):
        for hours in (3.0, 20.0, 36.0):
            gap = sr.required_gap(hours, variable)
            sigma = gap / (sr.VARIABLE_SIGMA[variable]
                           * sr.lead_multiplier(hours, variable))
            assert round(sigma, 4) == round(sr.SIGMAS_AT_BAR, 4)


def test_an_unknown_variable_falls_back_to_the_plain_bar():
    assert sr.required_gap(3.0, None) == sr.MIN_CANDIDATE_GAP_F
