"""Pure trading decisions: entry gating, agreement, bracket selection, sizing,
and exit rules. No network, no Streamlit — every function takes plain data so it
is unit-testable. IO and orchestration live in trader.py.
"""
from __future__ import annotations

import kelly
import model


def market_center(implied: dict | None) -> float | None:
    """The market's own implied temperature, or None when no market is priced."""
    return None if not implied else implied.get("ev")


def gates_clear(var_snap: dict) -> tuple[bool, str]:
    """Model safety gates that block a NEW entry. Mirrors the dashboard's own
    'wait' states so the trader never buys into a forming/unsettled extreme."""
    if var_snap.get("low_forming"):
        return False, "low still forming"
    if var_snap.get("front_widened"):
        return False, "front risk widened"
    if var_snap.get("convective_widened"):
        return False, "convective risk widened"
    return True, ""


def entry_allowed(var_snap: dict, implied: dict | None, params: dict,
                  variable: str) -> tuple[bool, str]:
    """Resolved floor + safety gates + model/market agreement. Returns (ok, reason)."""
    resolved = var_snap.get("resolved", 0.0)
    if resolved < params["min_resolved"]:
        return False, f"resolved {resolved:.0%} < {params['min_resolved']:.0%}"
    ok, reason = gates_clear(var_snap)
    if not ok:
        return False, reason
    mkt = market_center(implied)
    if mkt is None:
        return False, "no market center"
    model_c = var_snap.get("consensus")
    if model_c is None or abs(model_c - mkt) > params["agreement_tol"]:
        return False, f"model {model_c} vs market {mkt} disagree > {params['agreement_tol']}°F"
    return True, ""


def _between(contracts):
    return [c for c in contracts if c.get("strike_type") == "between"
            and c.get("floor") is not None and c.get("cap") is not None]


def select_bracket(contracts: list[dict], agreed_temp: float, variable: str,
                   tie_margin: float = 0.5) -> dict | None:
    """The target 'between' bracket for `agreed_temp`.

    Direct hit → the bracket whose [floor, cap] contains the temp. Near-tie
    (the temp sits in a gap between two brackets, or within `tie_margin` of a
    bracket edge that faces a neighbor) → buy in the direction the variable can
    still move: HIGH → the upper bracket, LOW → the lower. That keeps an adverse
    move heading toward the position (sellable via stop-loss) instead of settling
    it to $0 instantly."""
    br = sorted(_between(contracts), key=lambda c: c["floor"])
    if not br:
        return None

    # Candidate brackets near the agreed temp (contains it, or an edge within margin).
    def near(c):
        return (c["floor"] - tie_margin) <= agreed_temp <= (c["cap"] + tie_margin)

    cands = [c for c in br if near(c)]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    # Ambiguous straddle: direction tie-break.
    cands.sort(key=lambda c: c["floor"])
    return cands[-1] if variable == "high" else cands[0]
