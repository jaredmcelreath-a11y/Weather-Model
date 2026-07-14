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
