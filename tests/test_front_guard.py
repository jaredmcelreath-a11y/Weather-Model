"""Front-aware locked low: a locked member reports its anchored post-noon
forecast minimum instead of the observed min when that projection undercuts it
by FRONT_UNDERCUT_MARGIN — so a dry evening cold front (which the POP-gated
convective floor can't see) reopens the low instead of being discarded."""
from datetime import date, datetime, timedelta

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


def _at_next(hour, minute=0):
    """A datetime on the clock day AFTER _DAY (the settlement day's LST tail)."""
    nxt = _DAY + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, hour, minute, tzinfo=_TZ)


def _fc(curve):
    """{hour: temp} -> full-day hourly (times, temps) forecast series.
    Hours not listed interpolate nothing — list every hour you need."""
    hours = sorted(curve)
    return ([_at(h) for h in hours], [curve[h] for h in hours])


def _curve(evening):
    """A standard day shape: cool dawn, warm afternoon, then `evening` values
    for hours 18/21/23. Morning min ~78, peak 95 at 15:00."""
    base = {0: 84, 2: 82, 4: 80, 6: 78, 8: 82, 10: 86, 12: 90, 13: 92,
            14: 93, 15: 95, 16: 94, 17: 92}
    base.update(evening)
    return base


# ---- the locked-low undercut path (unit: _member_extreme directly) ----

def test_calm_locked_low_returns_observed():
    # Evening stays well above the 78.0 morning min -> locked exactly as today.
    times, temps = _fc(_curve({18: 88, 21: 84, 23: 81}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_front_undercut_reports_forecast_min():
    # Front: evening drops to 74.5 (3.5 under the observed min) -> the member
    # reports its projected new low, not the stale morning min.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 74.5


def test_pre_noon_dip_cannot_trigger():
    # A 9am forecast dip 2 under the min (dawn-adjacent jitter, the reason the
    # early sunrise lock exists) must NOT reopen the lock: scan starts at 12:00.
    curve = _curve({18: 88, 21: 84, 23: 81})
    curve[9] = 76.0                      # pre-noon dip, still in `remaining` at 08:00
    times, temps = _fc(curve)
    got = model._member_extreme(times, temps, _DAY, "low", _at(8),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_margin_graze_ignored():
    # Post-noon min 77.7 vs observed 78.0: undercut 0.3 < 0.5 margin -> locked.
    times, temps = _fc(_curve({18: 82, 21: 79, 23: 77.7}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_no_remaining_postnoon_hours_falls_back():
    # 23:30 with the last forecast point at 23:00 (already past): nothing left
    # to scan -> observed, no crash.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(23, 30),
                                observed=74.5, obs_now=None, locked=True)
    assert got == 74.5


def test_anchoring_offset_applies_to_scan():
    # Raw post-noon min is 78.2 (no trigger vs 78.0), but the member currently
    # reads 1°F warm (obs_now 89 vs fc_now 90 at 13:00 — the curve's 13:00 value
    # is pinned to 90 so the interpolated fc_now is exact), so its anchored
    # evening projection is 77.2 -> undercut fires at the ANCHORED value.
    curve = _curve({18: 82, 21: 79.5, 23: 78.2})
    curve[13] = 90.0                     # fc_now at 13:00 -> offset = 89 - 90 = -1
    times, temps = _fc(curve)
    unanchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                       observed=78.0, obs_now=None, locked=True)
    anchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                     observed=78.0, obs_now=89.0, locked=True)
    assert unanchored == 78.0
    assert anchored == 77.2


def test_locked_high_still_pins_to_observed():
    # The high's locked branch is untouched: a forecast projecting hotter later
    # is still ignored once locked (peak-postdates-trough guard owns the high).
    times, temps = _fc(_curve({18: 96, 21: 90, 23: 86}))
    got = model._member_extreme(times, temps, _DAY, "high", _at(16),
                                observed=95.0, obs_now=None, locked=True)
    assert got == 95.0


# ---- integration: predict_variable consensus/sigma/flag ----

def _obs_locked_afternoon():
    """Observed series: dawn min 78.0 at 06:00, risen to 93 by 14:00 (locked).
    The 14:00 reading matches the members' 14:00 forecast (93 in `_curve`) so
    the anchoring offset is 0 and the evening scenarios stay uncontaminated."""
    hours = [(0, 84), (2, 82), (4, 80), (6, 78.0), (8, 82), (10, 86),
             (12, 88), (14, 93)]
    return ([_at(h) for h, _ in hours], [t for _, t in hours])


def test_predict_variable_front_day_shifts_and_widens():
    # Two members agree through the afternoon (fc 93 at 14:00 -> offset 0), then
    # disagree on the evening: det_a stays warm, det_b drops to 74. The locked
    # low must shift below the morning min, reopen its spread, and set the flag.
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 80, 21: 76, 23: 74}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["peak_locked"] is True
    assert out["front_widened"] is True
    assert out["consensus"] < 78.0          # mean of 78 and 74
    assert out["sigma_used"] > model._SIGMA_FLOOR


def test_predict_variable_calm_day_unchanged():
    # Both members keep the evening above the min: byte-identical to today —
    # consensus pinned to the observed min, sigma collapsed to the floor.
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80})),
              "det_b": _fc(_curve({18: 84, 21: 82, 23: 81}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["peak_locked"] is True
    assert out["front_widened"] is False
    assert out["consensus"] == 78.0
    assert out["sigma_used"] == model._SIGMA_FLOOR


def test_high_never_sets_front_widened():
    series = {"det_a": _fc(_curve({18: 86, 21: 83, 23: 80}))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "high", _at(14), None)
    assert out["front_widened"] is False


def test_unanimous_undercut_floors_sigma():
    # BOTH members project the same evening undercut: the sample spread
    # collapses, but the projected new low is still an hours-ahead forecast —
    # sigma must floor at FRONT_SIGMA_MIN instead of printing observation-noise
    # confidence (the May 5 replay: sigma 0.8 on a 3.2°F miss).
    from config import FRONT_SIGMA_MIN
    ev = {18: 80, 21: 76, 23: 74}
    series = {"det_a": _fc(_curve(ev)), "det_b": _fc(_curve(ev))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is True
    assert out["sigma_used"] >= FRONT_SIGMA_MIN


def test_front_scan_includes_post_midnight_tail():
    # Under the LST window, _DAY's settlement day runs to 01:00 CDT the next
    # clock day. A front whose only undercut is a forecast reading at 00:30 CDT
    # the next day must reopen the locked low — the old `t.hour >= 12` clock
    # filter dropped it (hour 0).
    ev = _curve({18: 88, 21: 85, 23: 82})          # evening stays warm...
    series = {"det_a": _fc(ev), "det_b": _fc(ev)}
    # ...but append a cold projection at 00:30 CDT the NEXT clock day (in-window)
    tail_t = _at_next(0, 30)
    for lbl in series:
        t, v = series[lbl]
        series[lbl] = (t + [tail_t], v + [70.0])
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is True
    assert out["consensus"] < 78.0                 # pulled toward the 70 tail


def test_front_scan_still_excludes_pre_noon_dip():
    # A pre-noon dip (hour 9) still cannot trigger the guard.
    curve = _curve({18: 88, 21: 85, 23: 82})
    curve[9] = 70.0
    series = {"det_a": _fc(curve), "det_b": _fc(curve)}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is False


def test_lone_outlier_member_does_not_flag():
    # 2026-07-19 live episode: 2 of 151 members (ECMWF ensemble perturbations)
    # raw-undercut on a calm 101°F ridge day and the any() corroboration lit the
    # badge. A lone cold outlier in a big member set is ensemble noise, not a
    # front — the flag needs a quorum (FRONT_RAW_MIN_FRAC of scanned members).
    # The projections still shape the consensus; only the flag stays off.
    warm = _curve({18: 86, 21: 83, 23: 80})
    series = {f"ens_member{i:02d}_x": _fc(warm) for i in range(9)}
    series["ens_member09_x"] = _fc(_curve({18: 80, 21: 76, 23: 74}))
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["peak_locked"] is True
    assert out["front_widened"] is False
    assert out["front_guard"]["raw_undercut"] is False
    assert out["front_guard"]["raw_under"] == 1      # logged for recalibration
    assert out["front_guard"]["raw_scanned"] == 10


def test_member_quorum_flags():
    # Real fronts corroborate broadly (May 5 / May 10 replays: 100% of members
    # raw-undercut). A 3-of-10 quorum clears the 20% floor and must still flag.
    warm = _curve({18: 86, 21: 83, 23: 80})
    cold = _curve({18: 80, 21: 76, 23: 74})
    series = {f"ens_member{i:02d}_x": _fc(warm) for i in range(7)}
    for i in range(7, 10):
        series[f"ens_member{i:02d}_x"] = _fc(cold)
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is True
    assert out["front_guard"]["raw_undercut"] is True
    assert out["front_guard"]["raw_under"] == 3
