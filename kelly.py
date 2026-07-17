"""Kelly bet-sizing on Kalshi temperature buckets, accounting for order-book
slippage. A single bucket is one binary bet (all contracts share the outcome),
so this is Kelly on a binary bet with a lumpy, size-dependent cost curve. Pure
functions — no network, no Streamlit. The Kalshi book convention lives in
sources/kalshi.py; here a ladder is just an ascending list of (price, size).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Sizing:
    side: str = ""
    contracts: int = 0
    avg_price: float | None = None
    stake: float = 0.0
    ev: float = 0.0
    full_kelly: int = 0
    ev_ceiling: int = 0
    curve: list = field(default_factory=list)   # (n, ev_dollars)
    note: str = ""


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


def best_side(p, yes_ask, no_ask):
    """The side to buy: whichever of YES (win-prob p) / NO (win-prob 1-p) has
    the larger positive edge vs its ask. None if neither side has an edge or
    its ask is missing. Mirrors the market table's >0 edge signal.

    `p` is None when the model can't price the contract (it falls inside an
    open-ended bin tail) — an unpriceable contract isn't sizable."""
    if p is None:
        return None
    cands = []
    if yes_ask is not None:
        cands.append(("yes", p, yes_ask, p - yes_ask))
    if no_ask is not None:
        cands.append(("no", 1.0 - p, no_ask, (1.0 - p) - no_ask))
    cands = [c for c in cands if c[3] > 0]
    if not cands:
        return None
    side, win, ask, _edge = max(cands, key=lambda c: c[3])
    return (side, win, ask)


def optimal_size(ladder, q, bankroll, kelly_frac, side=""):
    """Recommended contract count on an ascending ask `ladder` for a binary
    contract with model win-prob `q`, sizing against `bankroll` at Kelly
    fraction `kelly_frac`. Walks the book contract-by-contract (fees rounded
    per level), stops at the negative-EV ceiling, maximizes expected
    log-growth for the full-Kelly point, then scales the stake by kelly_frac.
    """
    s = Sizing(side=side)
    if not ladder:
        s.note = "No live order book for this contract."
        return s

    # Cumulative cost incl. per-level-rounded fees, contract by contract.
    cost = [0.0]           # cost[n] = dollars to buy n contracts
    ev = [0.0]             # ev[n]   = q*n - cost[n]
    prev_levels_cost = 0.0
    hit_ceiling = False
    ceiling = 0
    for price, size in ladder:
        for k in range(1, int(size) + 1):
            block = k * price + fee(k, price)
            n = len(cost)                 # this contract's index
            c_n = prev_levels_cost + block
            marginal = c_n - cost[-1]
            if q - marginal <= 0:         # marginal contract is -EV: stop here
                hit_ceiling = True
                break
            if c_n >= bankroll:           # can't afford the next contract
                hit_ceiling = True
                break
            cost.append(c_n)
            ev.append(q * n - c_n)
            ceiling = n
            s.curve.append((n, round(ev[-1], 2)))
        if hit_ceiling:
            break
        prev_levels_cost += size * price + fee(int(size), price)

    s.ev_ceiling = ceiling
    if ceiling == 0:
        s.note = ("No bet — the best ask already meets or exceeds the model's "
                  "win probability.")
        return s

    # Full-Kelly optimum: maximize q*ln(B + n - cost) + (1-q)*ln(B - cost).
    def growth(n):
        win = bankroll + n - cost[n]
        lose = bankroll - cost[n]
        if win <= 0 or lose <= 0:
            return -1e18
        return q * math.log(win) + (1.0 - q) * math.log(lose)

    best_n, best_g = 0, growth(0)
    for n in range(1, ceiling + 1):
        g = growth(n)
        if g > best_g:
            best_g, best_n = g, n
    s.full_kelly = best_n

    # Fractional Kelly: largest n whose cost <= kelly_frac * cost[best_n].
    target = kelly_frac * cost[best_n]
    rec = 0
    for n in range(1, best_n + 1):
        if cost[n] <= target + 1e-9:
            rec = n
    s.contracts = rec
    s.stake = cost[rec]
    s.ev = ev[rec]
    s.avg_price = (cost[rec] / rec) if rec else None

    if not hit_ceiling and ceiling == sum(int(sz) for _, sz in ladder):
        s.note = ("Limited by book depth — the whole visible book is +EV, so the "
                  "shown size is all that's currently available to fill.")
    return s
