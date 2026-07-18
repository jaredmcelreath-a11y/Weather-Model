"""Front-guard corroboration + trigger-detail logging.

The guard's members project an ANCHORED post-noon undercut of the locked low —
but the anchoring offset (obs running cool vs. the forecast on a rain-cooled or
cloudy afternoon) can manufacture an undercut no forecast actually shows. Live
2026-07-14..18 that raised the amber "low at risk" flag five days straight while
every low settled exactly on the final consensus.

Split the two roles: members keep shaping the consensus with their anchored
projections (unchanged), but the `front_widened` FLAG — amber badge, Resolved
cap, FRONT_SIGMA_MIN — additionally requires RAW corroboration: some member's
unanchored forecast must itself undercut the observed min by the margin. And
either way the trigger details are surfaced (`front_guard`) and logged, so the
planned margin recalibration runs on evidence.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import betting_log
import forecast_log
import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


def _fc(curve):
    hours = sorted(curve)
    return ([_at(h) for h in hours], [curve[h] for h in hours])


def _curve(evening):
    """Standard day shape: dawn min ~78, peak 95 at 15:00, then `evening`."""
    base = {0: 84, 2: 82, 4: 80, 6: 78, 8: 82, 10: 86, 12: 90, 13: 92,
            14: 93, 15: 95, 16: 94, 17: 92}
    base.update(evening)
    return base


def _obs(afternoon_temp):
    """Observed series: dawn min 78.0, risen to `afternoon_temp` by 14:00 (the
    low is locked). 93 matches the members' 14:00 forecast (offset 0); a cooler
    reading makes the anchoring offset negative."""
    hours = [(0, 84), (2, 82), (4, 80), (6, 78.0), (8, 82), (10, 86),
             (12, 88), (14, afternoon_temp)]
    return ([_at(h) for h, _ in hours], [t for _, t in hours])


# --- model: flag requires raw corroboration; details surface either way ---

def test_anchor_only_undercut_projects_but_does_not_flag():
    # Raw evening min 79.5 never approaches the 78.0 morning min — but obs run
    # 3°F cool vs the forecast (90 obs vs fc 93 at 14:00), so the ANCHORED
    # projection is 76.5 and both members fire. The consensus may follow the
    # projection, but the flag must not raise on an undercut no forecast shows.
    ev = {18: 82, 21: 80.5, 23: 79.5}
    series = {"det_a": _fc(_curve(ev)), "det_b": _fc(_curve(ev))}
    out = model.predict_variable(series, {"obs": _obs(90.0)}, _DAY, "low",
                                 _at(14), None)
    assert out["peak_locked"] is True
    assert out["consensus"] < 78.0            # projections still shape the mean
    assert out["front_widened"] is False      # ...but no amber flag
    fg = out["front_guard"]
    assert fg is not None
    assert fg["raw_undercut"] is False
    assert fg["fired"] == 2


def test_raw_front_flags_with_details():
    # det_b's own forecast drops to 74 — a real front. Flag raises exactly as
    # before, and the trigger details are surfaced for the logs.
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 80, 21: 76, 23: 74}))}
    out = model.predict_variable(series, {"obs": _obs(93.0)}, _DAY, "low",
                                 _at(14), None)
    assert out["front_widened"] is True
    fg = out["front_guard"]
    assert fg == {"fired": 1, "members": 2, "projection": 74.0,
                  "undercut": 4.0, "raw_undercut": True}


def test_calm_locked_day_has_no_front_guard_detail():
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 84, 21: 82, 23: 81}))}
    out = model.predict_variable(series, {"obs": _obs(93.0)}, _DAY, "low",
                                 _at(14), None)
    assert out["front_widened"] is False
    assert out["front_guard"] is None


# --- forecast_log: recorded when fired, largest undercut latched ---

def _snapshot(day, front_guard, captured="T21:00:00-05:00"):
    var = {"day": day, "low": {"consensus": 77.0, "probabilities": {"77": 1.0}}}
    if front_guard is not None:
        var["low"]["front_guard"] = front_guard
    return {"updated": day + captured, "today": var}


_FG = {"fired": 2, "members": 6, "projection": 76.0, "undercut": 2.0,
       "raw_undercut": False}


def test_forecast_log_records_front_guard(tmp_path):
    p = str(tmp_path / "f.jsonl")
    forecast_log.record(_snapshot("2030-01-01", _FG), path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "low")
    assert row["front_guard"] == _FG


def test_forecast_log_omits_absent_front_guard(tmp_path):
    p = str(tmp_path / "f.jsonl")
    forecast_log.record(_snapshot("2030-01-01", None), path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "low")
    assert "front_guard" not in row


def test_forecast_log_latches_largest_undercut(tmp_path):
    # The day's strongest trigger survives later upserts: a calm 11:45pm capture
    # (no guard) or a weaker one must not erase it; a stronger one replaces it.
    p = str(tmp_path / "f.jsonl")
    forecast_log.record(_snapshot("2030-01-01", _FG), path=p, basis="cli")
    forecast_log.record(_snapshot("2030-01-01", None, "T22:00:00-05:00"),
                        path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "low")
    assert row["front_guard"] == _FG

    weaker = dict(_FG, undercut=0.7, projection=77.3)
    forecast_log.record(_snapshot("2030-01-01", weaker, "T22:30:00-05:00"),
                        path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "low")
    assert row["front_guard"] == _FG

    stronger = dict(_FG, undercut=3.5, projection=74.5)
    forecast_log.record(_snapshot("2030-01-01", stronger, "T23:00:00-05:00"),
                        path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "low")
    assert row["front_guard"] == stronger


# --- betting_log: recorded on the betting-time row ---

_CALIB = {"settlement_offset": {"high": 0.0, "high_std": 0.0,
                                "low": 0.0, "low_std": 0.0}}


def test_betting_log_records_front_guard(tmp_path):
    p = str(tmp_path / "b.jsonl")
    cli = {"today": {"day": "2030-01-01",
                     "low": {"consensus": 77.0, "probabilities": {"77": 1.0},
                             "observed_so_far": None, "observed_continuous": None,
                             "peak_locked": True, "sigma_used": 1.0,
                             "front_widened": False, "front_guard": _FG}}}
    betting_log.record(cli, {}, "sr", _CALIB, path=p)
    row = next(r for r in betting_log.load(p) if r["variable"] == "low")
    assert row["front_guard"] == _FG
