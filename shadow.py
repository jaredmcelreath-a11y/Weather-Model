"""Shadow (candidate) consensus comparison — pure data, no Streamlit.

Turns a snapshot that carries an optional `candidate` block (from
model.snapshot(include_candidate=True)) into comparison rows the Forecast page
renders next to the production consensus.
"""
from __future__ import annotations


def _consensus(pred: dict, variable: str):
    d = (pred or {}).get(variable) or {}
    return d.get("consensus")


def consensus_comparison(snap: dict) -> list[dict]:
    """[{day, variable, production, candidate, gap}] for today/tomorrow high/low.

    Empty when the snapshot has no candidate block. `gap` = candidate -
    production (rounded to 0.1), or None if either side is missing.
    """
    candidate = (snap or {}).get("candidate")
    if not candidate:
        return []
    rows: list[dict] = []
    for which in ("today", "tomorrow"):
        prod_pred = snap.get(which) or {}
        cand_pred = candidate.get(which) or {}
        for variable in ("high", "low"):
            p = _consensus(prod_pred, variable)
            c = _consensus(cand_pred, variable)
            gap = round(c - p, 1) if (p is not None and c is not None) else None
            rows.append({"day": which, "variable": variable,
                         "production": p, "candidate": c, "gap": gap})
    return rows
