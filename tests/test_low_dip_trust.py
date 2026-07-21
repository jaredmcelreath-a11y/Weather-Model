"""The low's lone sub-hourly cold dip.

Kalshi settles the daily LOW on the raw NWS daily minimum, so a brief post-sunrise
5-minute dip settles the market even when it stands alone — exactly as a lone hot
spike settles the daily max. The model's low continuous feed used
`min_support=2` (reject lone dips) to block convective cold blips, which structurally
ignored the settlement-moving dawn dip (2026-07-21 KDFW: a single 07:10 reading of
26.0C=78.8F dropped the settled low to the 78-79 bracket while the model held 80).

`_trusted_low_min` mirrors `_trusted_high_max`: trust a lone dip below the
corroborated trough only when the forecast gave that lower bin real probability —
a plausible dawn trough counts, a sensor glitch far below the forecast does not.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


# ---- unit: the trust gate itself ---------------------------------------------

def _fullday_lows(lows):
    """A fullday-sample stand-in: pure-forecast members whose only relevant
    feature to the gate is their value (the gate bins each sample)."""
    return list(lows)


def test_trusted_low_min_no_lone_dip_returns_raw():
    # c_raw == c_robust: the corroborated trough is the dip, nothing to gate.
    assert model._trusted_low_min(80.0, 80.0, _fullday_lows([80, 81]), 0.0) == 80.0


def test_trusted_low_min_trusts_plausible_lone_dip():
    # Lone dip to 78 below the corroborated 80; the forecast puts real mass at 78.
    lows = [78, 79, 80, 81]           # 1/4 = 25% of members <= 78 bin, >= 5%
    assert model._trusted_low_min(78.0, 80.0, lows, 0.0) == 78.0


def test_trusted_low_min_rejects_implausible_glitch():
    # Lone dip to 68, far below anything the forecast contemplated -> sensor glitch.
    lows = [79, 80, 81, 82]           # 0% of members near the 68 bin
    assert model._trusted_low_min(68.0, 80.0, lows, 0.0) == 80.0


def test_trusted_low_min_none_and_empty_fallbacks():
    assert model._trusted_low_min(None, 80.0, [79, 80], 0.0) == 80.0
    assert model._trusted_low_min(78.0, 80.0, [], 0.0) == 80.0   # no forecast -> robust


def test_trusted_low_min_gate_uses_the_settlement_shift():
    # A -3F shift drags the forecast lows down into the dip bin, making an otherwise
    # unsupported dip plausible on the settlement basis (mirrors the high's shift arg).
    lows = [81, 82, 83, 84]
    assert model._trusted_low_min(78.0, 80.0, lows, 0.0) == 80.0   # unshifted: no support
    assert model._trusted_low_min(78.0, 80.0, lows, -3.0) == 78.0  # shifted: 81-3=78 supports


# ---- integration: the dip flows into the locked-low consensus ----------------

def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series_lows_near_79(day):
    # Members with morning lows spanning 77..80 so the 78 bin carries >=5% mass.
    # (_member's in-window min is peak-14: the midnight point is trimmed.)
    return {f"m{low}": _member(day, low + 14) for low in (77, 78, 79, 80)}


# Hourly obs: min 80 at 05:00, risen to 91 by 16:00 -> the low is locked.
_HOURLY = [80 + abs(h - 5) for h in range(17)]
_LOW_OFF = {"low": -0.3, "low_std": 0.46, "high": 0.0, "high_std": 0.0}


def _obs_with_dip(day, dip_val):
    """Hourly obs plus a continuous feed that mirrors them, except one LONE
    reading near 07:00 set to `dip_val` (None = no dip, feed mirrors hourly)."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    ot = [base + timedelta(hours=h) for h in range(len(_HOURLY))]
    ct = [base + timedelta(minutes=15 * k) for k in range(len(_HOURLY) * 4)]
    cv = [_HOURLY[k // 4] for k in range(len(_HOURLY) * 4)]
    if dip_val is not None:
        cv[7 * 4] = dip_val          # 07:00, a single 15-min sample -> uncorroborated
    return {"obs": (ot, _HOURLY), "obs_continuous": (ct, cv)}


def _consensus(day, dip_val):
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    out = model.predict_variable(_series_lows_near_79(day), _obs_with_dip(day, dip_val),
                                 day, "low", now, None, _LOW_OFF)
    assert out["peak_locked"]
    return out["consensus"]


def test_plausible_lone_dip_pulls_locked_low_down():
    day = date(2030, 7, 1)
    base = _consensus(day, None)         # no dip: locked on hourly min 80
    dipped = _consensus(day, 78.0)       # plausible lone dip to 78
    assert dipped < base - 0.9           # the settlement-moving dip is trusted


def test_implausible_glitch_does_not_move_locked_low():
    day = date(2030, 7, 1)
    base = _consensus(day, None)
    glitch = _consensus(day, 60.0)       # absurd lone reading -> rejected
    assert abs(glitch - base) < 1e-6
