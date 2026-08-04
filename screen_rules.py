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
# At or above this the market has effectively resolved the bracket: there is no
# mispricing to harvest, and on a day already in progress it is simply the
# outcome that happened. A live pass without this flagged KXLOWTOKC 65-66 at
# $1.00 as "16F from the forecast" -- it was the low that had already occurred.
SETTLED_PRICE = 0.97


def _tradeable_price(row: dict):
    """The bracket's price when it is worth harvesting at all, else None."""
    price = price_of(row)
    if price is None or price < MIN_CANDIDATE_PRICE or price >= SETTLED_PRICE:
        return None
    return price


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
    price = _tradeable_price(row)
    if price is None:
        return None
    gap = bracket_gap(row.get("floor"), row.get("cap"), forecast)
    if gap is None or gap < MIN_CANDIDATE_GAP_F:
        return None
    return _candidate(row, "forecast", float(forecast), gap, price, now_iso)


# ---- Realized-extreme (hard) screen ---------------------------------------

MIN_OBS_SUPPORT = 2


def c_to_f(celsius):
    """NWS observations report Celsius; brackets are Fahrenheit."""
    if celsius is None:
        return None
    return round(float(celsius) * 9.0 / 5.0 + 32.0, 1)


def realized_extreme(temps_f: list, variable: str,
                     min_support: int = MIN_OBS_SUPPORT):
    """The day's realized extreme so far, or None when unestablished.

    Physics, not forecasting: the minimum realized so far is a CEILING on the
    settled low, and the maximum a FLOOR on the settled high. Neither can move
    back the other way.

    `min_support` guards against a single spurious reading: the returned extreme
    is the most extreme value that at least `min_support` observations reach.
    One bad 40F print must not declare every bracket above it dead."""
    values = sorted(float(t) for t in temps_f if t is not None)
    if len(values) < min_support:
        return None
    if variable == "low":
        return values[min_support - 1]      # min corroborated by min_support
    if variable == "high":
        return values[-min_support]         # max corroborated by min_support
    return None


def dead_candidate(row: dict, bound, now_iso: str):
    """A bracket the realized extreme has already made impossible, or None.

    For a LOW, `bound` is the realized minimum and any bracket entirely ABOVE it
    is dead. For a HIGH, `bound` is the realized maximum and any bracket
    entirely BELOW it is dead."""
    if bound is None:
        return None
    price = _tradeable_price(row)
    if price is None:
        return None
    variable = row.get("variable")
    floor, cap = row.get("floor"), row.get("cap")
    if variable == "low":
        if floor is None or floor <= bound:
            return None
        gap = round(float(floor) - float(bound), 2)
    elif variable == "high":
        if cap is None or cap >= bound:
            return None
        gap = round(float(bound) - float(cap), 2)
    else:
        return None
    return _candidate(row, "dead", float(bound), gap, price, now_iso)
