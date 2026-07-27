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


def stop_loss_hit(entry_ask: float, current_ask: float, stop_loss: float) -> bool:
    """Ask-referenced stop: exit when the current ask has fallen `stop_loss`
    below the entry ask. Never trips on the bid/spread at fill time. The 1e-9
    epsilon absorbs float subtraction noise (e.g. 0.60-0.20 == 0.3999…)."""
    return current_ask <= entry_ask - stop_loss + 1e-9


def should_exit(position: dict, current_ask, target_ticker, gates_ok: bool,
                params: dict) -> tuple[bool, str]:
    """Exit a held position on stop-loss OR signal reversal (a gate fired, or the
    target bracket is no longer this one). No take-profit — winners hold."""
    if not gates_ok:
        return True, "reversal: safety gate fired"
    if target_ticker is not None and target_ticker != position["ticker"]:
        return True, f"reversal: target moved to {target_ticker}"
    if current_ask is not None and stop_loss_hit(position["entry_ask"], current_ask,
                                                 params["stop_loss"]):
        return True, (f"stop-loss: ask {current_ask:.2f} <= entry "
                      f"{position['entry_ask']:.2f} - {params['stop_loss']:.2f}")
    return False, ""


def reentry_allowed(stopped_ticker: str | None, target_ticker: str) -> bool:
    """After a stop-loss, only re-enter when the signal has genuinely flipped to a
    DIFFERENT bracket (prevents churning back into the just-stopped one)."""
    return stopped_ticker != target_ticker


def size_bracket(contract: dict, var_snap: dict, orderbook: dict, bankroll: float,
                 params: dict) -> dict:
    """Fractional-Kelly size for `contract`, clamped by per_market_cap and the
    price bounds. Returns {"side","contracts","avg_price","stake","note"}."""
    probs = var_snap.get("probabilities") or {}
    p = model.prob_for_strike(probs, contract["strike_type"],
                              contract.get("floor"), contract.get("cap"))
    if p is None:
        return {"side": "", "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": "model abstains (open tail)"}
    # `require_edge` False (shadow experiment) drops the positive-edge gate so a
    # bracket that cleared the upstream checks still trades; when a real edge
    # exists it's used and Kelly-sized as usual.
    require_edge = params.get("require_edge", True)
    yes_ask, no_ask = contract.get("yes_ask"), contract.get("no_ask")
    pick = kelly.best_side(p, yes_ask, no_ask)
    if pick is None:
        if require_edge:
            return {"side": "", "contracts": 0, "avg_price": None, "stake": 0.0,
                    "note": "no edge on either side"}
        pick = kelly.preferred_side(p, yes_ask, no_ask)
        if pick is None:
            return {"side": "", "contracts": 0, "avg_price": None, "stake": 0.0,
                    "note": "no priced side to buy"}
    side, win, ask = pick
    if ask >= params["max_price"] or ask < params["min_price"]:
        return {"side": side, "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": (f"price {ask:.2f} outside "
                         f"[{params['min_price']},{params['max_price']})")}
    from sources import kalshi as _k
    ladder = _k.ask_ladder(orderbook, side)   # `orderbook` is the normalized book
    if require_edge:
        # Live-sizing path: fractional Kelly against the book. kelly_fraction was
        # retired as a user param (shadow runs use uniform sizing), so fall back
        # to a sane 0.25 if some stored/live config re-enables the edge path.
        sizing = kelly.optimal_size(ladder, win, bankroll,
                                    params.get("kelly_fraction", 0.25), side=side)
        n = sizing.contracts
        note = sizing.note
    else:
        # Shadow experiment: uniform single-contract entries, no Kelly sizing.
        # The per-market cap below still bounds (and can zero) it.
        n = 1
        note = "shadow entry (1 contract, no Kelly)"
    # Clamp to the per-market dollar cap.
    while n > 0 and (kelly.cost_to_buy(ladder, n) or 1e9) > params["per_market_cap"]:
        n -= 1
    if n <= 0:
        return {"side": side, "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": "cap or book leaves 0 contracts"}
    stake = kelly.cost_to_buy(ladder, n)
    return {"side": side, "contracts": n, "avg_price": stake / n, "stake": stake,
            "note": note}
