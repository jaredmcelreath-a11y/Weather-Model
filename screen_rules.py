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


# How much worse a forecast is a full day out than on the day itself, measured
# by scoring.per_lead_sigma() at KDFW: high 1.87/0.70, low 1.97/1.70. Only the
# RATIO is used, applied to every city -- error GROWTH with lead is a general
# property of forecasting, while the absolute error level is what varies from
# station to station. Claiming a per-city sigma is exactly the season-readiness
# phantom-edge bug, where an invented number produced a live "0% -> BUY NO +85".
#
# The asymmetry is real and must not be averaged away: a high decays 2.7x with
# lead while a low barely moves, because same-day lows are already convectively
# noisy and have less room to get worse.
LEAD_SIGMA_RATIO = {"high": 1.87 / 0.70, "low": 1.97 / 1.70}

# hours_to_close counts to the END of the climate day, so today's markets sit
# below 24 and tomorrow's above it. The extreme forms around mid-day, which puts
# a genuinely same-day forecast near 12h out and a full day-ahead one near 36h.
# Interpolating between them centres the transition on 24h without a cliff that
# would make a row jump in strength the moment it crossed.
_SAME_DAY_HOURS = 12.0
_DAY_AHEAD_HOURS = 36.0


def lead_multiplier(hours_to_close, variable: str) -> float:
    """How much bigger a gap must be at this lead to mean the same thing."""
    ratio = LEAD_SIGMA_RATIO.get(variable)
    if hours_to_close is None or ratio is None:
        return 1.0
    hours = float(hours_to_close)
    if hours <= _SAME_DAY_HOURS:
        return 1.0
    if hours >= _DAY_AHEAD_HOURS:
        return ratio
    span = (hours - _SAME_DAY_HOURS) / (_DAY_AHEAD_HOURS - _SAME_DAY_HOURS)
    return 1.0 + span * (ratio - 1.0)


def required_gap(hours_to_close, variable: str) -> float:
    """The gap this row would need to be as convincing as a same-day 4F one."""
    return MIN_CANDIDATE_GAP_F * lead_multiplier(hours_to_close, variable)


def strength(row: dict):
    """Multiples of the lead-adjusted bar this row's gap clears, or None.

    1.0 means it exactly meets the standard the flat threshold sets for a
    same-day bracket; below 1.0 it only qualified because the threshold ignores
    lead. Deliberately NOT a probability -- see the module docstring."""
    gap, hours = row.get("gap"), row.get("hours_to_close")
    if gap is None or hours is None:
        return None
    bar = required_gap(hours, row.get("variable"))
    return None if not bar else round(float(gap) / bar, 2)


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


def winning_range(row: dict):
    """The INCLUSIVE (lo, hi) temperatures at which this contract settles YES.

    Not the raw strikes. A tail's strike sits one degree outside the range it
    actually pays on, which Kalshi's own labels spell out: `greater` with
    floor_strike 90 is titled "91° or above", and `less` with cap_strike 83 is
    "82° or below". Only `between` is inclusive of both strikes ("89° to 90°").

    Using the raw strikes understates every tail's distance by a degree and lets
    a tail survive the dead screen at the exact boundary where it is already
    lost. None on a side means unbounded there."""
    floor, cap = row.get("floor"), row.get("cap")
    kind = row.get("strike_type")
    if kind == "greater":
        return (None if floor is None else float(floor) + 1, None)
    if kind == "less":
        return (None, None if cap is None else float(cap) - 1)
    return (None if floor is None else float(floor),
            None if cap is None else float(cap))


def bracket_gap(row: dict, value: float):
    """Degrees from `value` to the nearest temperature this bracket pays on;
    0 when `value` already falls inside it."""
    lo, hi = winning_range(row)
    if value is None or (lo is None and hi is None):
        return None
    if lo is not None and value < lo:
        return round(lo - float(value), 2)
    if hi is not None and value > hi:
        return round(float(value) - hi, 2)
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
        "strike_type": row.get("strike_type"),
        "label": row.get("label"),
        "price": price,
        # Carried for screen_score, not for screening. `price` is the YES ASK,
        # so it cannot say what FADING cost: buying NO sells against the bid.
        # Without the bid the scoring falls back to 1 - ask, which is cheaper
        # than reality and biases the measured edge in this screen's own
        # favour. Volume says whether that fill was ever realistic.
        "yes_bid": row.get("yes_bid"),
        "volume": row.get("volume"),
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
    gap = bracket_gap(row, forecast)
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
    lo, hi = winning_range(row)
    if variable == "low":
        # The settled low can only fall further, so a bracket whose LOWEST
        # winning temperature already sits above the realized minimum is lost.
        # An unbounded-below tail ("76 or below") never is.
        if lo is None or lo <= bound:
            return None
        gap = round(lo - float(bound), 2)
    elif variable == "high":
        if hi is None or hi >= bound:
            return None
        gap = round(float(bound) - hi, 2)
    else:
        return None
    return _candidate(row, "dead", float(bound), gap, price, now_iso)
