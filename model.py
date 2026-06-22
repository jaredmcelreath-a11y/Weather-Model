"""The probability engine.

Turns the multi-source forecasts into an honest probability for each market bin,
for a target day's high and low. Pipeline (see plan):

  1. Build a sample set  — each ensemble member (and each deterministic/NWS
     forecast) contributes one daily-extreme value.
  2. Bias-correct        — subtract each source-group's calibrated, lead-aware
     bias (no-op until calibration.py has run; safe default = 0).
  3. Smooth into bins    — model the samples as a Gaussian mixture whose total
     variance is inflated to the calibrated forecast error, integrate per bin.
  4. Nowcast blend       — for *today*, fold in observed temps: the realized
     extreme is a hard floor/ceiling and the distribution collapses toward it as
     the day's peak/trough passes. Skipped for future days.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    LEAD_SIGMA_INFLATION, TIMEZONE, bin_labels, lead_bucket)
from settlement import local_day_bounds, observed_so_far
from sources import (open_meteo_ensemble, open_meteo_models, nws_forecast,
                     nws_observations)

TZ = ZoneInfo(TIMEZONE)

# Uncertainty knobs (degrees F, 1-sigma).
_DEFAULT_INFLATION = 1.3   # inflate raw ensemble spread when no calibration yet
_DEFAULT_SIGMA = 2.0       # fallback day-ahead spread when no calibration yet
_MIN_SIGMA = 1.0           # floor on the day-ahead (pure-forecast) spread
_SIGMA_FLOOR = 0.7         # fully-locked residual: observation + rounding noise
_MIN_BANDWIDTH = 0.7       # kernel smoothing bandwidth


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _group_of(label: str) -> str:
    """Source group used for bias lookup."""
    if label.startswith("ens_"):
        return "ensemble"
    if label.startswith("det_"):
        return "deterministic"
    if label.startswith("nws"):
        return "nws"
    return "other"


def _member_extreme(times, temps, day, variable, now, observed, obs_now=None):
    """One member's contribution to the high/low sample for `day`.

    For today, blends the realized extreme with the member's forecast over the
    *remaining* hours. Crucially, the remaining forecast is anchored to the
    latest observation: if this member currently reads N° off from reality, its
    remaining hours are shifted by that error before taking the extreme — so once
    the peak has passed and temps are falling, the model follows reality down
    instead of trusting a stale, too-warm forecast. Future days: full-day extreme.
    """
    start, end = local_day_bounds(day)
    day_vals, remaining = [], []
    fc_now = None  # member's forecast value at the most recent hour <= now
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if not (start <= t < end):
            continue
        day_vals.append(v)
        if now is not None:
            if t > now:
                remaining.append(v)
            else:
                fc_now = v  # ascending order -> ends on the latest <= now
    if not day_vals:
        return None

    is_today = now is not None and start <= now < end
    if not is_today:
        return max(day_vals) if variable == "high" else min(day_vals)

    # Anchor the remaining forecast to the current observation.
    offset = (obs_now - fc_now) if (obs_now is not None and fc_now is not None) else 0.0
    remaining = [v + offset for v in remaining]

    # Today: combine realized-so-far with the anchored forecast of what's left.
    if variable == "high":
        fcst = max(remaining) if remaining else -math.inf
        return max(observed if observed is not None else -math.inf, fcst)
    else:
        fcst = min(remaining) if remaining else math.inf
        return min(observed if observed is not None else math.inf, fcst)


def _sample_weights(series, weights):
    """Map each member label to its per-sample weight from system weights.

    The combined ensemble's mass (`weights['ensemble_mean']`) is split evenly
    across its members so they still shape the distribution; each deterministic
    model keys by its own label; NWS keys by 'nws'. Missing systems fall back to
    the average system weight so an unexpected label can't be silently dropped.
    """
    avg = (sum(weights.values()) / len(weights)) if weights else 1.0
    ens_labels = [l for l in series if l.startswith("ens_")]
    m = len(ens_labels) or 1
    w_ens = weights.get("ensemble_mean", avg)
    out = {}
    for label in series:
        if label.startswith("ens_"):
            out[label] = w_ens / m
        elif label.startswith("det_"):
            out[label] = weights.get(label, avg)
        else:
            out[label] = weights.get("nws", avg)
    return out


def _collect_samples(series, day, variable, now, observed, bias, obs_now=None,
                     weights=None):
    """(values, weights) lists of daily extremes for `day`.

    Bias correction applies only to pure forecasts (skipped while anchoring to a
    live obs). `weights` is an optional {system: weight} map; when absent every
    sample weighs 1.0 (identical to the old equal-weight behavior).
    """
    anchoring = obs_now is not None
    wmap = _sample_weights(series, weights) if weights else None
    vals, ws = [], []
    for label, (times, temps) in series.items():
        val = _member_extreme(times, temps, day, variable, now, observed, obs_now)
        if val is None or not math.isfinite(val):
            continue
        if not anchoring:
            val -= bias.get(_group_of(label), {}).get(variable, 0.0)
        vals.append(val)
        ws.append(wmap[label] if wmap else 1.0)
    return vals, ws


def _bin_probabilities(samples, target_sigma, weights=None):
    """Gaussian-mixture density over weighted samples -> probability per bin.

    `weights` are per-sample mixture weights (default uniform). The ensemble
    members supply the shape; the total spread is pinned to `target_sigma` by
    scaling samples about their weighted mean with a fixed bandwidth, so total
    variance == target_sigma^2 regardless of the raw spread. Uniform weights
    reproduce the unweighted result exactly.
    """
    if weights is None:
        weights = [1.0] * len(samples)
    W = sum(weights) or 1.0
    mean = sum(w * s for w, s in zip(weights, samples)) / W
    raw_var = sum(w * (s - mean) ** 2 for w, s in zip(weights, samples)) / W
    bw = _MIN_BANDWIDTH
    needed = target_sigma ** 2 - bw ** 2
    if needed <= 0 or raw_var < 1e-6:
        samples = [mean]
        weights = [1.0]
        W = 1.0
        bw = max(target_sigma, _MIN_BANDWIDTH)
    else:
        alpha = math.sqrt(needed / raw_var)
        samples = [mean + alpha * (s - mean) for s in samples]

    probs = {}
    for label in bin_labels():
        if label.startswith("<="):
            edge = BIN_LOW + 0.5
            p = sum(w * _norm_cdf(edge, s, bw) for w, s in zip(weights, samples)) / W
        elif label.startswith(">="):
            edge = BIN_HIGH - 0.5
            p = sum(w * (1.0 - _norm_cdf(edge, s, bw)) for w, s in zip(weights, samples)) / W
        else:
            t = int(label)
            lo, hi = t - 0.5, t + 0.5
            p = sum(w * (_norm_cdf(hi, s, bw) - _norm_cdf(lo, s, bw))
                    for w, s in zip(weights, samples)) / W
        probs[label] = p
    total = sum(probs.values()) or 1.0
    return {k: v / total for k, v in probs.items()}


def _apply_hard_bound(probs, variable, observed):
    """Zero out bins that are physically impossible given what's already been
    observed today, then renormalize. A high can't be below the temperature the
    station has already reached; a low can't be above the coldest reading so far.
    """
    if observed is None:
        return probs
    bounded = {}
    for label, p in probs.items():
        if label.startswith("<="):
            # captures high/low <= BIN_LOW; impossible if a higher temp is locked in
            impossible = (variable == "high" and BIN_LOW + 0.5 <= observed)
        elif label.startswith(">="):
            impossible = (variable == "low" and BIN_HIGH - 0.5 >= observed)
        else:
            t = int(label)
            if variable == "high":
                impossible = (t + 0.5 <= observed)   # high >= observed
            else:
                impossible = (t - 0.5 >= observed)   # low  <= observed
        bounded[label] = 0.0 if impossible else p
    total = sum(bounded.values())
    if total <= 0:
        return probs  # bound removed everything (stale obs); fall back
    return {k: v / total for k, v in bounded.items()}


def _latest_obs(times, temps, day, now):
    """Most recent observed temperature at or before `now` within `day`."""
    start, end = local_day_bounds(day)
    latest = None
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if start <= t <= now and t < end:
            latest = v  # ascending -> ends on the most recent
    return latest


def _std(samples):
    if len(samples) < 2:
        return 0.0
    m = sum(samples) / len(samples)
    return math.sqrt(sum((s - m) ** 2 for s in samples) / len(samples))


def _day_ahead_sigma(fullday_samples, calib_sigma):
    """The pure-forecast (no obs yet) 1-sigma spread for this variable."""
    if calib_sigma is not None:
        return max(calib_sigma, _MIN_SIGMA)
    raw = _std(fullday_samples)
    return max(_DEFAULT_INFLATION * raw, _MIN_SIGMA) if raw else _DEFAULT_SIGMA


def _offset_bucket(settle_offset, variable, day, calib):
    """(shift, gap_std) for `variable` from a settlement-offset spec.

    Accepts the flat shape ({var: float, var_std: float}) and the bucketed shape
    ({var: {"clear_calm": float, "other": float, "clear_calm_std": float,
    "other_std": float}}). For the bucketed
    shape, the bucket is chosen from the overnight forecast conditions for `day`,
    defaulting to 'other' when conditions can't be fetched.
    """
    spec = (settle_offset or {}).get(variable)
    if isinstance(spec, dict):
        cool = (calib or {}).get("cooling") or {}
        ct = cool.get("cloud_thresh", CLEAR_CLOUD_MAX)
        wt = cool.get("wind_thresh", CALM_WIND_MAX)
        bucket = "other"
        try:
            cloud, wind = open_meteo_models.night_conditions(day)
            if cloud is not None and cloud < ct and wind < wt:
                bucket = "clear_calm"
        except Exception:
            pass
        return spec.get(bucket, 0.0), spec.get(f"{bucket}_std", 0.0)
    return ((settle_offset or {}).get(variable, 0.0),
            (settle_offset or {}).get(f"{variable}_std", 0.0))


def predict_variable(series, obs_series, day, variable, now, calib,
                     settle_offset=None):
    """Return a dict describing the predicted distribution for one variable.

    Spread logic: start from the calibrated day-ahead consensus error, then
    shrink it by how much of the forecast uncertainty has already been resolved
    by observations (the ratio of remaining ensemble spread to full-day spread).
    A fully-realized extreme collapses toward observation noise.
    """
    obs_times, obs_temps = obs_series.get("obs", ([], []))
    obs_max, obs_min = observed_so_far(obs_times, obs_temps, day, now) \
        if now is not None else (None, None)
    observed = obs_max if variable == "high" else obs_min
    obs_now = _latest_obs(obs_times, obs_temps, day, now) if now is not None else None

    bias = (calib or {}).get("bias", {})
    # Full-day extremes (ignoring obs) set the reference spread; nowcast-blended
    # samples carry the realized floor/ceiling and forecast anchored to obs_now.
    var_weights = (calib or {}).get("weights", {}).get(variable)
    fullday, _fw = _collect_samples(series, day, variable, None, None, bias)
    samples, weights = _collect_samples(series, day, variable, now, observed, bias,
                                        obs_now, var_weights)
    if not samples or not fullday:
        return None

    # Radiational cooling: on a forecast clear+calm night the bias-corrected low
    # still runs warm, so nudge the (pure-forecast) low samples down by the
    # calibrated offset. Skipped once obs are anchoring the day (obs_now set) —
    # the realized low already supersedes the forecast there.
    cooling_applied = False
    if variable == "low" and obs_now is None:
        cool = (calib or {}).get("cooling") or {}
        off = cool.get("low_offset", 0.0)
        if off:
            try:
                cloud, wind = open_meteo_models.night_conditions(day)
            except Exception:
                cloud = wind = None
            if (cloud is not None and cloud < cool["cloud_thresh"]
                    and wind < cool["wind_thresh"]):
                samples = [s - off for s in samples]
                cooling_applied = True

    # Kalshi settlement basis: shift the forecast distribution to the CLI basis
    # by a calibrated per-variable offset. Applied to the forecast samples only,
    # NOT the hard observed bound (the offset is an average gap, not a floor) —
    # so consensus/bins move but still-possible bins are not zeroed. A constant
    # shift leaves sigma and locked_ratio unchanged. None => Robinhood, no shift.
    settle_shift, settle_gap_std = _offset_bucket(settle_offset, variable, day, calib)
    if settle_shift:
        samples = [s + settle_shift for s in samples]
        fullday = [s + settle_shift for s in fullday]

    calib_sigma = (calib or {}).get("sigma", {}).get(variable)
    sigma_day_ahead = _day_ahead_sigma(fullday, calib_sigma)
    fullday_sd = _std(fullday)
    locked_ratio = min(1.0, _std(samples) / fullday_sd) if fullday_sd > 1e-6 else 0.0

    # Lead-aware spread: an empirical per-lead sigma from the forward log wins
    # when available; otherwise inflate the day-ahead sigma by the interim factor
    # for this lead bucket (today=0 -> 1.0, tomorrow wider). The nowcast
    # locked_ratio then collapses it as observations come in (today only).
    bucket = lead_bucket(now, day) if now is not None else 0
    by_lead = (calib or {}).get("sigma", {}).get("by_lead", {})
    emp = (by_lead.get(str(bucket)) or by_lead.get(bucket) or {}).get(variable)
    if emp is not None:
        sigma_base = max(emp, _MIN_SIGMA)
    else:
        sigma_base = sigma_day_ahead * LEAD_SIGMA_INFLATION.get(bucket, 1.0)
    sigma = max(sigma_base * locked_ratio, _SIGMA_FLOOR)

    # The CLI settlement offset is an average; its gap has irreducible spread
    # (std from calibration) we can't observe live, so widen sigma by it in
    # quadrature whenever the offset is applied. Center (consensus) is unchanged.
    if settle_gap_std:
        sigma = math.hypot(sigma, settle_gap_std)

    probs = _bin_probabilities(samples, sigma, weights)
    probs = _apply_hard_bound(probs, variable, observed)

    _w = sum(weights) or 1.0
    mean = sum(w * s for w, s in zip(weights, samples)) / _w
    return {
        "probabilities": probs,
        "consensus": round(mean, 1),
        "sample_spread": round(_std(samples), 1),
        "sigma_used": round(sigma, 1),
        "locked_ratio": round(locked_ratio, 2),
        "n_samples": len(samples),
        "observed_so_far": observed,
        "cooling_applied": cooling_applied,
    }


def bin_temp(label: str) -> int:
    """Integer temperature a bin label represents (tails map to their edge)."""
    if label.startswith("<="):
        return BIN_LOW
    if label.startswith(">="):
        return BIN_HIGH
    return int(label)


def prob_at_least(probs: dict, threshold: int) -> float:
    """P(value >= threshold) from a per-bin probability dict."""
    return sum(v for k, v in probs.items() if bin_temp(k) >= threshold)


def prob_at_most(probs: dict, threshold: int) -> float:
    """P(value <= threshold) from a per-bin probability dict."""
    return sum(v for k, v in probs.items() if bin_temp(k) <= threshold)


def prob_greater_than(probs: dict, threshold: int) -> float:
    """P(value > threshold) under whole-degree settlement — i.e. value >= T+1.
    Matches a Robinhood 'Greater than T°' (high) contract resolving YES."""
    return prob_at_least(probs, threshold + 1)


def prob_less_than(probs: dict, threshold: int) -> float:
    """P(value < threshold) under whole-degree settlement — i.e. value <= T-1.
    Matches a Robinhood 'Lower than T°' (low) contract resolving YES."""
    return prob_at_most(probs, threshold - 1)


def prob_for_contract(probs: dict, kind: str, strike: int) -> float:
    """Model YES probability for a Robinhood ladder contract ('>' high / '<' low)."""
    return prob_greater_than(probs, strike) if kind == ">" \
        else prob_less_than(probs, strike)


def prob_for_strike(probs: dict, strike_type: str, floor, cap) -> float:
    """Model YES probability for a Kalshi contract, from its strike fields.

    - 'less'    (e.g. cap=88, "87° or below"): value <= cap-1
    - 'greater' (e.g. floor=95, "96° or above"): value >= floor+1
    - 'between' (floor..cap inclusive): floor <= value <= cap
    """
    if strike_type == "less":
        return prob_at_most(probs, cap - 1)
    if strike_type == "greater":
        return prob_at_least(probs, floor + 1)
    return prob_at_most(probs, cap) - prob_at_most(probs, floor - 1)


def gather_series(forecast_days: int = 2):
    """All forecast series merged into one dict, plus the obs series."""
    series = {}
    series.update(open_meteo_ensemble.fetch(forecast_days))
    series.update(open_meteo_models.fetch(forecast_days))
    series.update(nws_forecast.fetch())
    obs = nws_observations.fetch()
    return series, obs


def predict(day: date, now: datetime | None = None, calib: dict | None = None,
            forecast_days: int = 2, settle_offset=None) -> dict:
    """Full prediction (high + low) for `day`. `now` enables the nowcast blend
    when `day` is today; pass None to force a pure forecast."""
    if now is None:
        now = datetime.now(TZ)
    series, obs = gather_series(forecast_days)
    return _predict_from(series, obs, day, now, calib, settle_offset)


def _predict_from(series, obs, day, now, calib, settle_offset=None):
    return {
        "day": day.isoformat(),
        "high": predict_variable(series, obs, day, "high", now, calib, settle_offset),
        "low": predict_variable(series, obs, day, "low", now, calib, settle_offset),
    }


def per_source_extremes(series, day):
    """{group: {label: (high, low)}} for the source-transparency panel."""
    from settlement import day_high_low
    out: dict[str, dict[str, tuple]] = {}
    for label, (times, temps) in series.items():
        hi, lo = day_high_low(times, temps, day)
        if hi is None:
            continue
        out.setdefault(_group_of(label), {})[label] = (hi, lo)
    return out


def snapshot(calib: dict | None = None, settle_offset=None) -> dict:
    """Fetch all sources once and return everything the dashboard needs:
    today + tomorrow predictions, the current observation, and per-source
    extremes for both days."""
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    series, obs = gather_series(forecast_days=2)

    obs_times, obs_temps = obs.get("obs", ([], []))
    current = None
    if obs_times:
        current = {"temp": round(obs_temps[-1], 1),
                   "time": obs_times[-1].isoformat(timespec="minutes")}

    return {
        "updated": now.isoformat(timespec="seconds"),
        "today": _predict_from(series, obs, today, now, calib, settle_offset),
        "tomorrow": _predict_from(series, obs, tomorrow, now, calib, settle_offset),
        "current": current,
        "sources": {"today": per_source_extremes(series, today),
                    "tomorrow": per_source_extremes(series, tomorrow)},
    }
