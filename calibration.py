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
from sources import open_meteo_ensemble, open_meteo_models, station_history
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


def _score_sigma(data, sigma):
    """(exact_peak_rate, cov80_rate) for a candidate base sigma over `data` =
    [(samples, actual), ...]. Peak = the argmax bin equals the settled bin."""
    import model
    from settlement import bin_for_temp
    from backtest import _interval_contains
    peak = cov = n = 0
    for samples, act in data:
        probs = model._bin_probabilities(samples, sigma)
        lab = bin_for_temp(act)
        n += 1
        if max(probs, key=probs.get) == lab:
            peak += 1
        if _interval_contains(probs, lab, 0.80):
            cov += 1
    return (peak / n, cov / n) if n else (0.0, 0.0)


def _exact_bin_sigma(fcst, actual, bias_var, var, residual_sigma,
                     cov_min=0.80, margin=0.0):
    """Pick the day-ahead base sigma that maximizes exact-bin hit rate, gated.

    The residual-std sigma (current default) is honest about *spread* but tends
    to sit a touch wider than the value that maximizes how often the peak bin is
    the settled degree. We search a grid in [_MIN_SIGMA, residual_sigma] (only
    TIGHTENING, and only to values the live model will actually honor given its
    _MIN_SIGMA floor), and accept a tighter sigma only if, on a held-out tail of
    the window, it (a) keeps 80% coverage >= cov_min and (b) beats the residual
    sigma's exact-bin by >= margin. Otherwise fall back to residual_sigma. This
    is the established gate idiom: never ship a change that doesn't generalize.
    """
    from model import _MIN_SIGMA
    if residual_sigma is None or residual_sigma <= _MIN_SIGMA:
        return residual_sigma
    days = sorted(d for d in fcst if d in actual)
    data = [([s - bias_var for s in fcst[d][var]],
             actual[d][0] if var == "high" else actual[d][1]) for d in days]
    if len(data) < 20:
        return residual_sigma
    grid = []
    s = _MIN_SIGMA
    while s <= residual_sigma + 1e-9:
        grid.append(round(s, 2))
        s += 0.1
    cut = int(len(data) * 0.6)
    train, test = data[:cut], data[cut:]
    base_ep, _ = _score_sigma(test, residual_sigma)

    def _best(subset):
        best = None
        for sig in grid:
            ep, cov = _score_sigma(subset, sig)
            if cov >= cov_min and (best is None or ep > best[1]):
                best = (sig, ep)
        return best

    cand = _best(train)
    if cand is None or cand[0] >= residual_sigma:
        return residual_sigma
    ep_test, cov_test = _score_sigma(test, cand[0])
    if cov_test >= cov_min and ep_test >= base_ep + margin:
        full = _best(data)        # stable final pick over the whole window
        return round(full[0], 2) if full else residual_sigma
    return residual_sigma


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
    # Gate math uses raw (unrounded) bucket means; _mean_std's rounded values are
    # used only for the emitted offset (matching _settlement_offset's convention),
    # so the comparison isn't skewed by mixing rounded and raw quantities.
    cc_raw = sum(gaps_cc) / len(gaps_cc) if gaps_cc else flat
    ot_raw = sum(gaps_ot) / len(gaps_ot) if gaps_ot else flat
    resid_flat = sum(abs(g - flat) for g in all_gaps) / len(all_gaps)
    resid_cond = (sum(abs(g - cc_raw) for g in gaps_cc)
                  + sum(abs(g - ot_raw) for g in gaps_ot)) / len(all_gaps)
    passed = (n_cc >= min_nights
              and abs(cc_raw - ot_raw) >= min_sep
              and resid_cond <= resid_flat - margin)
    if not passed:
        return flat, flat, 0.0, 0.0, False
    cc_mean, cc_std = _mean_std(gaps_cc) if gaps_cc else (flat, 0.0)
    ot_mean, ot_std = _mean_std(gaps_ot) if gaps_ot else (flat, 0.0)
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


def _system_extremes(start, end):
    """{day: {system: {'high':v, 'low':v}}} over [start, end].

    Systems = one combined 'ensemble_mean' (mean of all member extremes) plus
    each deterministic model by its label. NWS has no archive, so it is absent.
    Degrades to deterministic-only if the ensemble archive can't be fetched.
    """
    det = open_meteo_models.fetch_historical(start, end)
    try:
        ens = open_meteo_ensemble.fetch_historical(start, end)
    except Exception:
        ens = {}
    out: dict = {}
    day = start
    while day <= end:
        systems: dict[str, dict] = {}
        for label, (t, v) in det.items():
            hi, lo = day_high_low(t, v, day)
            if hi is not None:
                systems[label] = {"high": hi, "low": lo}
        ens_hi, ens_lo = [], []
        for _label, (t, v) in ens.items():
            hi, lo = day_high_low(t, v, day)
            if hi is not None:
                ens_hi.append(hi)
                ens_lo.append(lo)
        if ens_hi:
            systems["ensemble_mean"] = {"high": sum(ens_hi) / len(ens_hi),
                                        "low": sum(ens_lo) / len(ens_lo)}
        if systems:
            out[day] = systems
        day += timedelta(days=1)
    return out


def _system_weights(ext, actual, systems, lam=0.25):
    """{var: {system: weight}} from trailing skill, strongly shrunk to equal.

    For each variable: weight_i proportional to (1-lam)*equal + lam*invMAE_norm_i,
    where invMAE_norm normalizes inverse per-system MAE to sum 1. lam small =>
    near equal (conservative). Systems with no data on a day are skipped that day.
    """
    weights = {}
    n = len(systems)
    equal = 1.0 / n if n else 0.0
    for var in ("high", "low"):
        mae = {}
        for s in systems:
            errs = [abs(ext[d][s][var] - (actual[d][0] if var == "high" else actual[d][1]))
                    for d in ext if d in actual and s in ext[d]]
            mae[s] = (sum(errs) / len(errs)) if errs else None
        inv = {s: 1.0 / max(mae[s], 0.1) for s in systems if mae[s] is not None}
        inv_sum = sum(inv.values()) or 1.0
        inv_norm = {s: inv.get(s, 0.0) / inv_sum for s in systems}
        raw = {s: (1.0 - lam) * equal + lam * inv_norm[s] for s in systems}
        total = sum(raw.values()) or 1.0
        weights[var] = {s: raw[s] / total for s in systems}
    return weights


def _consensus_mae(ext, actual, systems, var, wmap):
    """Mean abs error of the wmap-weighted consensus over days with data."""
    errs = []
    for d in ext:
        if d not in actual:
            continue
        num = den = 0.0
        for s in systems:
            if s in ext[d]:
                w = wmap[s]
                num += w * ext[d][s][var]
                den += w
        if den <= 0:
            continue
        cons = num / den
        act = actual[d][0] if var == "high" else actual[d][1]
        errs.append(abs(cons - act))
    return (sum(errs) / len(errs)) if errs else float("inf")


def _actual_var(actual, d, var):
    return actual[d][0] if var == "high" else actual[d][1]


def _system_bias(ext, actual, system, var, days=None):
    """Mean signed error of one system's daily extreme over `days` (default all)."""
    days = days if days is not None else [d for d in ext if d in actual]
    errs = [ext[d][system][var] - _actual_var(actual, d, var)
            for d in days if d in actual and d in ext and system in ext[d]]
    return (sum(errs) / len(errs)) if errs else None


def _det_mean(ext, d, var):
    """The deterministic-only consensus (excludes the combined ensemble) for a day."""
    vals = [ext[d][s][var] for s in ext[d] if s != "ensemble_mean"]
    return (sum(vals) / len(vals)) if vals else None


def _det_consensus_bias(ext, actual, var, days):
    """Mean signed error of the deterministic consensus — the bias currently
    copied onto the ensemble. The gate's baseline."""
    errs = []
    for d in days:
        dm = _det_mean(ext, d, var)
        if dm is not None and d in actual:
            errs.append(dm - _actual_var(actual, d, var))
    return (sum(errs) / len(errs)) if errs else None


def _ens_bias_beats_copied(ext, actual, var, margin=0.05, train=30):
    """True iff the ensemble's OWN bias de-centers the ensemble mean better than
    the copied deterministic-consensus bias, OUT-OF-SAMPLE.

    Walk-forward, mirroring `_weights_beat_equal`: for each held-out day, both
    biases are learned from the trailing `train` days only, applied to that day's
    ensemble mean, and scored by absolute error. We gate on MAE (not exact-bin)
    because this is a sub-degree *centering* decision — MAE is the direct, low-
    noise signal, while exact-bin is a coarse function of it (cf. the project's
    'trust the MAE deltas over the win-rate' rule). Falls back to the copied bias
    unless the ensemble's own bias wins by >= margin, so a thin/noisy ensemble
    archive can never ship a worse center than today's behavior.
    """
    days = sorted(d for d in ext if d in actual and "ensemble_mean" in ext[d]
                  and _det_mean(ext, d, var) is not None)
    if len(days) <= train:
        return False
    own_errs, copied_errs = [], []
    for i in range(train, len(days)):
        d = days[i]
        window = days[i - train:i]
        act = _actual_var(actual, d, var)
        ens_val = ext[d]["ensemble_mean"][var]
        eb = _system_bias(ext, actual, "ensemble_mean", var, window)
        db = _det_consensus_bias(ext, actual, var, window)
        if eb is None or db is None:
            continue
        own_errs.append(abs(ens_val - eb - act))
        copied_errs.append(abs(ens_val - db - act))
    if not own_errs:
        return False
    return (sum(own_errs) / len(own_errs)) <= (sum(copied_errs) / len(copied_errs)) - margin


def _weights_beat_equal(ext, actual, systems, var, lam=0.25, margin=0.02, train=30):
    """True iff skill weights beat equal weight OUT-OF-SAMPLE by >= margin.

    Walk-forward: for each test day, weights are learned from the trailing
    `train` days only (never the test day itself), then both the weighted and
    the equal-weight consensus are scored on that held-out day. This guards
    against the in-sample illusion that weighting "always helps" — fitting
    weights on the same days you score on almost always wins, but may not
    generalize (e.g. the high benefits from equal-weight error cancellation).
    """
    days = sorted(d for d in ext if d in actual)
    if len(days) <= train:
        return False
    equal = {s: 1.0 for s in systems}
    w_errs, eq_errs = [], []
    for i in range(train, len(days)):
        d = days[i]
        window = days[i - train:i]
        tr_ext = {x: ext[x] for x in window}
        tr_act = {x: actual[x] for x in window}
        cand = _system_weights(tr_ext, tr_act, systems, lam)
        day_ext, day_act = {d: ext[d]}, {d: actual[d]}
        w_errs.append(_consensus_mae(day_ext, day_act, systems, var, cand[var]))
        eq_errs.append(_consensus_mae(day_ext, day_act, systems, var, equal))
    if not w_errs:
        return False
    return (sum(w_errs) / len(w_errs)) <= (sum(eq_errs) / len(eq_errs)) - margin


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
        resid_sigma = round(math.sqrt(resid_var), 2)
        # Sharpen toward the exact-bin optimum, gated + coverage-guarded; falls
        # back to the residual std when tightening doesn't generalize.
        try:
            sigma[var] = _exact_bin_sigma(fcst, actual, bias[var], var, resid_sigma)
        except Exception:
            sigma[var] = resid_sigma

    # Per-system archived extremes (deterministic models + combined ensemble mean),
    # fetched once and reused for both the ensemble bias and the skill weights.
    try:
        sysext = _system_extremes(start, end)
    except Exception:
        sysext = {}

    # Ensemble bias: the ensemble is the distribution's backbone, but it was
    # de-biased with the *deterministic* consensus bias. Give it its own, gated to
    # fall back to the copied value unless it wins out-of-sample.
    #   NOTE: the Open-Meteo ensemble *historical* archive only retains ~5 days
    #   (deep per-member history isn't free — see backtest.py module docstring),
    #   so this gate is effectively DORMANT today: it can't reach the >30-day OOS
    #   bar and safely keeps the copied bias. The deterministic bias is a fair
    #   proxy meanwhile (the EPS systems share the GFS/ECMWF/ICON cores). The real
    #   ensemble bias arrives once the forward log (which records per-source
    #   extremes — see forecast_log) accumulates; wiring that in is the follow-up.
    ens_bias = dict(bias)
    for var in ("high", "low"):
        if sysext and _ens_bias_beats_copied(sysext, actual, var):
            b = _system_bias(sysext, actual, "ensemble_mean", var)
            if b is not None:
                ens_bias[var] = round(b, 2)

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

    try:
        cond = open_meteo_models.historical_night_conditions(start, end)
    except Exception:
        cond = {}
    settlement_offset = _conditional_settlement_offset(cli_actual, actual, cond) \
        or _settlement_offset(cli_actual, actual)

    weights = {"high": {}, "low": {}}
    try:
        ext = sysext
        systems = sorted({s for day in ext.values() for s in day})
        if ext and len(systems) >= 2:
            cand = _system_weights(ext, actual, systems)
            # Three regimes per variable:
            #   gate passes -> skill-weighted systems (cand[var]);
            #   gate fails   -> uniform *system* weights (the rebalanced neutral);
            #   no archive / <2 systems / exception -> {} (handled below), which
            #     the model reads as OFF and falls back to the equal-per-member
            #     pool (old behavior).
            # The fail case is uniform-SYSTEM, not {}, on purpose: the group
            # rebalancing (ensemble counts as one estimator, not ~50 votes) beats
            # the old member-dominated pool out-of-sample on its own (validated:
            # low 1.21->1.03, high 0.95->0.92 MAE); the gate only decides the
            # additional skill tilt on top of that neutral.
            for var in ("high", "low"):
                if _weights_beat_equal(ext, actual, systems, var):
                    weights[var] = cand[var]
                else:
                    weights[var] = {s: 1.0 / len(systems) for s in systems}
    except Exception:
        weights = {"high": {}, "low": {}}

    return {
        "computed": datetime.now().isoformat(timespec="seconds"),
        "window_days": CALIBRATION_WINDOW_DAYS,
        "n_days": len(set(fcst) & set(actual)),
        # Ensemble gets its own (gated) bias; falls back to the deterministic
        # value when the gate doesn't fire. NWS has no free archive -> uncorrected.
        "bias": {
            "deterministic": bias,
            "ensemble": ens_bias,
            "nws": {"high": 0.0, "low": 0.0},
        },
        "sigma": sigma,
        "weights": weights,
        "cooling": cooling,
        "settlement_offset": settlement_offset,
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
