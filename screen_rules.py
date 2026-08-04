"""Screening rules: which live brackets are worth a human's two minutes.

Two independent screens, both returning candidate dicts of the same shape:

  forecast_candidate -- SOFT. The market pays real money for an outcome far from
      the NWS forecast. Reported as a DISTANCE in degrees, never as a
      probability: there is no per-city calibrated sigma here, and converting a
      gap into a probability with an invented one manufactures a confident
      number that is a guess (see the 2026-07-17 season-readiness bug, where a
      bin outside the model's range printed 0% and produced a live
      "0% -> BUY NO +85").

  dead_candidate -- HARD. Realized temperature has already made the bracket
      impossible. No calibration, no judgment.
"""
from __future__ import annotations

MIN_CANDIDATE_PRICE = 0.10
MIN_CANDIDATE_GAP_F = 4.0


def price_of(row: dict):
    """What acting on this bracket would cost: the YES ask, or the bid when
    there is no offer. You cannot trade the midpoint."""
    ask, bid = row.get("yes_ask"), row.get("yes_bid")
    return ask if ask is not None else bid


def bracket_gap(floor, cap, value: float):
    """Degrees from `value` to the nearest edge of [floor, cap]; 0 inside it.

    Open-ended tails carry one strike: 'greater' has no cap, 'less' no floor,
    and each is unbounded on its missing side."""
    if value is None or (floor is None and cap is None):
        return None
    if floor is not None and value < floor:
        return round(float(floor) - float(value), 2)
    if cap is not None and value > cap:
        return round(float(value) - float(cap), 2)
    return 0.0


def _candidate(row: dict, kind: str, reference: float, gap: float,
               price: float, now_iso: str) -> dict:
    return {
        "ts": now_iso,
        "series": row.get("series"),
        "variable": row.get("variable"),
        "ticker": row.get("ticker"),
        "floor": row.get("floor"),
        "cap": row.get("cap"),
        "price": price,
        "forecast": reference,
        "gap": gap,
        "kind": kind,
        "hours_to_close": row.get("hours_to_close"),
    }


def forecast_candidate(row: dict, forecast, now_iso: str):
    """A richly-priced bracket far from the forecast, or None."""
    if forecast is None:
        return None
    price = price_of(row)
    if price is None or price < MIN_CANDIDATE_PRICE:
        return None
    gap = bracket_gap(row.get("floor"), row.get("cap"), forecast)
    if gap is None or gap < MIN_CANDIDATE_GAP_F:
        return None
    return _candidate(row, "forecast", float(forecast), gap, price, now_iso)
