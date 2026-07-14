"""Unit tests for kelly.py — the bet-sizing math. All pure; no network."""
import math

import kelly


def test_fee_matches_kalshi_formula():
    # Kalshi: fee = ceil_to_cent(0.07 * n * p * (1-p)).
    # 100 @ $0.50 -> 0.07*100*0.25 = 1.75 exactly.
    assert kelly.fee(100, 0.50) == 1.75


def test_fee_rounds_up_to_the_cent():
    # 1 @ $0.50 -> 0.07*0.25 = 0.0175 -> rounds UP to $0.02.
    assert kelly.fee(1, 0.50) == 0.02


def test_fee_zero_contracts_is_zero():
    assert kelly.fee(0, 0.50) == 0.0


LADDER = [(0.55, 40), (0.58, 120), (0.63, 300)]  # ascending asks


def test_cost_walks_the_book_gross():
    # Buy 100: 40@55 + 60@58 = 22.00 + 34.80 = 56.80 -> avg 56.8c.
    cost = kelly.cost_to_buy(LADDER, 100, include_fees=False)
    assert round(cost, 4) == 56.80
    assert round(cost / 100, 3) == 0.568


def test_cost_partial_first_level():
    assert kelly.cost_to_buy(LADDER, 40, include_fees=False) == 40 * 0.55


def test_cost_includes_per_level_fees():
    # 40 @ 55c: fee = ceil(0.07*40*0.55*0.45) = ceil(0.693) -> $0.70.
    gross = 40 * 0.55
    assert kelly.cost_to_buy(LADDER, 40, include_fees=True) == gross + 0.70


def test_cost_none_when_deeper_than_book():
    assert kelly.cost_to_buy(LADDER, 461, include_fees=False) is None
    assert kelly.cost_to_buy(LADDER, 460, include_fees=False) is not None


def test_kelly_fraction_positive_edge():
    # q=0.60, price=0.50 -> (0.60-0.50)/(1-0.50) = 0.20.
    assert round(kelly.kelly_fraction(0.60, 0.50), 4) == 0.20


def test_kelly_fraction_no_edge_is_zero():
    assert kelly.kelly_fraction(0.50, 0.50) == 0.0
    assert kelly.kelly_fraction(0.40, 0.50) == 0.0


def test_best_side_picks_yes_when_underpriced():
    # p=0.65, yes_ask=0.55 -> edge_yes +0.10; no_ask=0.50 -> edge_no 0.35-0.50<0.
    assert kelly.best_side(0.65, 0.55, 0.50) == ("yes", 0.65, 0.55)


def test_best_side_picks_no_when_yes_overpriced():
    # p=0.30 -> no win-prob 0.70; no_ask=0.55 -> edge_no +0.15 beats yes.
    assert kelly.best_side(0.30, 0.80, 0.55) == ("no", 0.70, 0.55)


def test_best_side_none_when_no_edge():
    assert kelly.best_side(0.50, 0.55, 0.55) is None


def test_best_side_ignores_missing_ask():
    # yes_ask missing -> only NO considered; NO win-prob 0.35 vs 0.50 ask is
    # negative edge, so nothing clears -> None.
    assert kelly.best_side(0.65, None, 0.50) is None
    # yes_ask present with edge, no_ask missing -> picks YES.
    assert kelly.best_side(0.65, 0.55, None) == ("yes", 0.65, 0.55)
