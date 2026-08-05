"""Did the Screen's flagged fades actually win?

The screen has been listing brackets since 2026-08-03 with nothing measuring
whether it was right, so every threshold on that page — the 4F gap, the 0.10
price floor, the 0.20 live-NO gate — is a guess with no feedback. This joins
`scan_candidates` to `scan_settled` on `ticker` and reports the answer.

THE TRAP THIS EXISTS TO AVOID: of the settled brackets on the scan-data branch,
83.4% settled NO (6,667 of 7,994). A screen whose fades "win 83% of the time"
therefore has ZERO edge. Raw hit rate flatters this table enormously and proves
nothing, so the headline here is always edge against the price paid, and the
base rate is printed beside it as the reference line.

Read-only measurement. Changes no threshold and places no order.
"""
from __future__ import annotations

import math

import scan_log
import scan_report

# Decisions below which no verdict is given. Matches the culture of the rest of
# the repo (MIN_LEAD_DAYS, min_nights): report the counts, refuse the
# conclusion. The first runs of this WILL be below it -- candidates start
# 2026-08-03 and settlements 2026-08-04 -- and that is the correct output.
MIN_SAMPLE = 30

GAP_BANDS = [(4.0, 5.0), (5.0, 7.0), (7.0, 999.0)]
HOUR_BANDS = [(0.0, 6.0), (6.0, 18.0), (18.0, 999.0)]
STORM_BANDS = [(0.0, 1.0), (1.0, 40.0), (40.0, 101.0)]


def first_flags(candidates: list) -> dict:
    """{ticker: its EARLIEST candidate row, plus a `firings` count}.

    A bracket flagged at twenty consecutive firings is one trading decision, not
    twenty, and the decision was taken at the first price you could have acted
    on. Scoring every firing would let one long-lived row dominate the sample;
    scoring the best price would assume perfect timing. `firings` keeps the
    persistence visible without letting it distort the count."""
    out = {}
    for row in candidates or []:
        ticker = row.get("ticker")
        if not ticker:
            continue
        seen = out.get(ticker)
        if seen is None:
            out[ticker] = {**row, "firings": 1}
        else:
            seen["firings"] += 1
            if str(row.get("ts") or "") < str(seen.get("ts") or ""):
                out[ticker] = {**row, "firings": seen["firings"]}
    return out


def settle_map(settled: list) -> dict:
    """{ticker: 'yes'|'no'} from the settlement log."""
    return {r["ticker"]: r.get("result") for r in settled or []
            if r.get("ticker")}


def base_rate(settled: list):
    """Fraction of ALL settled brackets that went NO — the reference line.

    Fading a bracket chosen at random wins this often, so a screen that cannot
    beat it is finding nothing. None when the log is empty."""
    results = [r.get("result") for r in settled or []]
    results = [r for r in results if r in ("yes", "no")]
    if not results:
        return None
    return round(sum(1 for r in results if r == "no") / len(results), 4)


def _cost(row: dict):
    """What fading this bracket actually cost, and whether that is exact.

    Buying NO sells against the resting YES bid, so the true price is
    1 - yes_bid (scan_report.no_cost). Rows logged before the bid was captured
    fall back to 1 - yes_ask, which is CHEAPER than reality and therefore
    biases every number here in the screen's favour — hence the flag, so a
    report can say how much of its sample is approximate."""
    exact = scan_report.no_cost(row)
    if exact is not None:
        return exact, True
    price = row.get("price")
    return (None, False) if price is None else (round(1.0 - float(price), 4),
                                                False)


def score(candidates: list, settled: list) -> list:
    """One record per flagged bracket that has since settled.

    A bracket with no settlement is EXCLUDED, not counted as a loss: today's
    flags have not resolved yet, and treating them as losses would make every
    fresh firing look like a disaster."""
    results = settle_map(settled)
    out = []
    for ticker, row in first_flags(candidates).items():
        result = results.get(ticker)
        if result not in ("yes", "no"):
            continue
        cost, exact = _cost(row)
        if cost is None:
            continue
        won = result == "no"
        out.append({
            "ticker": ticker,
            "series": row.get("series"),
            "variable": row.get("variable"),
            "kind": row.get("kind"),
            "gap": row.get("gap"),
            "storm": row.get("storm"),
            "hours_to_close": row.get("hours_to_close"),
            "firings": row.get("firings", 1),
            "price": row.get("price"),
            "cost": cost,
            "exact": exact,
            "result": result,
            "won": won,
            # pnl = 1{won} - cost, exactly: a winner returns 1.00 against the
            # cost, a loser returns nothing. Written this way because it makes
            # the identity in summarize() self-evident.
            "pnl": round((1.0 if won else 0.0) - cost, 4),
        })
    return out


def summarize(records: list) -> dict:
    """Headline numbers over `records`.

    `edge` and `ev_per_contract` are THE SAME NUMBER in different units, not two
    findings: since pnl = 1{won} - cost, the mean pnl is exactly
    (hit rate - mean cost). Reporting them as independent evidence would be
    double-counting one result."""
    n = len(records)
    if not n:
        return {"n": 0, "wins": 0, "hit_rate": None, "mean_implied": None,
                "edge": None, "ev_per_contract": None, "total_pnl": 0.0,
                "se": None, "enough": False, "exact_n": 0}
    wins = sum(1 for r in records if r["won"])
    hit_rate = wins / n
    mean_implied = sum(r["cost"] for r in records) / n
    edge = hit_rate - mean_implied
    return {
        "n": n,
        "wins": wins,
        "hit_rate": round(hit_rate, 4),
        "mean_implied": round(mean_implied, 4),
        "edge": round(edge, 4),
        "ev_per_contract": round(sum(r["pnl"] for r in records) / n, 4),
        "total_pnl": round(sum(r["pnl"] for r in records), 4),
        # Binomial standard error of the win rate: an edge smaller than this is
        # not distinguishable from noise.
        "se": round(math.sqrt(hit_rate * (1 - hit_rate) / n), 4),
        "enough": n >= MIN_SAMPLE,
        "exact_n": sum(1 for r in records if r["exact"]),
    }


def _band(value, bands):
    if value is None:
        return None
    for lo, hi in bands:
        if lo <= float(value) < hi:
            return (lo, hi)
    return None


def _group(records: list, key) -> dict:
    out = {}
    for r in records:
        out.setdefault(key(r), []).append(r)
    return {k: summarize(v) for k, v in out.items() if k is not None}


def by_kind(records: list) -> dict:
    """'dead' vs 'forecast'. The spec claims the hard screen is near-certain and
    the soft one is barely better than the market; this is the first test."""
    return _group(records, lambda r: r.get("kind"))


def by_gap(records: list) -> dict:
    return _group(records, lambda r: _band(r.get("gap"), GAP_BANDS))


def by_hours(records: list) -> dict:
    """The flat 4F threshold treats a 30-hour gap like a 3-hour one, though the
    measured day-ahead sigma is nearly 3x the same-day figure."""
    return _group(records, lambda r: _band(r.get("hours_to_close"), HOUR_BANDS))


def by_storm(records: list) -> dict:
    return _group(records, lambda r: _band(r.get("storm"), STORM_BANDS))


def _line(label: str, s: dict) -> str:
    if not s["n"]:
        return f"{label:<16} —"
    edge = f"{s['edge']:+.1%}"
    flag = "" if s["enough"] else "  (thin)"
    return (f"{label:<16} n={s['n']:>4}  hit={s['hit_rate']:>6.1%}  "
            f"implied={s['mean_implied']:>6.1%}  edge={edge:>7}  "
            f"±{s['se']:.1%}  P&L={s['total_pnl']:>+7.2f}{flag}")


def format_report(records: list, settled: list) -> str:
    """Fixed-width rendering for the Action log, in scan_report's house style."""
    overall = summarize(records)
    rate = base_rate(settled)
    lines = ["SCREEN OUTCOME SCORING", "=" * 78]
    if rate is not None:
        lines.append(f"Base rate: {rate:.1%} of ALL settled brackets went NO. "
                     f"An edge below this is not a finding.")
    lines.append(_line("OVERALL", overall))
    if overall["n"] and overall["exact_n"] < overall["n"]:
        lines.append(f"  {overall['n'] - overall['exact_n']} of {overall['n']} "
                     f"priced off the ASK, not the bid — those are optimistic.")
    if not overall["enough"]:
        lines.append(f"  Below MIN_SAMPLE={MIN_SAMPLE}: counts only, no verdict.")
    for title, groups in (("BY KIND", by_kind(records)),
                          ("BY GAP", by_gap(records)),
                          ("BY HOURS TO CLOSE", by_hours(records)),
                          ("BY STORM %", by_storm(records))):
        lines += ["", title, "-" * 78]
        if not groups:
            lines.append("(no settled rows carry this field yet)")
        for key in sorted(groups, key=str):
            label = key if isinstance(key, str) else f"{key[0]:g}-{key[1]:g}"
            lines.append(_line(label, groups[key]))
    return "\n".join(lines)


def load(days: int = 30, transport=None) -> tuple:
    """(candidates, settlements) over the last `days` daily partitions."""
    return (scan_log.load_recent(scan_log.CANDIDATES_PATH, days, transport),
            scan_log.load_recent(scan_log.SETTLED_PATH, days, transport))


def main(argv: list) -> int:
    if (argv[0] if argv else "") != "report":
        print("usage: screen_score.py report [days]")
        return 2
    days = int(argv[1]) if len(argv) > 1 else 30
    candidates, settled = load(days)
    print(format_report(score(candidates, settled), settled))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
