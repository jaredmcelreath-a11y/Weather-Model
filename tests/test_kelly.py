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
