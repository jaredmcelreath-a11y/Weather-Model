"""Join betting_log with settlements and report model-vs-market edge and the
flat-vs-live settlement-offset predictor. Analysis only — no live path reads this.
"""
from __future__ import annotations

import csv
import math
import os
from datetime import date as _date


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


def _rmse(pairs):
    if not pairs:
        return None
    return round(math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)), 4)


def _mae(errs):
    return round(sum(abs(e) for e in errs) / len(errs), 4) if errs else None


def _subset_metrics(rows: list[dict], variable: str) -> dict:
    """Q1 (model-vs-market) + Q2 (flat-vs-live offset, high only) stats over one
    list of rows. Q2 fields stay None for the low variable."""
    model_err = [r["cli_consensus"] - r["settled_cli"] for r in rows]
    market_err = [r["market_ev"] - r["settled_cli"] for r in rows if r.get("market_ev") is not None]

    disagreements = model_bin_wins = market_bin_wins = 0
    for r in rows:
        if not r.get("market_buckets"):
            continue
        model_b = settled_bucket(r["cli_consensus"], r["market_buckets"])
        market_b = top_bucket(r["market_buckets"])
        actual_b = settled_bucket(r["settled_cli"], r["market_buckets"])
        if model_b != market_b:
            disagreements += 1
            if model_b == actual_b:
                model_bin_wins += 1
            elif market_b == actual_b:
                market_bin_wins += 1

    entry = {
        "n": len(rows),
        "model_mae": _mae(model_err),
        "market_mae": _mae(market_err),
        "disagreements": disagreements,
        "model_bin_wins": model_bin_wins,
        "market_bin_wins": market_bin_wins,
        "n_boundary": sum(1 for r in rows if is_boundary(r["cli_consensus"])),
        "flat_rmse": None, "live_rmse": None, "flip_toward": None, "flip_away": None,
    }
    if variable == "high":
        og = [r for r in rows if r.get("live_gap") is not None and r.get("actual_gap") is not None]
        entry["flat_rmse"] = _rmse([(r["flat_offset"], r["actual_gap"]) for r in og])
        entry["live_rmse"] = _rmse([(r["live_gap"], r["actual_gap"]) for r in og])
        toward = away = 0
        for r in og:
            flat_pred = round(r["settled_hourly"] + r["flat_offset"])
            live_pred = round(r["settled_hourly"] + r["live_gap"])
            truth = round(r["settled_cli"])
            if flat_pred != live_pred:
                if live_pred == truth:
                    toward += 1
                elif flat_pred == truth:
                    away += 1
        entry["flip_toward"], entry["flip_away"] = toward, away
    return entry


def metrics(joined: list[dict]) -> dict:
    """Group joined rows by (capture_slot, variable) and compute model-vs-market
    (Q1) and flat-vs-live offset (Q2, high only) stats, SLICED by day type.

    Returns {(capture_slot, variable, subset): stats} with subset in
    'all' / 'boundary' / 'mid_bin'. The decision gate is specifically about
    boundary days (consensus near a Kalshi bin edge), so those get their own
    entry rather than being pooled into 'all'. 'all' is always emitted;
    'boundary'/'mid_bin' only when that subset has at least one row."""
    groups: dict = {}
    for r in joined:
        groups.setdefault((r["capture_slot"], r["variable"]), []).append(r)

    out = {}
    for (slot, variable), rows in groups.items():
        boundary = [r for r in rows if is_boundary(r["cli_consensus"])]
        mid_bin = [r for r in rows if not is_boundary(r["cli_consensus"])]
        out[(slot, variable, "all")] = _subset_metrics(rows, variable)
        if boundary:
            out[(slot, variable, "boundary")] = _subset_metrics(boundary, variable)
        if mid_bin:
            out[(slot, variable, "mid_bin")] = _subset_metrics(mid_bin, variable)
    return out


def join(betting_rows: list[dict], cli_map: dict, hourly_map: dict) -> list[dict]:
    """Augment each settled row with settled_cli/settled_hourly/actual_gap."""
    out = []
    for r in betting_rows:
        d = _date.fromisoformat(r["target_date"])
        if d not in cli_map or d not in hourly_map:
            continue
        vi = 0 if r["variable"] == "high" else 1
        settled_cli = cli_map[d][vi]
        settled_hourly = hourly_map[d][vi]
        out.append({**r,
                    "settled_cli": settled_cli,
                    "settled_hourly": settled_hourly,
                    "actual_gap": settled_cli - settled_hourly})
    return out


_COLS = ["capture_slot", "variable", "subset", "n", "model_mae", "market_mae",
         "disagreements", "model_bin_wins", "market_bin_wins", "n_boundary",
         "flat_rmse", "live_rmse", "flip_toward", "flip_away"]

# subset display order within a (slot, variable), decision-relevant first
_SUBSET_ORDER = {"boundary": 0, "all": 1, "mid_bin": 2}


def _subset_line(subset: str, m: dict) -> str:
    """One ASSESSMENT bullet for a subset's stats."""
    head = {"boundary": "BOUNDARY", "all": "all", "mid_bin": "mid-bin"}[subset]
    parts = [f"- **{head}** (n={m['n']}): Model MAE {m['model_mae']} vs Market MAE "
             f"{m['market_mae']}; disagreements {m['disagreements']} "
             f"(model {m['model_bin_wins']} / market {m['market_bin_wins']})"]
    if m.get("live_rmse") is not None:
        verdict = ("live gap BEATS flat" if (m["flat_rmse"] or 0) - (m["live_rmse"] or 0) >= 0.15
                   else "no clear offset edge")
        parts.append(f"; offset flat RMSE {m['flat_rmse']} vs live RMSE {m['live_rmse']} "
                     f"({verdict}), flips toward {m['flip_toward']} / away {m['flip_away']}")
    return "".join(parts)


def write_report(metrics_by_key: dict, out_dir: str) -> list[str]:
    """Write metrics.csv and ASSESSMENT.md to out_dir. Create out_dir if absent.
    Keys are (capture_slot, variable, subset). Returns list of written paths."""
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "metrics.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_COLS)
        for (slot, variable, subset), m in sorted(metrics_by_key.items()):
            w.writerow([slot, variable, subset] + [m.get(c) for c in _COLS[3:]])

    # Group by (slot, variable) so each block shows its boundary/all/mid-bin split.
    blocks: dict = {}
    for (slot, variable, subset), m in metrics_by_key.items():
        blocks.setdefault((slot, variable), {})[subset] = m

    md_path = os.path.join(out_dir, "ASSESSMENT.md")
    lines = ["# Betting-time edge report", "",
             "Model-vs-market (Q1) and flat-vs-live settlement-offset (Q2, high only), "
             "sliced by day type. **BOUNDARY** = consensus near a Kalshi bin edge — the "
             "days the decision gate turns on; mid-bin days rarely move a bin.", ""]
    for (slot, variable) in sorted(blocks):
        subsets = blocks[(slot, variable)]
        lines.append(f"## {slot} — {variable}")
        for subset in sorted(subsets, key=lambda s: _SUBSET_ORDER.get(s, 9)):
            lines.append(_subset_line(subset, subsets[subset]))
        lines.append("")
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    return [csv_path, md_path]


def _settlement_maps():
    import settlements
    return settlements.as_map("cli"), settlements.as_map("hourly")


def run(betting_rows: list[dict], out_dir: str) -> list[str]:
    cli_map, hourly_map = _settlement_maps()
    joined = join(betting_rows, cli_map, hourly_map)
    return write_report(metrics(joined), out_dir)


if __name__ == "__main__":
    import sys
    from datetime import date
    import betting_log
    today = date.today().isoformat()
    if "--retro" in sys.argv:
        import forecast_log
        rows = []
        for r in forecast_log.load(forecast_log._PATH):
            if r.get("variable") == "high" and r.get("lead_bucket") == 0 and r.get("market"):
                rows.append({"target_date": r["target_date"], "variable": "high",
                             "capture_slot": "retro", "cli_consensus": r.get("consensus"),
                             "market_ev": r["market"].get("ev"),
                             "market_buckets": r["market"].get("buckets"),
                             "model_bins": [], "flat_offset": 0.89, "live_gap": None})
        out = f"docs/benchmarks/{today}/edge-retro"
        paths = run(rows, out)
        print(f"RETRO (directional only, n={len(rows)}): {paths}")
    else:
        rows = betting_log.load()
        out = f"docs/benchmarks/{today}/edge"
        print(run(rows, out))
