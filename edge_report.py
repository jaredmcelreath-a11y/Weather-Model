"""Join betting_log with settlements and report model-vs-market edge and the
flat-vs-live settlement-offset predictor. Analysis only — no live path reads this.
"""
from __future__ import annotations

import math


def settled_bucket(temp: float, buckets: list) -> tuple | None:
    """The (lo, hi) Kalshi bucket that `temp` falls in; open ends use None."""
    for lo, hi, _p in buckets:
        lo_ok = lo is None or temp >= lo
        hi_ok = hi is None or temp <= hi
        if lo_ok and hi_ok:
            return (lo, hi)
    return None


def top_bucket(buckets: list) -> tuple | None:
    if not buckets:
        return None
    lo, hi, _p = max(buckets, key=lambda b: b[2])
    return (lo, hi)


def is_boundary(consensus: float, half_width: float = 0.5) -> bool:
    """True when consensus is within half_width of an even|odd Kalshi edge (even+0.5)."""
    edges = [e + 0.5 for e in range(60, 120, 2)]   # ...94.5, 96.5, 98.5...
    return min(abs(consensus - e) for e in edges) <= half_width
