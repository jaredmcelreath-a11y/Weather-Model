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
    return math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)) if pairs else None


def _mae(errs):
    return sum(abs(e) for e in errs) / len(errs) if errs else None


def metrics(joined: list[dict]) -> dict:
    """Group joined rows by (capture_slot, variable) and compute model-vs-market
    (Q1) and flat-vs-live offset (Q2, high rows only) edge stats."""
    groups: dict = {}
    for r in joined:
        groups.setdefault((r["capture_slot"], r["variable"]), []).append(r)

    out = {}
    for key, rows in groups.items():
        variable = key[1]
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
        out[key] = entry
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


_COLS = ["capture_slot", "variable", "n", "model_mae", "market_mae",
         "disagreements", "model_bin_wins", "market_bin_wins", "n_boundary",
         "flat_rmse", "live_rmse", "flip_toward", "flip_away"]


def write_report(metrics_by_key: dict, out_dir: str) -> list[str]:
    """Write metrics.csv and ASSESSMENT.md to out_dir. Create out_dir if absent.
    Returns list of written paths."""
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "metrics.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_COLS)
        for (slot, variable), m in sorted(metrics_by_key.items()):
            w.writerow([slot, variable] + [m.get(c) for c in _COLS[2:]])

    md_path = os.path.join(out_dir, "ASSESSMENT.md")
    lines = ["# Betting-time edge report", "",
             "Model-vs-market and flat-vs-live settlement-offset, by capture slot.",
             "Q2 (flat vs live RMSE) is measured on high rows only.", ""]
    for (slot, variable), m in sorted(metrics_by_key.items()):
        lines.append(f"## {slot} — {variable} (n={m['n']}, boundary days={m['n_boundary']})")
        lines.append(f"- Model MAE {m['model_mae']} vs Market MAE {m['market_mae']}")
        lines.append(f"- Disagreements {m['disagreements']}: model won {m['model_bin_wins']}, "
                     f"market won {m['market_bin_wins']}")
        if m.get("live_rmse") is not None:
            verdict = ("live gap BEATS flat" if (m["flat_rmse"] or 0) - (m["live_rmse"] or 0) >= 0.15
                       else "no clear offset edge")
            lines.append(f"- Offset: flat RMSE {m['flat_rmse']} vs live RMSE {m['live_rmse']} "
                         f"({verdict}); bin flips toward {m['flip_toward']} / away {m['flip_away']}")
        lines.append("")
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    return [csv_path, md_path]
