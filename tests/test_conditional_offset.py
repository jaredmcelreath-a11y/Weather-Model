"""Two-bucket conditional settlement offset helpers — bucket gating and fallback."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import backtest
import calibration
import model
from calibration import _conditional_settlement_offset
from config import TIMEZONE
from sources import open_meteo_models, station_history

_TZ = ZoneInfo(TIMEZONE)


def _days(n, start=date(2026, 5, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def test_emits_buckets_when_low_gap_differs_and_enough_nights():
    # 12 clear/calm nights with low gap -0.8, 12 other nights with low gap -0.2.
    days = _days(24)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 12
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


def test_unsplit_variable_keeps_flat_gap_std():
    # Low splits by condition (so the bucketed offset is emitted), but the high
    # gap has real spread that does NOT correlate with the bucket, so the high
    # split fails. The unsplit high must still carry the flat std of its gap —
    # zeroing it would make the model overconfident on the CLI high basis.
    days = _days(24)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 12
        low_gap = -0.8 if clear else -0.2
        high_gap = 0.5 if i % 2 == 0 else 1.5   # mean 1.0 in both buckets, std 0.5
        hourly[d] = (90.0, 70.0)
        cli[d] = (90.0 + high_gap, 70.0 + low_gap)
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    off = _conditional_settlement_offset(cli, hourly, cond)
    assert off is not None
    assert off["high"]["clear_calm"] == off["high"]["other"] == 1.0
    # the genuine spread of the high gap (std 0.5) survives in both buckets
    assert off["high"]["clear_calm_std"] == 0.5
    assert off["high"]["other_std"] == 0.5


def test_thin_quantized_low_split_falls_back_to_flat():
    # Reproduces 2026-07-02: 7 clear/calm nights (5x -1, 2x 0) and 38 other
    # nights (10x -1, 28x 0). The -0.75 clear_calm mean is overfit to rounding
    # noise on a tiny sample, so the split must NOT be emitted -> flat fallback.
    cc_lows = [-1.0] * 5 + [0.0] * 2                 # 7 clear/calm
    ot_lows = [-1.0] * 10 + [0.0] * 28               # 38 other
    cli, hourly, cond = {}, {}, {}
    day = date(2026, 5, 1)
    for gap in cc_lows:
        hourly[day] = (90.0, 70.0)
        cli[day] = (91.0, 70.0 + gap)                # high gap +1 (flat)
        cond[day] = (10.0, 5.0)                       # clear + calm
        day += timedelta(days=1)
    for gap in ot_lows:
        hourly[day] = (90.0, 70.0)
        cli[day] = (91.0, 70.0 + gap)
        cond[day] = (80.0, 20.0)                      # cloudy + windy
        day += timedelta(days=1)
    # Split rejected on the count floor (7 < 12) -> None -> caller uses flat.
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_too_few_clear_calm_nights():
    days = _days(10)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 3                          # only 3 clear/calm (< 12)
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.8 if clear else -0.2))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_buckets_too_similar():
    # plenty of clear/calm nights but the gap barely differs -> no value in split
    days = _days(24)
    cli, hourly, cond = {}, {}, {}
    for i, d in enumerate(days):
        clear = i < 12
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + (-0.45 if clear else -0.40))
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


def test_returns_none_when_split_fails_margin_gate():
    # count floor passes (12 nights/bucket, means differ by 0.6) but the huge
    # within-bucket noise both fails the MAR-margin gate and inflates SE_diff so
    # the significance test also rejects the split. Low gaps alternate +-10 around
    # the bucket mean; cc mean +0.3, ot mean -0.3 -> flat 0.0, resid_flat ==
    # resid_cond, and 2*SE_diff >> 0.6.
    days = _days(24)
    cli, hourly, cond = {}, {}, {}
    cc_gaps = [10.3, -9.7] * 6          # mean +0.3
    ot_gaps = [9.7, -10.3] * 6          # mean -0.3
    for i, d in enumerate(days):
        clear = i < 12
        gap = cc_gaps[i] if clear else ot_gaps[i - 12]
        hourly[d] = (90.0, 70.0)
        cli[d] = (91.0, 70.0 + gap)
        cond[d] = (10.0, 5.0) if clear else (80.0, 20.0)
    assert _conditional_settlement_offset(cli, hourly, cond) is None


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
    day = date(2030, 1, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (10.0, 5.0))           # clear + calm
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    # unshifted low consensus is peak-15 -> mean(75,77)=76; clear/calm shift -0.8
    assert out["consensus"] == 75.2


def test_model_picks_other_bucket(monkeypatch):
    day = date(2030, 1, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (90.0, 25.0))          # cloudy + windy
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # 76 - 0.2


def test_model_other_bucket_when_conditions_unavailable(monkeypatch):
    day = date(2030, 1, 1)
    def boom(d):
        raise RuntimeError("no network")
    monkeypatch.setattr(model.open_meteo_models, "night_conditions", boom)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, {}, _BUCKETED)
    assert out["consensus"] == 75.8                       # falls back to 'other'


def test_backtest_applies_bucketed_offset_per_day(monkeypatch):
    d_clear = date(2026, 1, 10)
    d_cloud = date(2026, 1, 11)
    # one series spanning both days
    base = {}
    for d in (d_clear, d_cloud):
        t, v = _member(d, 90.0)
        base.setdefault("det_a", ([], []))
        base["det_a"] = (base["det_a"][0] + t, base["det_a"][1] + v)
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e, **kw: base)
    monkeypatch.setattr(open_meteo_models, "historical_night_conditions",
                        lambda s, e: {d_clear: (10.0, 5.0), d_cloud: (90.0, 25.0)})
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {d_clear: (90.0, 75.0), d_cloud: (90.0, 75.0)})
    # CLI low truth equals the bucketed shift applied to the hourly low (75):
    #   clear/calm -> 75-0.8=74.2 ; other -> 75-0.2=74.8
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {d_clear: (91.0, 74.2), d_cloud: (91.0, 74.8)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    off = {"high": {"clear_calm": 1.0, "other": 1.0,
                    "clear_calm_std": 0.0, "other_std": 0.0},
           "low": {"clear_calm": -0.8, "other": -0.2,
                   "clear_calm_std": 0.0, "other_std": 0.0}}
    res = backtest.run(cli=True, settle_offset=off)
    assert res["low"]["mae"] == 0.0          # each day's shift matches its CLI low
