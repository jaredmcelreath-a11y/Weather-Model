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


def cost_to_buy(ladder, n, include_fees=True):
    """Total dollars to buy `n` contracts walking the ascending ask `ladder`
    (levels of (price, size)); fees applied per level on the block taken from
    that level. None if `n` exceeds total book depth."""
    if n <= 0:
        return 0.0
    remaining = n
    total = 0.0
    for price, size in ladder:
        take = min(remaining, size)
        total += take * price
        if include_fees:
            total += fee(take, price)
        remaining -= take
        if remaining == 0:
            return total
    return None  # book too thin to fill n


def kelly_fraction(q, price):
    """Classic Kelly fraction of bankroll to risk on a binary contract bought
    at fixed `price` with win-probability `q`. Clamped at 0 (no bet) when the
    edge is non-positive. Reference point for the book-walk optimizer."""
    if price >= 1.0 or price <= 0.0:
        return 0.0
    f = (q - price) / (1.0 - price)
    return max(0.0, f)
