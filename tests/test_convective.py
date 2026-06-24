"""Tests for the convective downside-humility trigger and the model sigma gate.
All synthetic — no live network — mirroring tests/test_accuracy.py.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DAY = date(2026, 6, 16)


def test_convective_config_constants():
    assert config.CONVECTIVE_SIGMA >= 2.0
    assert config.CONVECTIVE_POP_MIN > 0
    assert config.CONVECTIVE_CAPE_MIN > 0
    ugc = set(config.CONVECTIVE_UPSTREAM_UGC)
    assert "TXC497" in ugc  # Wise County — the NW approach


def test_window_max_reduces_to_remaining_hours():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    pop = [float(h) for h in range(24)]          # 0..23, increasing
    cape = [100.0 * h for h in range(24)]         # 0..2300
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, pop, cape, DAY, now)
    assert mp == 23.0 and mc == 2300.0            # max over [18:00, midnight)


def test_window_max_empty_window_is_none():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(5)]   # only 00:00-04:00
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, [1.0] * 5, [1.0] * 5, DAY, now)
    assert mp is None and mc is None
