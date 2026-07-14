"""Kelly bet-sizing on Kalshi temperature buckets, accounting for order-book
slippage. A single bucket is one binary bet (all contracts share the outcome),
so this is Kelly on a binary bet with a lumpy, size-dependent cost curve. Pure
functions — no network, no Streamlit. The Kalshi book convention lives in
sources/kalshi.py; here a ladder is just an ascending list of (price, size).
"""
from __future__ import annotations

import math


def fee(n: int, price: float) -> float:
    """Kalshi trading fee in dollars for `n` contracts filled at `price`
    (dollars 0-1): ceil to the next cent of 0.07 * n * price * (1 - price)."""
    if n <= 0:
        return 0.0
    raw = 0.07 * n * price * (1.0 - price)
    return math.ceil(raw * 100 - 1e-9) / 100.0
