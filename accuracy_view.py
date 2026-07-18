"""Accuracy Scorecard page — how good the forecast itself is, the complement to
the betting-P&L History page. Reuses market_view._render_accuracy for the detailed
body and adds glanceable headline tiles on top."""
from __future__ import annotations


def _pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def _num(v) -> str:
    return f"{v:.2f}" if v is not None else "—"


def headline_tiles(live: dict) -> list[dict]:
    """Glanceable accuracy tiles from scoring.score()'s live dict: settled-day
    count plus each variable's exact-bin %, within-±1 %, and Brier. Missing
    variables are skipped; None metrics render as an em dash."""
    tiles = [{"label": "Settled days", "value": str(live.get("n_settled", 0) or 0)}]
    by_var = live.get("by_variable") or {}
    for var in ("high", "low"):
        m = by_var.get(var)
        if not m:
            continue
        cap = var.capitalize()
        tiles.append({"label": f"{cap} exact-bin", "value": _pct(m.get("exact_peak"))})
        tiles.append({"label": f"{cap} within ±1", "value": _pct(m.get("within1"))})
        tiles.append({"label": f"{cap} Brier", "value": _num(m.get("brier"))})
    return tiles
