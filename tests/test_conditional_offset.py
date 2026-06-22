"""Two-bucket conditional settlement offset helpers — bucket gating and fallback."""
from datetime import date

from calibration import _conditional_settlement_offset


def _days(n, start=date(2026, 5, 1)):
    from datetime import timedelta
    return [start + timedelta(days=i) for i in range(n)]


def test_emits_buckets_when_low_gap_differs_and_enough_nights():
    # 8 clear/calm nights with low gap -0.8, 8 other nights with low gap -0.2.
    days = _days(16)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 8
        low_gap = -0.8 if clear else -0.2
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + low_gap)        # high gap +1 both buckets
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    off = _conditional_settlement_offset(cli, hourly, cond)
    assert off is not None
    assert round(off["low"]["clear_calm"], 2) == -0.8
    assert round(off["low"]["other"], 2) == -0.2
    assert "clear_calm_std" in off["low"] and "other_std" in off["low"]
    # high gap is identical in both buckets -> high gate fails -> equal buckets
    assert off["high"]["clear_calm"] == off["high"]["other"] == 1.0


def test_returns_none_when_too_few_clear_calm_nights():
    days = _days(10)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 3                          # only 3 clear/calm (< 5)
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.8 if clear else -0.2))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_buckets_too_similar():
    # plenty of clear/calm nights but the gap barely differs -> no value in split
    days = _days(16)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 8
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.45 if clear else -0.40))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_split_fails_margin_gate():
    # min_nights and min_sep both pass (8 nights/bucket, means differ by 0.6) but
    # huge within-bucket noise means splitting barely reduces the residual, so the
    # MAR-margin gate rejects the split. Low gaps alternate +-10 around the bucket
    # mean; cc mean +0.3, ot mean -0.3 -> flat 0.0, resid_flat == resid_cond.
    days = _days(16)
    cli, hourly, cond = {}, {}, {}
    cc_gaps = [10.3, -9.7] * 4          # mean +0.3
    ot_gaps = [9.7, -10.3] * 4          # mean -0.3
    for i, d in enumerate(days):
        clear = i < 8
        gap = cc_gaps[i] if clear else ot_gaps[i - 8]
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + gap)
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


_BUCKETED = {"high": {"clear_calm": 0.0, "other": 0.0,
                      "clear_calm_std": 0.0, "other_std": 0.0},
             "low": {"clear_calm": -0.8, "other": -0.2,
                     "clear_calm_std": 0.0, "other_std": 0.0}}


def test_model_picks_clear_calm_bucket(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (10.0, 5.0))           # clear + calm
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    # unshifted low consensus is peak-15 -> mean(75,77)=76; clear/calm shift -0.8
    assert out["consensus"] == 75.2


def test_model_picks_other_bucket(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (90.0, 25.0))          # cloudy + windy
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # 76 - 0.2


def test_model_other_bucket_when_conditions_unavailable(monkeypatch):
    day = date(2030, 7, 1)
    def boom(d):
        raise RuntimeError("no network")
    monkeypatch.setattr(model.open_meteo_models, "night_conditions", boom)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # falls back to 'other'
