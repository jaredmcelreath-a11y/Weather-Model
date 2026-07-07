"""Peak-lock robustness (2026-07-06 regression + no-regression guards).

Two changes, both HIGH-only and both gated so calm single-peak days are unaffected:
- Persistence guard: on a *bumpy* afternoon the blunt 2°F peak-lock needs a second
  confirming reading, so a lone convective dip before a higher peak can't false-lock.
- Plateau lock: past the afternoon gate, a high that has stopped climbing (held near
  its max) locks without waiting for a full retreat, so a flat-topped peak locks while
  the market is still live instead of ~2 h later.

The 2026-07-06 trace: 96.1 @1:53 -> a 2.2°F dip to 93.9 @2:53 (false-locked at 96 under
the old rule) -> recovered -> real peak ~98.1 @4:53, held flat, eased only by 6:53.
"""
from datetime import date, datetime

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 6)


def _series(*hmt):
    """(hour, minute, temp) rows -> (times, temps)."""
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, m, tzinfo=_TZ)
             for h, m, _ in hmt]
    temps = [t for *_, t in hmt]
    return times, temps


def _at(h, m=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, h, m, tzinfo=_TZ)


# Today's bumpy hourly (:53) trace.
_BUMPY = _series((6, 53, 72), (9, 53, 86), (13, 53, 96.1), (14, 53, 93.9),
                 (15, 53, 95.0), (16, 53, 98.1), (17, 53, 98.1), (18, 53, 97.0))


def test_bumpy_dip_does_not_false_lock():
    times, temps = _BUMPY
    # 2:53: the 2.2°F dip clears the 2°F rule, but a bumpy afternoon must wait for a
    # second confirming reading — which never comes (3:53 recovers to -1.1°F).
    assert model._extreme_locked(times, temps, _DAY, "high", _at(14, 53), bumpy=True) is False
    assert model._extreme_locked(times, temps, _DAY, "high", _at(15, 53), bumpy=True) is False


def test_plateau_locks_at_flat_top_not_hours_later():
    times, temps = _BUMPY
    # 4:53 just set a fresh max (could still climb) -> not locked.
    assert model._extreme_locked(times, temps, _DAY, "high", _at(16, 53), bumpy=True) is False
    # 5:53 holds 98.1 (plateaued, past the ~4:47 gate) -> locks now, not at 6:53.
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17, 53), bumpy=True) is True


def test_calm_early_peak_locks_immediately():
    # Calm single peak at 1:53, then a clean >2°F fall -> no persistence delay.
    times, temps = _series((6, 53, 70), (9, 53, 82), (13, 53, 95), (14, 53, 92.5))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(14, 53), bumpy=False) is True


def test_calm_afternoon_peak_early_locks_unchanged():
    # Calm, peak 98 @4:53 then 96.5 @5:53 (retreat 1.5, past gate) -> early-locks as before.
    times, temps = _series((6, 53, 72), (9, 53, 84), (15, 53, 96), (16, 53, 98), (17, 53, 96.5))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17, 53), bumpy=False) is True


def test_still_climbing_never_locks():
    # Current reading is the running max (still rising / sitting on the peak) -> no lock,
    # even past the gate, bumpy or not.
    times, temps = _series((6, 53, 72), (9, 53, 84), (15, 53, 94), (17, 53, 95))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17, 53), bumpy=True) is False


def test_lone_high_spike_trusted_only_when_forecast_supports_it():
    # A lone continuous spike above the corroborated peak is trusted only if the
    # forecast (shifted to the settlement basis) gave its settled bin >= 5%.
    forecast_supports = [96, 97, 97, 98, 98, 99, 99, 100, 100, 101]   # ~20% >= 100
    forecast_tops_99 = [95, 96, 96, 97, 97, 97, 98, 98, 98, 99]       # ~0% >= 105
    # real brief peak the forecast expected -> trusted (raw 100.4)
    assert model._trusted_high_max(100.4, 98.6, forecast_supports, 0.0) == 100.4
    # a glitch far above the forecast -> rejected, falls back to the corroborated peak
    assert model._trusted_high_max(105.0, 98.6, forecast_tops_99, 0.0) == 98.6
    # no lone spike above the corroborated peak -> raw value used as-is
    assert model._trusted_high_max(98.6, 98.6, forecast_tops_99, 0.0) == 98.6
