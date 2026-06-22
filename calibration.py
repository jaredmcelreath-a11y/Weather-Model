"""Bias and spread calibration against KDFW's actual history.

For the last CALIBRATION_WINDOW_DAYS we compare archived deterministic forecasts
to what KDFW actually recorded, then derive:

  * bias  — the mean signed error per variable (how warm/cold the models run at
    this station), which the model subtracts.
  * sigma — the residual RMSE after removing bias, i.e. the real forecast
    uncertainty, which the model uses as its target spread.

Result is cached to calibration.json and refreshed once a day.

Scope note (v1): the free historical-forecast archive returns a short-lead
forecast per past day, so the derived sigma reflects roughly day-ahead error and
the deterministic bias is reused as the ensemble bias (their means track closely).
True per-lead-bucket calibration is a documented future refinement; same-day
predictions don't lean on sigma anyway because the nowcast blend collapses them.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timedelta

from config import CALIBRATION_WINDOW_DAYS, CALM_WIND_MAX, CLEAR_CLOUD_MAX
from sources import open_meteo_models, station_history
from settlement import day_high_low

_PATH = os.path.join(os.path.dirname(__file__), "calibration.json")
_MAX_AGE = 24 * 3600


def _forecast_daily_extremes(start: date, end: date):
    """{day: {'high':[per-model], 'low':[per-model]}} from archived forecasts."""
    series = open_meteo_models.fetch_historical(start, end)
    out: dict[date, dict[str, list[float]]] = {}
    day = start
    while day <= end:
        highs, lows = [], []
        for _label, (times, temps) in series.items():
            hi, lo = day_high_low(times, temps, day)
            if hi is not None:
                highs.append(hi)
                lows.append(lo)
        if highs:
            out[day] = {"high": highs, "low": lows}
        day += timedelta(days=1)
    return out


def _mean_std(xs: list[float]) -> tuple[float, float]:
    """Population mean and std, each rounded to 2 dp."""
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return round(m, 2), round(var ** 0.5, 2)


def _settlement_offset(cli: dict, hourly: dict) -> dict:
    """Mean and std of the (CLI - hourly) daily-extreme gap, per variable.

    The Kalshi page adds the mean to the hourly forecast (to reach the CLI
    settlement basis) and the std in quadrature to its spread (the gap is an
    unobservable average, not exact). Zeros when there is no overlapping
    history (safe degrade to current behavior)."""
    dh, dl = [], []
    for day, (chi, clo) in cli.items():
        if day not in hourly:
            continue
        hhi, hlo = hourly[day]
        dh.append(chi - hhi)
        dl.append(clo - hlo)
    if not dh:
        return {"high": 0.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0, "n_days": 0}
    hm, hs = _mean_std(dh)
    lm, ls = _mean_std(dl)
    return {"high": hm, "low": lm, "high_std": hs, "low_std": ls, "n_days": len(dh)}


def _var_bucket(
    gaps_cc: list[float], gaps_ot: list[float],
    min_nights: int, margin: float, min_sep: float,
) -> tuple[float, float, float, float, bool]:
    """Per-variable bucket means/stds + whether the split is worth keeping.

    Returns (cc_mean, ot_mean, cc_std, ot_std, passed). `passed` is True only
    when there are >= min_nights clear/calm nights, the two bucket means differ
    by at least `min_sep` degrees (so a near-identical split is rejected), AND
    splitting reduces the mean absolute residual vs a single flat mean by at
    least `margin`. The separation guard is what makes "buckets too similar"
    fall back to flat — with real within-bucket noise the residual check alone
    is not enough.

    Requires at least one gap across both buckets; returns a not-passed result
    for an empty input rather than dividing by zero.
    """
    n_cc = len(gaps_cc)
    all_gaps = gaps_cc + gaps_ot
    if not all_gaps:
        return 0.0, 0.0, 0.0, 0.0, False
    flat = sum(all_gaps) / len(all_gaps)
    cc_mean, cc_std = _mean_std(gaps_cc) if gaps_cc else (flat, 0.0)
    ot_mean, ot_std = _mean_std(gaps_ot) if gaps_ot else (flat, 0.0)
    resid_flat = sum(abs(g - flat) for g in all_gaps) / len(all_gaps)
    resid_cond = (sum(abs(g - cc_mean) for g in gaps_cc)
                  + sum(abs(g - ot_mean) for g in gaps_ot)) / len(all_gaps)
    passed = (n_cc >= min_nights
              and abs(cc_mean - ot_mean) >= min_sep
              and resid_cond <= resid_flat - margin)
    if not passed:
        return flat, flat, 0.0, 0.0, False
    return cc_mean, ot_mean, cc_std, ot_std, True


def _conditional_settlement_offset(cli: dict, hourly: dict, cond: dict,
                                   min_nights: int = 5, margin: float = 0.02,
                                   min_sep: float = 0.25) -> dict | None:
    """Bucketed (clear_calm/other) CLI-hourly offset, or None to use the flat one.

    Splits the per-day gap by overnight conditions (cloud<CLEAR_CLOUD_MAX and
    wind<CALM_WIND_MAX). Returns the bucketed dict only if at least one variable's
    split is worth keeping (see `_var_bucket`); otherwise None so the caller falls
    back to the flat `_settlement_offset`.
    """
    cc = {"high": [], "low": []}
    ot = {"high": [], "low": []}
    for day, (chi, clo) in cli.items():
        if day not in hourly or day not in cond:
            continue
        hhi, hlo = hourly[day]
        cloud, wind = cond[day]
        bucket = cc if (cloud < CLEAR_CLOUD_MAX and wind < CALM_WIND_MAX) else ot
        bucket["high"].append(chi - hhi)
        bucket["low"].append(clo - hlo)
    if not cc["low"] and not ot["low"]:
        return None
    out = {}
    any_passed = False
    for var in ("high", "low"):
        cm, om, cs, os_, passed = _var_bucket(cc[var], ot[var], min_nights,
                                              margin, min_sep)
        any_passed = any_passed or passed
        out[var] = {"clear_calm": cm, "other": om,
                    "clear_calm_std": cs, "other_std": os_}
    if not any_passed:
        return None
    out["n_days"] = len(cc["high"]) + len(ot["high"])
    out["n_clear_calm"] = len(cc["high"])
    return out


def compute() -> dict:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=CALIBRATION_WINDOW_DAYS)
    actual = station_history.fetch_actual(start, end)
    try:
        cli_actual = station_history.fetch_actual_cli(start, end)
    except Exception:
        cli_actual = {}
    fcst = _forecast_daily_extremes(start, end)

    # Error of the *consensus* (model mean), since the model predicts around the
    # consensus — pooling individual-model errors would overstate uncertainty.
    errs = {"high": [], "low": []}
    for day, ext in fcst.items():
        if day not in actual:
            continue
        act_hi, act_lo = actual[day]
        errs["high"].append(sum(ext["high"]) / len(ext["high"]) - act_hi)
        errs["low"].append(sum(ext["low"]) / len(ext["low"]) - act_lo)

    bias, sigma = {}, {}
    for var in ("high", "low"):
        e = errs[var]
        if not e:
            bias[var], sigma[var] = 0.0, None
            continue
        b = sum(e) / len(e)
        resid_var = sum((x - b) ** 2 for x in e) / len(e)
        bias[var] = round(b, 2)
        sigma[var] = round(math.sqrt(resid_var), 2)

    # Empirical per-lead spread from the forward prediction log, once enough days
    # have settled (lazy import avoids a cycle: scoring -> backtest -> calibration).
    try:
        import scoring
        by_lead = scoring.per_lead_sigma()
        if by_lead:
            sigma["by_lead"] = by_lead
    except Exception:
        pass

    cooling = _cooling_offset(start, end, fcst, actual, bias.get("low", 0.0))

    return {
        "computed": datetime.now().isoformat(timespec="seconds"),
        "window_days": CALIBRATION_WINDOW_DAYS,
        "n_days": len(set(fcst) & set(actual)),
        # deterministic bias reused as the ensemble bias (see module note); NWS
        # has no free archive, so it is left uncorrected.
        "bias": {
            "deterministic": bias,
            "ensemble": bias,
            "nws": {"high": 0.0, "low": 0.0},
        },
        "sigma": sigma,
        "cooling": cooling,
        "settlement_offset": _settlement_offset(cli_actual, actual),
    }


def _cooling_offset(start: date, end: date, fcst: dict, actual: dict,
                    bias_low: float) -> dict:
    """Extra cooling the bias-corrected model still misses on clear+calm nights.

    For each clear+calm night (overnight cloud & wind below thresholds), measure
    how much warmer the bias-corrected consensus low was than the actual low.
    The mean of those residuals is the offset the model subtracts from the low
    on future clear+calm nights. Needs >= 5 such nights to be trusted, else 0.
    """
    try:
        cond = open_meteo_models.historical_night_conditions(start, end)
    except Exception:
        cond = {}
    resid = []
    for day, ext in fcst.items():
        if day not in actual or day not in cond:
            continue
        cloud, wind = cond[day]
        if cloud < CLEAR_CLOUD_MAX and wind < CALM_WIND_MAX:
            cons_low = sum(ext["low"]) / len(ext["low"])
            resid.append((cons_low - bias_low) - actual[day][1])
    n = len(resid)
    return {
        "cloud_thresh": CLEAR_CLOUD_MAX,
        "wind_thresh": CALM_WIND_MAX,
        "n_clear_calm": n,
        "low_offset": round(sum(resid) / n, 2) if n >= 5 else 0.0,
    }


def compute_and_save() -> dict:
    calib = compute()
    with open(_PATH, "w") as fh:
        json.dump(calib, fh, indent=2)
    return calib


def get(refresh: bool = True) -> dict | None:
    """Return cached calibration, recomputing if stale. None if unavailable and
    recompute is off (model then falls back to its built-in defaults)."""
    if os.path.exists(_PATH):
        fresh = time.time() - os.path.getmtime(_PATH) < _MAX_AGE
        with open(_PATH) as fh:
            cached = json.load(fh)
        if fresh or not refresh:
            return cached
    if not refresh:
        return None
    try:
        return compute_and_save()
    except Exception:
        return None
