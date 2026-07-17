"""Season readiness: self-describing bin labels + the tail abstain guard.

See docs/superpowers/specs/2026-07-17-season-readiness-design.md
"""
import model


def test_bin_temp_parses_legacy_tail_labels():
    # A row logged under the old 60..110 range must keep its original meaning
    # even after the range widens — bin_temp reads the label, not the config.
    assert model.bin_temp("<= 60") == 60
    assert model.bin_temp(">= 110") == 110


def test_bin_temp_parses_new_tail_labels():
    assert model.bin_temp("<= -10") == -10
    assert model.bin_temp(">= 115") == 115


def test_bin_temp_parses_interior_label():
    assert model.bin_temp("90") == 90


def test_bin_temp_ignores_config_range():
    # The whole point: changing the config must not change what a label means.
    original = (model.BIN_LOW, model.BIN_HIGH)
    try:
        model.BIN_LOW, model.BIN_HIGH = -99, 999
        assert model.bin_temp("<= 60") == 60
        assert model.bin_temp(">= 110") == 110
    finally:
        model.BIN_LOW, model.BIN_HIGH = original


# A September cold-front low near 55, as the OLD 60..110 range would log it.
_LEGACY_FRONT = {"<= 60": 0.97, "61": 0.02, "62": 0.01}
# A closed dict with no tails at all (as several existing tests build).
_CLOSED = {"90": 0.5, "91": 0.5}


def test_abstains_when_query_cuts_inside_low_tail():
    # The bug: this returned 0 — a confident "impossible" for a near-certain low.
    assert model.prob_at_most(_LEGACY_FRONT, 59) is None
    assert model.prob_at_most(_LEGACY_FRONT, 55) is None


def test_abstains_when_query_cuts_inside_high_tail():
    probs = {"108": 0.01, "109": 0.02, ">= 110": 0.97}
    assert model.prob_at_least(probs, 111) is None


def test_threshold_on_the_tail_edge_is_answerable():
    # "<= 60" IS exactly the mass at or below 60 — no resolution needed inside
    # it, so this is answerable: the tail's own 0.97, not the whole dict.
    assert abs(model.prob_at_most(_LEGACY_FRONT, 60) - 0.97) < 1e-9


def test_query_past_the_far_tail_is_answerable():
    # Everything is >= 60 when 60 is the low edge; no tail-splitting required.
    assert model.prob_at_least(_LEGACY_FRONT, 60) == 1.0
    assert model.prob_at_least(_LEGACY_FRONT, 55) == 1.0


def test_closed_dict_without_tails_never_abstains():
    # No open tail => mass outside the set is genuinely zero, not unknown.
    assert model.prob_at_most(_CLOSED, 50) == 0.0
    assert model.prob_at_least(_CLOSED, 200) == 0.0


def test_abstain_propagates_through_contract_helpers():
    assert model.prob_less_than(_LEGACY_FRONT, 60) is None      # -> at_most(59)
    assert model.prob_for_contract(_LEGACY_FRONT, "<", 60) is None


def test_abstain_propagates_through_kalshi_strikes():
    # "59 or below" and "between 54-55" both need sub-tail resolution.
    assert model.prob_for_strike(_LEGACY_FRONT, "less", None, 60) is None
    assert model.prob_for_strike(_LEGACY_FRONT, "between", 54, 55) is None


def test_answerable_strike_still_prices():
    p = model.prob_for_strike(_LEGACY_FRONT, "between", 61, 62)
    assert abs(p - 0.03) < 1e-9


import kelly


def test_best_side_abstains_when_model_cannot_price():
    # Without the guard this raises TypeError on `p - yes_ask`.
    assert kelly.best_side(None, 0.40, 0.55) is None


def test_best_side_still_picks_the_edge_when_priced():
    side, win, ask = kelly.best_side(0.70, 0.55, 0.42)
    assert side == "yes"
    assert win == 0.70
    assert ask == 0.55


import backtest


def test_contract_points_skips_unpriceable_strikes():
    # A legacy-range row swept against the new wider strike range hits strikes
    # the model can't price; those must be skipped, not crash.
    pts = backtest.contract_points(_LEGACY_FRONT, 55.0, "low")
    assert isinstance(pts, list)
    assert all(p is not None and 0.01 <= p <= 0.99 for p, _won in pts)
