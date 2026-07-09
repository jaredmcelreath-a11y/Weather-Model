"""Tests for the Kalshi CLI settlement basis (Part A): CLI truth fetch parsing,
the calibrated settlement offset, and the model's settle_offset shift."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE
from sources.station_history import _parse_daily

_TZ = ZoneInfo(TIMEZONE)

SAMPLE_CSV = (
    "station,day,max_temp_f,min_temp_f,precip_in\n"
    "DFW,2026-06-08,95.0,78.0,0.0\n"
    "DFW,2026-06-09,None,77.0,0.0\n"      # missing max -> skipped
    "DFW,2026-06-10,94.0,M,0.0\n"          # missing min -> skipped
    "DFW,2026-06-11,93.0,79.0,0.0\n"
)


def test_parse_daily_maps_day_to_high_low():
    out = _parse_daily(SAMPLE_CSV)
    assert out[date(2026, 6, 8)] == (95.0, 78.0)
    assert out[date(2026, 6, 11)] == (93.0, 79.0)


def test_parse_daily_skips_missing_rows():
    out = _parse_daily(SAMPLE_CSV)
    assert date(2026, 6, 9) not in out   # None max
    assert date(2026, 6, 10) not in out  # M min
    assert len(out) == 2


from calibration import _settlement_offset


def test_settlement_offset_means_the_cli_minus_hourly_gap():
    cli = {date(2026, 6, 8): (95.0, 78.0), date(2026, 6, 9): (94.0, 77.0)}
    hourly = {date(2026, 6, 8): (94.0, 78.0), date(2026, 6, 9): (93.0, 79.0)}
    off = _settlement_offset(cli, hourly)
    assert off["high"] == 1.0    # (1 + 1) / 2
    assert off["low"] == -1.0    # (0 + -2) / 2
    assert off["n_days"] == 2
    assert off["high_std"] == 0.0   # high gaps [1, 1] -> std 0
    assert off["low_std"] == 1.0    # low gaps [0, -2] -> std 1.0


def test_settlement_offset_zero_when_no_overlap():
    off = _settlement_offset({date(2026, 6, 8): (95.0, 78.0)}, {})
    assert off == {"high": 0.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0, "n_days": 0}


def _member(day, peak):
    """A synthetic 24-hour member peaking at `peak` at 15:00 local."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]  # max=peak, min=peak-15
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


def test_settle_offset_shifts_consensus_and_distribution():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    plus = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0})
    assert base["consensus"] == 91.0
    assert plus["consensus"] == 92.0
    # Constant shift must not change the spread, only the location.
    assert plus["sigma_used"] == base["sigma_used"]
    assert (model.prob_at_least(plus["probabilities"], 92)
            > model.prob_at_least(base["probabilities"], 92))


def test_zero_offset_is_identical_to_none_robinhood_guard():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    zero = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 0.0, "low": 0.0})
    assert base == zero


def test_predict_from_threads_offset():
    day = date(2030, 7, 1)
    pf = model._predict_from(_series(day), {"obs": ([], [])}, day, None, None,
                             {"high": 1.0, "low": 0.0})
    assert pf["high"]["consensus"] == 92.0
    # low offset is 0.0 -> low consensus stays at the unshifted value (peak-15).
    base_low = model.predict_variable(_series(day), {"obs": ([], [])}, day,
                                      "low", None, None)
    assert pf["low"]["consensus"] == base_low["consensus"]


import json

import forecast_log


def _snap():
    return {
        "updated": "2026-06-20T15:00:00",
        "today": {
            "day": "2026-06-20",
            "high": {"consensus": 91, "probabilities": {"90": 0.5, "91": 0.5}},
            "low": {"consensus": 75, "probabilities": {"74": 0.5, "75": 0.5}},
        },
        "tomorrow": {
            "day": "2026-06-21",
            "high": {"consensus": 93, "probabilities": {"92": 0.5, "93": 0.5}},
            "low": {"consensus": 77, "probabilities": {"76": 0.5, "77": 0.5}},
        },
    }


def test_hourly_and_cli_records_coexist(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snap(), path=p, basis="hourly")
    forecast_log.record(_snap(), path=p, basis="cli")
    rows = forecast_log.load(p)
    assert {r["basis"] for r in rows} == {"hourly", "cli"}
    assert len([r for r in rows if r["basis"] == "hourly"]) == 4
    assert len([r for r in rows if r["basis"] == "cli"]) == 4


def test_legacy_untagged_record_treated_as_hourly(tmp_path):
    p = str(tmp_path / "log.jsonl")
    legacy = {"target_date": "2026-06-20", "variable": "high", "lead_bucket": 0,
              "captured_at": "x", "consensus": 91, "probabilities": {"91": 1.0}}
    with open(p, "w") as fh:
        fh.write(json.dumps(legacy) + "\n")
    forecast_log.record(_snap(), path=p, basis="hourly")
    rows = forecast_log.load(p)
    match = [r for r in rows
             if r["target_date"] == "2026-06-20" and r["variable"] == "high"
             and r["lead_bucket"] == 0 and r.get("basis", "hourly") == "hourly"]
    assert len(match) == 1


import scoring
from sources import station_history


def test_score_filters_by_basis_and_uses_matching_truth(monkeypatch):
    recs = [
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "hourly", "consensus": 90, "probabilities": {"90": 1.0}},
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "cli", "consensus": 91, "probabilities": {"91": 1.0}},
    ]
    monkeypatch.setattr(scoring.forecast_log, "load", lambda path=None: recs)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 10): (90.0, 70.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {date(2026, 6, 10): (91.0, 70.0)})
    today = date(2026, 6, 11)
    h = scoring.score(today=today, basis="hourly")
    c = scoring.score(today=today, basis="cli")
    assert h["n_settled"] == 1 and c["n_settled"] == 1
    assert h["by_variable"]["high"]["brier"] == 0.0
    assert c["by_variable"]["high"]["brier"] == 0.0


import backtest
import calibration
from sources import open_meteo_models


def test_backtest_cli_uses_cli_truth_and_applies_offset(monkeypatch):
    day = date(2026, 6, 10)
    series = {"det_a": _member(day, 90.0)}  # daily high 90, low 75
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: series)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {day: (91.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    hourly = backtest.run()
    cli_off = backtest.run(cli=True, settle_offset={"high": 1.0, "low": 0.0})
    cli_no = backtest.run(cli=True)

    assert hourly["high"]["mae"] == 0.0     # consensus 90 vs hourly 90
    assert cli_off["high"]["mae"] == 0.0    # consensus 90+1=91 vs cli 91
    assert cli_no["high"]["mae"] == 1.0     # consensus 90 vs cli 91 -> off by 1


def test_scheduled_log_records_cli_only(monkeypatch):
    """The live site is Kalshi/CLI-only, so the scheduler logs the CLI snapshot
    and no longer grows an hourly (Robinhood) cohort."""
    import scheduled_log
    import model

    monkeypatch.setattr(calibration, "get",
                        lambda refresh=True: {"settlement_offset": {"high": 1.0, "low": 0.0}})
    monkeypatch.setattr(model, "snapshot",
                        lambda calib, settle_offset=None, continuous_obs=False:
                        {"_off": settle_offset})
    calls = []
    monkeypatch.setattr(scheduled_log.forecast_log, "record",
                        lambda snap, basis="hourly": calls.append((snap.get("_off"), basis)))
    monkeypatch.setattr(scheduled_log.forecast_log, "load", lambda path=None: [])

    scheduled_log.main()

    # only the offset/CLI snapshot is logged; no hourly basis row
    assert ({"high": 1.0, "low": 0.0}, "cli") in calls
    assert all(basis == "cli" for _off, basis in calls)


def test_settle_offset_std_widens_sigma_without_moving_center():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0})
    wide = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0, "high_std": 2.0, "low_std": 0.0})
    assert wide["consensus"] == base["consensus"]    # center unchanged
    assert wide["sigma_used"] > base["sigma_used"]   # gap std widened sigma


def _obs(day, temps, continuous):
    """Hourly obs from `temps` (one per hour from midnight), optionally with a
    finer continuous feed mirroring them (so observed_cont is set)."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    ot = [base + timedelta(hours=h) for h in range(len(temps))]
    obs = {"obs": (ot, temps)}
    if continuous:
        ct = [base + timedelta(minutes=15 * k) for k in range(len(temps) * 4)]
        cv = [temps[k // 4] for k in range(len(temps) * 4)]
        obs["obs_continuous"] = (ct, cv)
    return obs


# Gap-std widening is the average CLI-vs-hourly spread we add when we can't see
# today's gap. But a LOCKED low whose continuous extreme is already observed has
# its gap measured — widening there double-hedges and smears the settled low
# across the rounding boundary (flagging fake edges vs Kalshi). Skip it there.
_LOCKED_LOW = [79 + abs(h - 5) for h in range(17)]   # dip 79 @05:00, risen to 90 @16:00
_LOCKED_HIGH = [95 - abs(h - 13) for h in range(18)]  # peak 95 @13:00, fallen to 91 @17:00
_LOW_OFF = {"low": -0.3, "low_std": 0.46, "high": 0.0, "high_std": 0.0}


def test_locked_low_anchors_on_continuous_and_skips_widening():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)

    with_cont = model.predict_variable(series, _obs(day, _LOCKED_LOW, True),
                                       day, "low", now, None, _LOW_OFF)
    no_cont = model.predict_variable(series, _obs(day, _LOCKED_LOW, False),
                                     day, "low", now, None, _LOW_OFF)
    no_offset = model.predict_variable(series, _obs(day, _LOCKED_LOW, True),
                                       day, "low", now, None, None)

    assert with_cont["peak_locked"] and no_cont["peak_locked"]
    # Continuous mirrors hourly here -> the measured CLI gap is 0, so the anchored
    # center equals the no-offset (hourly) center, NOT the average-offset center.
    assert with_cont["consensus"] == no_offset["consensus"]
    assert with_cont["consensus"] != no_cont["consensus"]
    # Gap observed -> no widening -> tighter sigma than the unknown-gap path.
    assert with_cont["sigma_used"] < no_cont["sigma_used"]


def test_unlocked_low_still_widens_with_continuous():
    # Before the low locks, the gap is still unknown -> widening must still apply.
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 4, tzinfo=_TZ)
    temps = [84, 83, 82, 81, 80]   # still descending at 04:00 -> not locked
    wide = model.predict_variable(series, _obs(day, temps, True), day, "low",
                                  now, None, _LOW_OFF)
    narrow = model.predict_variable(series, _obs(day, temps, True), day, "low",
                                    now, None, {**_LOW_OFF, "low_std": 0.0})
    assert not wide["peak_locked"]
    assert wide["sigma_used"] > narrow["sigma_used"]


def test_locked_high_anchors_on_continuous_and_skips_widening():
    # Extended to the high: a locked high with the continuous peak observed anchors
    # on the measured value and drops the average offset + gap-std widening.
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 17, tzinfo=_TZ)
    off = {"high": 1.0, "high_std": 2.0, "low": 0.0, "low_std": 0.0}
    with_cont = model.predict_variable(series, _obs(day, _LOCKED_HIGH, True), day,
                                       "high", now, None, off)
    no_cont = model.predict_variable(series, _obs(day, _LOCKED_HIGH, False), day,
                                     "high", now, None, off)
    assert with_cont["peak_locked"] and no_cont["peak_locked"]
    # Anchored (gap 0, no widening) vs the average +1.0 offset + gap-std widening
    # when the continuous feed is absent.
    assert with_cont["sigma_used"] < no_cont["sigma_used"]
    assert with_cont["consensus"] < no_cont["consensus"]


def test_locked_high_no_mass_above_realized_peak():
    # The user-reported bug generalized: a locked high must not put real mass
    # above the peak it already realized (temps don't re-climb after the peak).
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 17, tzinfo=_TZ)
    off = {"high": 1.0, "high_std": 2.0, "low": 0.0, "low_std": 0.0}
    hi = model.predict_variable(series, _obs(day, _LOCKED_HIGH, True), day,
                                "high", now, None, off)   # peak realized at 95
    # 2°F above the realized peak collapses from ~34% (old offset+widening) to the
    # sigma-floor tail; 3°F above is effectively impossible.
    assert model.prob_at_least(hi["probabilities"], 97) < 0.05
    assert model.prob_at_least(hi["probabilities"], 98) < 0.005


def test_settle_offset_zero_std_matches_no_std():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    a = model.predict_variable(series, obs, day, "high", None, None,
                               {"high": 1.0, "low": 0.0})
    b = model.predict_variable(series, obs, day, "high", None, None,
                               {"high": 1.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0})
    assert a == b


def test_backtest_cli_std_widens_distribution(monkeypatch):
    day = date(2026, 6, 10)
    series = {"det_a": _member(day, 90.0)}
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: series)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {day: (91.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    narrow = backtest.run(cli=True, settle_offset={"high": 1.0, "low": 0.0})
    wide = backtest.run(cli=True,
                        settle_offset={"high": 1.0, "low": 0.0,
                                       "high_std": 3.0, "low_std": 0.0})
    # consensus is centered on the actual (91), so a wider sigma -> higher CRPS
    assert wide["high"]["crps"] > narrow["high"]["crps"]


def _top(probs):
    """Integer degree of the highest-probability bin."""
    return model.bin_temp(max(probs, key=probs.get))


# A morning low that is SET but not yet locked: min 79 @03:00, ticked to 80
# @04:00 (rise 1°F < 2°F fallback, and pre-sunrise so the early-lock gate is off).
_NL_TEMPS = [82, 81, 80, 79, 80]
_NL_NOW_H = 4
_ZERO_OFF = {"low": 0.0, "low_std": 0.0, "high": 0.0, "high_std": 0.0}


def test_live_low_anchors_on_daily_summary_cli_min():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False)
    obs_cli["cli_daily"] = {day: (95.0, 78.0)}      # CLI min 78 = 1°F below hourly 79
    anchored = model.predict_variable(series, obs_cli, day, "low", now, None,
                                      _ZERO_OFF, live=True)
    assert not base["peak_locked"] and not anchored["peak_locked"]
    # Measured gap (78 - 79 = -1) replaces the zero average offset -> center -1°F.
    assert anchored["consensus"] == round(base["consensus"] - 1.0, 1)
    # Constant shift: spread unchanged, top bin drops one degree.
    assert anchored["sigma_used"] == base["sigma_used"]
    assert _top(anchored["probabilities"]) == _top(base["probabilities"]) - 1


def test_live_low_ignores_daily_summary_when_warmer_or_too_cold():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=True)
    warm = _obs(day, _NL_TEMPS, False); warm["cli_daily"] = {day: (95.0, 80.0)}
    cold = _obs(day, _NL_TEMPS, False); cold["cli_daily"] = {day: (95.0, 74.0)}
    # Warmer-than-hourly (gap >= 0) ignored; implausibly cold (gap < -3) clamped out.
    assert model.predict_variable(series, warm, day, "low", now, None,
                                  _ZERO_OFF, live=True)["consensus"] == base["consensus"]
    assert model.predict_variable(series, cold, day, "low", now, None,
                                  _ZERO_OFF, live=True)["consensus"] == base["consensus"]


def test_backtest_low_ignores_daily_summary_when_not_live():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=False)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    replay = model.predict_variable(series, obs_cli, day, "low", now, None,
                                    _ZERO_OFF, live=False)
    assert replay["consensus"] == base["consensus"]   # not-locked anchor is live-only


def test_locked_low_prefers_daily_summary_over_5min_feed():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    obs = _obs(day, _LOCKED_LOW, True)               # 5-min feed mirrors hourly low 79
    only_cont = model.predict_variable(series, obs, day, "low", now, None,
                                       _LOW_OFF, live=True)
    obs_cli = _obs(day, _LOCKED_LOW, True); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    with_cli = model.predict_variable(series, obs_cli, day, "low", now, None,
                                      _LOW_OFF, live=True)
    assert only_cont["peak_locked"] and with_cli["peak_locked"]
    # Anchors on the whole-°F 78, not the 5-min 79 -> ~1°F colder center.
    assert with_cli["consensus"] == round(only_cont["consensus"] - 1.0, 1)


def test_locked_low_daily_summary_gap_zero_no_average_offset():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    obs = _obs(day, _LOCKED_LOW, True); obs["cli_daily"] = {day: (95.0, 79.0)}
    with_cli = model.predict_variable(series, obs, day, "low", now, None,
                                      _LOW_OFF, live=True)
    no_offset = model.predict_variable(series, _obs(day, _LOCKED_LOW, True), day,
                                       "low", now, None, None, live=True)
    # Measured gap 0 -> anchored on the hourly low, NOT the -0.3 average offset.
    assert with_cli["consensus"] == no_offset["consensus"]


def test_locked_low_rejects_out_of_range_gap_falls_back_to_average_offset():
    """The locked-low branch clamps the measured gap to [-MAX_CLI_GAP, 0]. A gap
    outside that range (here cli_daily min 74 vs hourly 79 -> gap -5, beyond
    -MAX_CLI_GAP=-3) must NOT anchor on the out-of-range value -- it should fall
    back to the flat average offset, exactly as if no cli_daily/continuous were
    present at all."""
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    obs = _obs(day, _LOCKED_LOW, True)
    obs["cli_daily"] = {day: (95.0, 74.0)}   # gap = 74 - 79 = -5 < -MAX_CLI_GAP
    out_of_range = model.predict_variable(series, obs, day, "low", now, None,
                                          _LOW_OFF, live=True)
    fallback = model.predict_variable(series, _obs(day, _LOCKED_LOW, False), day,
                                      "low", now, None, _LOW_OFF, live=True)
    assert out_of_range["peak_locked"] and fallback["peak_locked"]
    assert out_of_range["consensus"] == fallback["consensus"]


def test_high_ignores_daily_summary_min():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    off = {"high": 0.0, "high_std": 0.0, "low": 0.0, "low_std": 0.0}
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "high", now, None, off, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    assert model.predict_variable(series, obs_cli, day, "high", now, None,
                                  off, live=True)["consensus"] == base["consensus"]


def test_robinhood_low_ignores_daily_summary():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, None, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    assert model.predict_variable(series, obs_cli, day, "low", now, None,
                                  None, live=True) == base


def test_fetch_cli_daily_returns_summary(monkeypatch):
    day = date(2026, 7, 3)
    monkeypatch.setattr(model, "fetch_actual_cli",
                        lambda s, e, ttl=None: {day: (83.0, 78.0)})
    assert model._fetch_cli_daily(day) == {day: (83.0, 78.0)}


def test_fetch_cli_daily_swallows_errors(monkeypatch):
    def boom(s, e, ttl=None):
        raise RuntimeError("network down")
    monkeypatch.setattr(model, "fetch_actual_cli", boom)
    assert model._fetch_cli_daily(date(2026, 7, 3)) == {}


def test_fetch_cli_daily_uses_live_ttl(monkeypatch):
    """Today's daily-summary tightens through the morning, so the live fetch
    must not use the 7-day archive cache -- it must drive the short live TTL."""
    from config import CACHE_TTL_SECONDS
    day = date(2026, 7, 3)
    captured = {}

    def fake(s, e, ttl=None):
        captured["ttl"] = ttl
        return {day: (83.0, 78.0)}

    monkeypatch.setattr(model, "fetch_actual_cli", fake)
    assert model._fetch_cli_daily(day) == {day: (83.0, 78.0)}
    assert captured["ttl"] == CACHE_TTL_SECONDS


def test_fetch_actual_cli_forwards_live_ttl_to_get_text(monkeypatch):
    """At the get_text seam: a live caller passing ttl=CACHE_TTL_SECONDS must
    reach get_text with that short ttl, so today's fetch isn't served from the
    7-day archive cache."""
    from config import CACHE_TTL_SECONDS
    from sources import station_history

    captured = {}

    def fake_get_text(url, params=None, **kwargs):
        captured["kwargs"] = kwargs
        return SAMPLE_CSV

    monkeypatch.setattr(station_history, "get_text", fake_get_text)
    day = date(2026, 6, 8)
    station_history.fetch_actual_cli(day, day, ttl=CACHE_TTL_SECONDS)
    assert captured["kwargs"].get("ttl") == CACHE_TTL_SECONDS


def test_fetch_actual_cli_default_omits_ttl_for_archive_callers(monkeypatch):
    """calibration/backtest callers fetch immutable PAST days and pass no ttl --
    they must keep get_text's own 7-day archive default, not a short live ttl."""
    from sources import station_history

    captured = {}

    def fake_get_text(url, params=None, **kwargs):
        captured["kwargs"] = kwargs
        return SAMPLE_CSV

    monkeypatch.setattr(station_history, "get_text", fake_get_text)
    day = date(2026, 6, 8)
    station_history.fetch_actual_cli(day, day)
    assert "ttl" not in captured["kwargs"]


# --- Display of the continuous sub-hourly extreme (2026-07-09) -----------------
# The low's spike-guard (min_support=2) drops a lone dip so it can't wrongly anchor
# the settlement. But the DISPLAYED "observed continuous" number should still show
# the dip the feed actually touched (mirroring the high, which shows its spike) —
# otherwise the shown number contradicts the consensus. Display-only: the behavior
# value (`observed_continuous`, which drives the bound/anchor) stays the guarded one.

def _obs_lone_low_dip(day):
    """Morning obs sitting at 82°F with a single 15-min continuous dip to 80°F
    (lone: no neighbor within 0.7°F, so the ≥2-corroboration guard rejects it)."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    hourly = [86, 84, 83, 82, 82, 82, 82, 82, 82]     # hours 0..8, hourly min 82
    ot = [base + timedelta(hours=h) for h in range(len(hourly))]
    ct, cv = [], []
    dip_at = base + timedelta(hours=6, minutes=15)
    for k in range(len(hourly) * 4):                   # every 15 min through 08:45
        t = base + timedelta(minutes=15 * k)
        val = hourly[min(k // 4, len(hourly) - 1)]
        cv.append(80.0 if t == dip_at else val)
        ct.append(t)
    return {"obs": (ot, hourly), "obs_continuous": (ct, cv)}


def test_low_display_shows_lone_dip_while_behavior_uses_guarded_min():
    day = date(2030, 7, 1)
    now = datetime(day.year, day.month, day.day, 8, tzinfo=_TZ)
    r = model.predict_variable(_series(day), _obs_lone_low_dip(day), day, "low", now, None)
    # Behavior anchors on the corroborated (guarded) min — the lone 80 is rejected.
    assert r["observed_continuous"] == 82.0
    # Display shows the sub-hourly dip the feed actually touched.
    assert r["observed_continuous_display"] == 80.0


def test_high_display_matches_behavior_when_no_lone_spike():
    # No lone spike (continuous mirrors hourly) -> display == behavior for the high.
    day = date(2030, 7, 1)
    now = datetime(day.year, day.month, day.day, 17, tzinfo=_TZ)
    off = {"high": 1.0, "high_std": 2.0, "low": 0.0, "low_std": 0.0}
    r = model.predict_variable(_series(day), _obs(day, _LOCKED_HIGH, True), day, "high", now, None, off)
    assert r["observed_continuous_display"] == r["observed_continuous"]
