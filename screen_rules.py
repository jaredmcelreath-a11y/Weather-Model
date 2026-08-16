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

import math

import scan_log

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

# Same-day error by VARIABLE, from the same measurement. The flat 4F threshold
# is variable-blind as well as lead-blind: 4F is ~5.7 sigma of evidence against
# a high but only ~2.4 against a low, because the low forecast is that much
# noisier. Without this factor a low reading 1.0x looked as convincing as a high
# reading 1.0x while being less than half as strong -- the column invited a
# comparison it could not support.
#
# Used only as a RATIO against the reference below, never as an absolute per-city
# sigma. The anchor is arbitrary but explicit: a same-day HIGH still reads
# exactly 1.0x at MIN_CANDIDATE_GAP_F, so the original scale does not move.
VARIABLE_SIGMA = {"high": 0.70, "low": 1.70}
_REFERENCE_VARIABLE = "high"

# What 1.0x means in sigma, identically for every variable and lead. Anything
# quoted in multiples of the bar can be converted with this one constant.
SIGMAS_AT_BAR = MIN_CANDIDATE_GAP_F / VARIABLE_SIGMA[_REFERENCE_VARIABLE]

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
    """The gap this row needs to carry SIGMAS_AT_BAR sigma of evidence.

    Two corrections to the flat threshold, both pure ratios: the variable's own
    baseline noise, and how that noise grows with lead."""
    baseline = VARIABLE_SIGMA.get(variable)
    factor = 1.0 if baseline is None else (
        baseline / VARIABLE_SIGMA[_REFERENCE_VARIABLE])
    return MIN_CANDIDATE_GAP_F * factor * lead_multiplier(hours_to_close,
                                                          variable)


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


# What the Screen page and the alert loop both consider actionable RIGHT NOW.
# Below the floor the market has already resolved the bracket against the fade;
# above the cap it agrees with the fade and there is nothing left to win. Both
# consumers read these from here: two copies of a band silently drift.
MIN_LIVE_NO_PRICE = 0.20
MAX_LIVE_NO_PRICE = 0.90


def no_ask_of(market: dict):
    """Dollars to BUY NO on this market right now, or None when unquoted.

    Kalshi's own NO ask when there is one, else the YES bid inverted: buying NO
    sells against the resting YES bid, so NO ask = 1 - yes bid. Prices arrive as
    dollar STRINGS ("0.8800"), the gotcha that silently empties a scan pass."""
    ask = scan_log.dollars(market.get("no_ask_dollars"))
    if ask is not None:
        return ask
    bid = scan_log.dollars(market.get("yes_bid_dollars"))
    return None if bid is None else round(1.0 - bid, 2)


def within_band(price) -> bool:
    """Whether a live NO price is worth showing or pushing.

    An unquoted row (None) SURVIVES: an absent quote is thin liquidity or a
    market that has since closed, not evidence about the fade."""
    if price is None:
        return True
    return MIN_LIVE_NO_PRICE <= float(price) <= MAX_LIVE_NO_PRICE


# The mirror band, for the side that BUYS rather than fades. Above the cap the
# market already agrees and under 11% is left to win; below the floor the market
# is saying the screen's REFERENCE is wrong, not that the price is -- a
# supposedly locked bracket at 5c is far likelier to mean a bad station reading
# than free money, and that is exactly when this screen must not shout.
#
# One band, applied at firing (screen.py), on the live loop (screen_alert) and
# at page load (screen_view), so a row cannot be logged under a standard the
# page then disagrees with.
MIN_LIVE_YES_PRICE = 0.20
MAX_LIVE_YES_PRICE = 0.90


def yes_ask_of(market: dict):
    """Dollars to BUY YES on this market right now, or None when unquoted.

    Kalshi's own YES ask when there is one, else the NO bid inverted: buying YES
    sells against the resting NO bid, so YES ask = 1 - no bid. Prices arrive as
    dollar STRINGS ("0.3900"), the gotcha that silently empties a scan pass."""
    ask = scan_log.dollars(market.get("yes_ask_dollars"))
    if ask is not None:
        return ask
    bid = scan_log.dollars(market.get("no_bid_dollars"))
    return None if bid is None else round(1.0 - bid, 2)


def within_yes_band(price) -> bool:
    """Whether a live YES price is worth showing or pushing.

    An unquoted row (None) SURVIVES, as on the fade side: an absent quote is
    thin liquidity or a market that has since closed, not evidence."""
    if price is None:
        return True
    return MIN_LIVE_YES_PRICE <= float(price) <= MAX_LIVE_YES_PRICE


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


# What an observation actually pins down about the number that SETTLES.
#
# Kalshi settles on the NWS climate report, which states whole degrees F, while
# observations arrive in Celsius -- and many stations report only WHOLE Celsius
# (verified 2026-08-06: every KATL reading that day was an integer degC, while
# KNYC reports tenths). A whole-degC reading of 22 means the true temperature is
# somewhere in [21.5, 22.5)C, i.e. 70.7F to just under 72.5F, which the report
# can round to 71 or to 72.
#
# Comparing a raw reading against a strike ignores both effects and manufactures
# certainty the observation does not carry. On 2026-08-06 that called Atlanta's
# "72 to 73" low DEAD off a realized 71.6F -- a bracket Kalshi had at 91% YES,
# because 71.6F rounds to a settled 72, inside the bracket. This screen is the
# one that claims hard evidence, so it has to reason on the settling basis.
_WHOLE_C_TOLERANCE = 0.05       # recovering degC from 1-decimal degF is exact
                                # to ~0.03, so this cannot misread tenths
_WHOLE_C_HALF_STEP_F = 0.9      # half a degC, all a whole-degC report resolves
_TENTHS_C_HALF_STEP_F = 0.1     # a tenths-degC report, rounded into degF


def _reading_slack_f(temp_f: float) -> float:
    """How far the true temperature can sit from this reading, in F.

    A reading that lands exactly on the whole-Celsius grid is treated as a
    whole-Celsius report, which is the conservative reading of it: a station
    sending tenths that happens to hit 22.0 loses nothing but a hair of the
    screen's reach, while assuming precision a whole-degC station does not have
    is what produces a false 'dead'."""
    celsius = (float(temp_f) - 32.0) * 5.0 / 9.0
    on_grid = abs(celsius - round(celsius)) < _WHOLE_C_TOLERANCE
    return _WHOLE_C_HALF_STEP_F if on_grid else _TENTHS_C_HALF_STEP_F


def settled_range(temp_f) -> tuple:
    """The (lowest, highest) whole-degF values the climate report could state
    for this reading, inclusive.

    Half-up rounding over the reading's own uncertainty band. The band is
    half-open at the top -- 72.5F rounds to 73, so a band ending exactly there
    stops at 72 -- which `ceil(x - 0.5)` gets right and `floor(x + 0.5)` does
    not."""
    slack = _reading_slack_f(temp_f)
    return (math.floor(float(temp_f) - slack + 0.5),
            math.ceil(float(temp_f) + slack - 0.5))


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
    entirely BELOW it is dead.

    Compared against the whole-degF values `bound` could SETTLE at, never
    against the raw reading -- see settled_range()."""
    if bound is None:
        return None
    price = _tradeable_price(row)
    if price is None:
        return None
    variable = row.get("variable")
    lo, hi = winning_range(row)
    lowest_settled, highest_settled = settled_range(bound)
    if variable == "low":
        # The settled low can only fall further, so a bracket whose LOWEST
        # winning temperature already sits above every value the realized
        # minimum could settle at is lost. An unbounded-below tail ("76 or
        # below") never is.
        if lo is None or lo <= highest_settled:
            return None
        gap = round(lo - float(bound), 2)
    elif variable == "high":
        if hi is None or hi >= lowest_settled:
            return None
        gap = round(float(bound) - hi, 2)
    else:
        return None
    return _candidate(row, "dead", float(bound), gap, price, now_iso)


# ---- The YES side: brackets the day has already won -----------------------
#
# Not a mirror of dead_candidate, because the physics is not symmetric. A low
# can only FALL, so a low bracket left open UPWARD ("92 or above") never becomes
# certain while the day runs -- an evening downdraft can still crash it. Only a
# tail open in the direction the extreme can still move is ever safe.

def settled_inside(row: dict, bound) -> bool:
    """Whether EVERY whole-degF value this reading could settle at wins.

    On the settled basis, never the raw reading: a 92.4 that could be reported
    as 91 does not "already sit inside" a 92-and-above bracket, however it looks
    on the thermometer."""
    if bound is None:
        return False
    lo, hi = winning_range(row)
    lowest, highest = settled_range(bound)
    if lo is not None and lowest < lo:
        return False
    if hi is not None and highest > hi:
        return False
    return True


def _yes_candidate(row: dict, kind: str, reference: float, margin: float,
                   price, now_iso: str) -> dict:
    """A YES-side candidate. Deliberately NOT the fade shape: `margin` not
    `gap`, `reference` not `forecast`, and an explicit `side`, so a row from
    this screen can never be mistaken for a fade even if the two logs are read
    together."""
    return {
        "ts": now_iso,
        "series": row.get("series"),
        "variable": row.get("variable"),
        "ticker": row.get("ticker"),
        "floor": row.get("floor"),
        "cap": row.get("cap"),
        "strike_type": row.get("strike_type"),
        "label": row.get("label"),
        "side": "YES",
        "price": price,
        "yes_bid": row.get("yes_bid"),
        "volume": row.get("volume"),
        "reference": reference,
        "margin": margin,
        "kind": kind,
        "hours_to_close": row.get("hours_to_close"),
    }


def locked_candidate(row: dict, bound, now_iso: str):
    """A bracket the realized extreme has made impossible to LOSE, or None.

    Fires only on a tail left open in the direction the extreme can still move:
    a LOW bracket unbounded BELOW once the low has reached it, or a HIGH bracket
    unbounded ABOVE. Everything else can still be taken away."""
    if bound is None:
        return None
    price = row.get("yes_ask")
    if not within_yes_band(price):
        return None
    variable = row.get("variable")
    lo, hi = winning_range(row)
    lowest_settled, highest_settled = settled_range(bound)
    if variable == "low":
        # Unbounded BELOW, and every value it could settle at already wins.
        if lo is not None or hi is None or highest_settled > hi:
            return None
        margin = round(hi - highest_settled, 2)
    elif variable == "high":
        if hi is not None or lo is None or lowest_settled < lo:
            return None
        margin = round(lowest_settled - lo, 2)
    else:
        return None
    return _yes_candidate(row, "locked", float(bound), margin, price, now_iso)


def guarded_candidate(row: dict, bound, remaining, now_iso: str):
    """A bracket the realized extreme already wins and the forecast protects.

    Two conditions: every value the realized extreme could settle at already
    wins, AND the forecast still ahead keeps it there by MIN_CANDIDATE_GAP_F.

    Each variable has exactly ONE threatened edge, which is what makes this and
    locked_candidate partition rather than overlap: a low can only fall, so only
    its `lo` can be breached; a high can only rise, so only its `hi` can. An
    unbounded threatened edge means nothing can take the bracket away, which is
    locked_candidate's job, not this one's.

    The bar is the FLAT MIN_CANDIDATE_GAP_F, never required_gap. required_gap
    scales by forecast error at the lead where the extreme FORMS; here it has
    already formed, and the only question left is whether the remaining hours
    can undercut it -- a short-range, convection-dominated risk. required_gap
    would demand 9.7F of a same-day low and silence every case this exists for.
    Read the row's Storm column beside this: it is the risk that breaks it."""
    if bound is None or remaining is None:
        return None
    price = row.get("yes_ask")
    if not within_yes_band(price):
        return None
    if not settled_inside(row, bound):
        return None
    variable = row.get("variable")
    lo, hi = winning_range(row)
    if variable == "low":
        if lo is None:                    # nothing below to breach
            return None
        margin = round(float(remaining) - lo, 2)
    elif variable == "high":
        if hi is None:
            return None
        margin = round(hi - float(remaining), 2)
    else:
        return None
    if margin < MIN_CANDIDATE_GAP_F:
        return None
    return _yes_candidate(row, "guarded", float(bound), margin, price, now_iso)


# ---- Which ladders are still live -----------------------------------------
#
# Not a screening rule and deliberately not shaped like one: it flags no
# mispricing and produces no candidate. It answers the prior question the page
# never answered -- which of the forty ladders has a market at all today, and
# which have already collapsed onto one bracket.

# At or above this the ladder has picked its answer and there is nothing left to
# decide. The mirror of SETTLED_PRICE's logic applied to a whole ladder rather
# than one bracket, and a round number by choice: this is an orientation
# threshold, not a calibrated one.
UNSETTLED_BELOW = 0.90


def leading_bracket(rows: list):
    """The dearest bracket on this ladder and its runner-up, or None.

    Priced off `price_of` -- the YES ask, falling back to the bid -- so the
    number means what Price means everywhere else on the page: what taking that
    side would cost.

    The runner-up is carried as its own label/price pair rather than a nested
    dict, so a consumer reading one field cannot accidentally read the
    leader's. It is None on a one-bracket ladder rather than zero: a zero would
    read as "the market prices the alternative at nothing", which the ladder
    never said.

    Ties break on ticker so the answer is deterministic: a leader that flickered
    between two equally-priced brackets would republish a different document
    every firing from identical inputs."""
    priced = [(price_of(r), r) for r in rows or []]
    priced = [(p, r) for p, r in priced if p is not None]
    if not priced:
        return None
    priced.sort(key=lambda pair: (-pair[0], str(pair[1].get("ticker") or "")))
    price, top = priced[0]
    runner = priced[1] if len(priced) > 1 else None
    return {"ticker": top.get("ticker"), "label": top.get("label"),
            "price": price,
            "next_label": None if runner is None else runner[1].get("label"),
            "next_price": None if runner is None else runner[0]}


def is_unsettled(leader) -> bool:
    """Whether this ladder still has something to decide."""
    price = (leader or {}).get("price")
    return price is not None and float(price) < UNSETTLED_BELOW
