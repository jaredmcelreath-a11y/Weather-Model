"""Before/after validation of the storm-proof correction estimators on the
REAL data-branch logs.

OLD = the retired estimator (all-time, unfiltered mean / std) recomputed inline.
NEW = the shipped per_lead_bias / per_lead_sigma (windowed, flag-excluded, median).

Gates (from the spec):
  1. NEW lead-0 low bias correction is ABSENT (the old mean-based path emitted
     ~-0.33 purely from the June 26-28 storm nights; the median reads ~0).
  2. NEW lead-24 high correction SURVIVES (the day-ahead warm bias is
     consistent across days, not outlier-driven).
  3. Rerun with today=2026-08-15 (June 26-28 aged out of the 45-day window):
     lead-0 low sigma DROPS vs its value today (~1.25 -> calm-night level),
     proving the contamination self-heals via the window.

Run from the repo root:
  .venv/bin/python docs/benchmarks/2026-07-13/robust-corrections/validate.py
"""
import math
import os
import statistics
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import forecast_log
import scoring
import settlements
from scoring import MIN_LEAD_DAYS, SHRINK_K, SIG_Z

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "RESULTS.md")


def _fetch_data_branch():
    subprocess.run(["git", "fetch", "origin", "data"], check=True, capture_output=True)
    for name in ("forecast_log.jsonl", "settlements.jsonl"):
        text = subprocess.run(["git", "show", f"origin/data:{name}"], check=True,
                              capture_output=True, text=True).stdout
        with open(os.path.join(HERE, name), "w") as fh:
            fh.write(text)
    forecast_log._PATH = os.path.join(HERE, "forecast_log.jsonl")
    settlements._PATH = os.path.join(HERE, "settlements.jsonl")


def _patch_actuals():
    cli = settlements.as_map("cli")
    hourly = settlements.as_map("hourly")
    scoring._actuals_for = lambda records, basis="hourly": cli if basis == "cli" else hourly


def _old_estimators(today, basis="cli"):
    """The retired behavior: all-time, unfiltered mean bias + std sigma."""
    records = [r for r in scoring._settled_records(today)
               if r.get("basis", "hourly") == basis and r.get("consensus") is not None]
    actual = scoring._actuals_for(records, basis)
    resid = {}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        act = actual[d][0] if r["variable"] == "high" else actual[d][1]
        resid.setdefault((r["lead_bucket"], r["variable"]), []).append(r["consensus"] - act)
    bias, sigma = {}, {}
    for (bucket, var), errs in resid.items():
        n = len(errs)
        if n < MIN_LEAD_DAYS:
            continue
        m = sum(errs) / n
        sd = math.sqrt(sum((e - m) ** 2 for e in errs) / n)
        sigma.setdefault(bucket, {})[var] = round(sd, 2)
        if abs(m) > SIG_Z * sd / math.sqrt(n):
            bias.setdefault(bucket, {})[var] = round(m * n / (n + SHRINK_K), 2)
    return bias, sigma


def main():
    _fetch_data_branch()
    _patch_actuals()
    today = date.today()
    old_bias, old_sigma = _old_estimators(today)
    new_bias = scoring.per_lead_bias(basis="cli", today=today)
    new_sigma = scoring.per_lead_sigma(basis="cli", today=today)
    aug_sigma = scoring.per_lead_sigma(basis="cli", today=date(2026, 8, 15))

    g1 = "low" not in new_bias.get(0, {})
    g2 = "high" in new_bias.get(24, {})
    s_now = new_sigma.get(0, {}).get("low")
    s_aug = aug_sigma.get(0, {}).get("low")
    g3 = s_now is not None and s_aug is not None and s_aug < s_now

    lines = [
        "# Storm-proof corrections — before/after on the live data-branch logs",
        f"\nRun date: {today}\n",
        "| estimator | OLD (all-time mean/std) | NEW (windowed, flagged-out, median) |",
        "|---|---|---|",
        f"| bias | {old_bias} | {new_bias} |",
        f"| sigma | {old_sigma} | {new_sigma} |",
        f"| sigma @ today=2026-08-15 | — | {aug_sigma} |",
        "",
        f"- Gate 1 (lead-0 low phantom correction gone): {'PASS' if g1 else 'FAIL'} "
        f"(old: {old_bias.get(0, {}).get('low')})",
        f"- Gate 2 (lead-24 high correction survives): {'PASS' if g2 else 'FAIL'} "
        f"(old: {old_bias.get(24, {}).get('high')}, new: {new_bias.get(24, {}).get('high')})",
        f"- Gate 3 (lead-0 low sigma self-heals by 2026-08-15): {'PASS' if g3 else 'FAIL'} "
        f"(now: {s_now}, aug: {s_aug})",
    ]
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not (g1 and g2 and g3):
        sys.exit(1)


if __name__ == "__main__":
    main()
