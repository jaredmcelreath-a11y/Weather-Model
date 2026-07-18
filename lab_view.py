"""Lab page — scored forward-log experiments.

Section A: shadow (candidate) consensus vs production, from forecast-log rows
that carry candidate_consensus. Section B: per-model scoreboard from the
per-source means, incl. MOS at matched lead. Pure data assembly here;
streamlit only in render — mirrors edge_view.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import market_view


def head_to_head(rows: list[dict], settled: dict) -> dict:
    """{(variable, lead_bucket): {n, prod_mae, cand_mae, prod_wins, cand_wins,
    ties, days}} for every settled rolling CLI row carrying a candidate
    consensus. Rolling rows only (the 0900 cohort would double-count the day).
    `days` = [{date, prod_err, cand_err}] ascending, for the error chart."""
    out: dict = {}
    for r in rows:
        if r.get("candidate_consensus") is None or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli" or r.get("consensus") is None:
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        key = (r["variable"], r.get("lead_bucket") or 0)
        g = out.setdefault(key, {"n": 0, "_pa": 0.0, "_ca": 0.0, "prod_wins": 0,
                                 "cand_wins": 0, "ties": 0, "days": []})
        pe = abs(r["consensus"] - actual)
        ce = abs(r["candidate_consensus"] - actual)
        g["n"] += 1
        g["_pa"] += pe
        g["_ca"] += ce
        if round(pe, 2) == round(ce, 2):
            g["ties"] += 1
        elif pe < ce:
            g["prod_wins"] += 1
        else:
            g["cand_wins"] += 1
        g["days"].append({"date": r["target_date"], "prod_err": round(pe, 2),
                          "cand_err": round(ce, 2)})
    for g in out.values():
        g["prod_mae"] = round(g.pop("_pa") / g["n"], 2)
        g["cand_mae"] = round(g.pop("_ca") / g["n"], 2)
        g["days"].sort(key=lambda d: d["date"])
    return out


# Same-day mos_lav lows logged before this date carry the wrong-tail
# covers_extreme bug (fixed 2026-07-18, commit 14a2a3a) — a now-forward LAMP
# feed reported the evening tail (~84) as the "low" on settled-77 nights.
_MOS_LAV_LOW_FIX = "2026-07-19"


def per_model_scores(rows: list[dict], settled: dict) -> dict:
    """{(source, variable, lead_bucket): {n, mae, bias}} from the per-source
    means the forecast log records (ensemble / deterministic / nws / mos_*).
    bias = mean(model − settled): positive means the source ran hot. Rolling
    rows only, matched lead by construction (the sources were captured at the
    same moment as the row's consensus)."""
    out: dict = {}
    for r in rows:
        src = r.get("sources")
        if not src or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli":
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        lead = r.get("lead_bucket") or 0
        for name, val in src.items():
            if val is None:
                continue
            if (name == "mos_lav" and r.get("variable") == "low" and lead == 0
                    and r["target_date"] < _MOS_LAV_LOW_FIX):
                continue
            g = out.setdefault((name, r["variable"], lead),
                               {"n": 0, "_a": 0.0, "_s": 0.0})
            g["n"] += 1
            g["_a"] += abs(val - actual)
            g["_s"] += val - actual
    for g in out.values():
        g["mae"] = round(g.pop("_a") / g["n"], 2)
        g["bias"] = round(g.pop("_s") / g["n"], 2)
    return out
