"""Reliability curve for the multi-city scanner.

Of the brackets priced in a given band, what fraction actually settled YES? A
well-calibrated market lands on the diagonal; the favorite-longshot bias predicts
that low-priced brackets settle LESS often than their price implies, which is the
inefficiency a fade strategy would harvest.
"""
from __future__ import annotations

PRICE_BANDS = [(0.05, 0.15), (0.15, 0.30), (0.30, 0.45),
               (0.45, 0.60), (0.60, 0.80), (0.80, 0.95)]

HOUR_BUCKETS = [(0, 6), (6, 18), (18, 36)]


def mid(row: dict):
    """Fair-value price: the bid/ask midpoint, or whichever side exists alone."""
    bid, ask = row.get("yes_bid"), row.get("yes_ask")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return ask if ask is not None else bid


def no_cost(row: dict):
    """What FADING this bracket would actually have cost: buying NO sells against
    the YES bid, so the price paid is 1 - yes_bid. The mid says whether a market
    is fair; this says whether the edge survives the spread."""
    bid = row.get("yes_bid")
    return None if bid is None else round(1.0 - bid, 4)


def _bucket(value, buckets):
    for lo, hi in buckets:
        if lo <= value < hi:
            return (lo, hi)
    return None


def reliability(rows: list, settled: list, bands=PRICE_BANDS,
                hour_buckets=HOUR_BUCKETS) -> list:
    """Observed settle-YES rate per series x variable x price band x hours-to-close.

    Rows with no settlement on file are excluded — an unsettled bracket has no
    outcome to score against, and counting it would silently deflate every rate.
    """
    outcome = {r.get("ticker"): (r.get("result") or "").lower() for r in settled}
    groups: dict = {}
    for row in rows:
        result = outcome.get(row.get("ticker"))
        if result not in ("yes", "no"):
            continue
        price = mid(row)
        hours = row.get("hours_to_close")
        if price is None or hours is None:
            continue
        band = _bucket(price, bands)
        hbucket = _bucket(hours, hour_buckets)
        if band is None or hbucket is None:
            continue
        key = (row.get("series"), row.get("variable"), band, hbucket)
        g = groups.setdefault(key, {"n": 0, "yes": 0, "tickers": set(),
                                    "no_costs": []})
        g["n"] += 1
        g["yes"] += 1 if result == "yes" else 0
        g["tickers"].add(row.get("ticker"))
        cost = no_cost(row)
        if cost is not None:
            g["no_costs"].append(cost)

    out = []
    for (series, variable, band, hours), g in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][3], kv[0][2])):
        costs = g["no_costs"]
        out.append({
            "series": series, "variable": variable, "band": band, "hours": hours,
            "n_observations": g["n"],
            "n_unique_brackets": len(g["tickers"]),
            "settled_yes": g["yes"],
            "hit_rate": round(g["yes"] / g["n"], 4),
            "mid_band_center": round((band[0] + band[1]) / 2.0, 4),
            "mean_no_cost": round(sum(costs) / len(costs), 4) if costs else None,
        })
    return out


def format_table(stats: list) -> str:
    """Fixed-width rendering for the Action log."""
    header = (f"{'series':<14} {'var':<5} {'band':<12} {'hrs':<8} "
              f"{'n':>5} {'brk':>5} {'settled':>8} {'implied':>8} {'noCost':>7}")
    lines = [header, "-" * len(header)]
    for s in stats:
        band = f"{s['band'][0]:.0%}-{s['band'][1]:.0%}"
        hrs = f"{s['hours'][0]}-{s['hours'][1]}h"
        cost = "—" if s["mean_no_cost"] is None else f"{s['mean_no_cost']:.2f}"
        lines.append(
            f"{s['series']:<14} {s['variable']:<5} {band:<12} {hrs:<8} "
            f"{s['n_observations']:>5} {s['n_unique_brackets']:>5} "
            f"{s['hit_rate']:>7.1%} {s['mid_band_center']:>7.1%} {cost:>7}")
    return "\n".join(lines)
