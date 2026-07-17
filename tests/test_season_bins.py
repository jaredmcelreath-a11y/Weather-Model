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


import sys

try:
    import streamlit  # noqa: F401
except ModuleNotFoundError:
    from unittest.mock import MagicMock
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import market_view


class _AbstainAdapter:
    """model_prob returns each contract's stashed p — None means unpriceable."""
    def model_prob(self, probs, c):
        return c["p"]


def test_unpriceable_contract_is_not_a_kelly_pick():
    # The phantom-edge scenario: a near-certain YES bucket the model can't
    # price, priced cheap on the NO side. It must NOT become a pick.
    contracts = [{"label": "54-55", "p": None, "yes_ask": 0.85, "no_ask": 0.15}]
    assert market_view._kelly_pick(contracts, {}, _AbstainAdapter()) is None


def test_priceable_contract_still_picked_alongside_unpriceable():
    contracts = [
        {"label": "54-55", "p": None, "yes_ask": 0.85, "no_ask": 0.15},
        {"label": "90-91", "p": 0.70, "yes_ask": 0.55, "no_ask": 0.42},
    ]
    pick = market_view._kelly_pick(contracts, {}, _AbstainAdapter())
    assert pick is not None
    assert pick[0]["label"] == "90-91"


import config


def test_bin_range_brackets_dfw_climate():
    # DFW all-time records are about -8 and 113; the sample low is -2 (Feb 2021)
    # and the sample high is 110. The range must clear both with margin.
    assert config.BIN_LOW == -10
    assert config.BIN_HIGH == 115


def test_bin_labels_span_the_new_range():
    labels = config.bin_labels()
    assert labels[0] == "<= -10"
    assert labels[-1] == ">= 115"
    assert len(labels) == 126


def test_september_front_low_is_now_priceable():
    # THE regression this whole change exists for. Under the old range this
    # distribution was '<= 60': ~1.0 and P(low <= 55) came back a confident 0.
    from settlement import bin_for_temp
    assert bin_for_temp(55) == "55"          # 55 is its own bin now, not a tail
    probs = {lbl: 0.0 for lbl in config.bin_labels()}
    probs["55"] = 0.6
    probs["56"] = 0.4
    p = model.prob_at_most(probs, 55)
    assert p is not None
    assert abs(p - 0.6) < 1e-9


def test_hot_tail_contract_is_now_priceable():
    # 3 of 4018 days hit >= 110; 111 used to be unpriceable.
    from settlement import bin_for_temp
    assert bin_for_temp(111) == "111"


import edge_report


def test_is_boundary_edges_follow_the_config_range():
    # A September front low near a Kalshi even|odd edge must register as a
    # boundary case; the old hardcoded range(60, 120, 2) missed everything <60.
    assert edge_report.is_boundary(58.5) is True     # on the 58|59 edge
    assert edge_report.is_boundary(58.0) is True     # 0.5 away
    assert edge_report.is_boundary(57.4) is False    # 0.9 from 56.5 and 58.5


def test_is_boundary_unchanged_in_the_old_range():
    # tests/test_edge_report.py:34 assertions must keep holding.
    assert edge_report.is_boundary(96.5) is True
    assert edge_report.is_boundary(97.0) is True
    assert edge_report.is_boundary(95.4) is False
    assert edge_report.is_boundary(97.6) is False


def test_within1_matches_index_distance_on_legacy_tail_labels():
    # bin_temp distance == LABELS.index distance, but can't ValueError on a
    # legacy label absent from the widened LABELS.
    assert abs(model.bin_temp("<= 60") - model.bin_temp("61")) == 1
    assert abs(model.bin_temp("108") - model.bin_temp(">= 110")) == 2


def test_summer_day_probabilities_are_effectively_unchanged():
    # A typical summer high near 97: the old 60..110 tails held ~0 mass, so
    # widening must not move the distribution.
    samples = [95.0 + i * 0.3 for i in range(40)]
    weights = [1.0] * len(samples)
    probs = model._bin_probabilities(samples, 2.0, weights)

    assert abs(sum(probs.values()) - 1.0) < 1e-9        # still normalized
    assert probs["<= -10"] < 1e-12                      # tails hold nothing
    assert probs[">= 115"] < 1e-12
    # Mass sits where it did before, in the explicit bins.
    assert sum(v for k, v in probs.items()
               if k not in ("<= -10", ">= 115")) > 0.999


def test_prob_table_thresholds_never_abstain():
    # prob_table feeds bin_temp of the dict's OWN labels back into the
    # cumulative helpers; those land ON tail edges, never inside them.
    samples = [95.0 + i * 0.3 for i in range(40)]
    probs = model._bin_probabilities(samples, 2.0, [1.0] * len(samples))
    for label in probs:
        t = model.bin_temp(label)
        assert model.prob_at_least(probs, t) is not None
        assert model.prob_at_most(probs, t) is not None
