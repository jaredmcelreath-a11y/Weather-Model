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

import requests

from config import (BIN_HIGH, BIN_LOW, CACHE_TTL_SECONDS, CALM_WIND_MAX,
                    CLEAR_CLOUD_MAX, HIGH_BUMPY_STD, HIGH_LOCK_DROP,
                    HIGH_LOCK_NOON_OFFSET_HOURS, HIGH_PLATEAU_MAX,
                    LEAD_SIGMA_INFLATION, LOW_LOCK_RISE, MAX_CLI_GAP,
                    PEAK_LOCK_DROP, TIMEZONE, bin_labels, lead_bucket)
from settlement import (covers_extreme, local_day_bounds, observed_so_far,
                        observed_so_far_robust, round_half_up,
                        _HIGH_WINDOW, _LOW_WINDOW)
from convective import convective_sigma
import solar
from sources import (open_meteo_ensemble, open_meteo_models, nws_forecast,
                     nws_observations, iem_mos)
from sources.station_history import fetch_actual_cli

TZ = ZoneInfo(TIMEZONE)

# Uncertainty knobs (degrees F, 1-sigma).
_DEFAULT_INFLATION = 1.3   # inflate raw ensemble spread when no calibration yet
_DEFAULT_SIGMA = 2.0       # fallback day-ahead spread when no calibration yet
_MIN_SIGMA = 1.0           # floor on the day-ahead (pure-forecast) spread
_SIGMA_FLOOR = 0.7         # fully-locked residual: observation + rounding noise
_MIN_BANDWIDTH = 0.7       # kernel smoothing bandwidth
# A lone sub-hourly HIGH spike (above the corroborated peak) is trusted only when the
# forecast gave its settled bin at least this probability — a plausible brief peak
# vs a sensor glitch far above the forecast. (The low keeps strict ≥2-corroboration.)
SPIKE_FORECAST_MIN = 0.05


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
    if label.startswith("mos_"):
        return "guidance"
    return "other"


def _past_high_peak_gate(day, now) -> bool:
    """True once `now` is past solar noon + HIGH_LOCK_NOON_OFFSET_HOURS — the time
    of day by which KDFW's afternoon maximum has formed. Used to gate the early
    high lock and the high's CLI offset gate on the seasonally-shifting peak
    instead of a fixed clock hour. False (conservative) if solar noon can't be
    computed."""
    if now is None:
        return False
    try:
        gate = solar.solar_noon(day) + timedelta(hours=HIGH_LOCK_NOON_OFFSET_HOURS)
    except Exception:
        return False
    return now.astimezone(TZ) >= gate


def _retreat_persisted(vals, drop, n=2) -> bool:
    """True when the last `n` readings are all at least `drop` below the running
    max — i.e. the retreat has held, not just a single (possibly convective) dip."""
    if len(vals) < n + 1:
        return False
    m = max(vals)
    return all(m - v >= drop for v in vals[-n:])


def _extreme_locked(times, temps, day, variable, now, drop=PEAK_LOCK_DROP,
                    bumpy=False) -> bool:
    """True once today's extreme has clearly passed.

    The high (or low) is treated as set when the latest observation has retreated
    `drop` °F from the running max (high) or risen `drop` above the running min
    (low). At that point the realized extreme is the answer and the forecast's
    projected further rise/fall is just noise — `_member_extreme` then collapses
    each member to the observed extreme. The drop has to clear observation +
    rounding noise, so a brief dip before a higher peak won't false-lock.
    Only meaningful intraday (now within the day); otherwise False.

    For the high, the running max must also postdate the running min so far
    (temps rose to a peak and came back down). On a warm summer night the
    calendar day's max is leftover heat just after midnight that *precedes* the
    morning minimum — the real daytime peak hasn't started — so retreat from it
    must not lock. The low needs no such guard: its minimum genuinely sits near
    dawn, after the warm start of the calendar day, with no midnight-boundary
    artifact to mistake for it.
    """
    if now is None or drop is None:
        return False
    start, end = local_day_bounds(day)
    if not (start <= now < end):
        return False
    vals = []
    for t, v in zip(times, temps):
        if v is None:
            continue
        t = t.astimezone(TZ)
        if start <= t <= now and t < end:
            vals.append(v)
    if len(vals) < 3:
        return False
    cur = vals[-1]
    if variable == "high":
        # The peak must postdate the trough; an earlier max is just overnight
        # heat ahead of the morning minimum, not a passed daytime peak.
        if vals.index(max(vals)) <= vals.index(min(vals)):
            return False
        max_i = vals.index(max(vals))
        retreat = max(vals) - cur
        # Blunt 2°F fallback. On a bumpy (convective) afternoon require the retreat
        # to persist across a second reading, so a lone dip before a higher peak
        # can't false-lock; a calm afternoon locks on the first reading as before.
        if retreat >= drop and (not bumpy or _retreat_persisted(vals, drop)):
            return True
        # Past the afternoon gate the daytime max is in. Lock when we're off the
        # peak (small confirming retreat, clears obs/rounding jitter) OR when the
        # high has plateaued — the max was set at an earlier reading and we're
        # holding within HIGH_PLATEAU_MAX of it without a new high. The plateau case
        # locks a flat-topped peak while the market's still live instead of waiting
        # for it to fall. Nothing sets a new daytime max after this window.
        if _past_high_peak_gate(day, now):
            if retreat >= HIGH_LOCK_DROP:
                return True
            if max_i < len(vals) - 1 and retreat <= HIGH_PLATEAU_MAX:
                return True
        return False
    risen = cur - min(vals)
    if risen >= drop:
        return True
    # Early lock: past sunrise the dawn minimum is behind us; a small confirming
    # rise (clears obs + rounding jitter) means we're off the trough. The margin
    # naturally waits for a min that lands shortly after sunrise, since temps are
    # still falling toward it until then (risen <= 0).
    try:
        sr = solar.sunrise(day)
    except Exception:
        return False
    return now.astimezone(TZ) >= sr and risen >= LOW_LOCK_RISE


def _anchor_obs_now(recent):
    """Live sub-hourly anchor for the forecast offset: the mean of the last 4 readings
    (~20 min). The offset `(obs_now - fc_now)` shifts the WHOLE remaining forecast 1:1,
    so a noisy anchor swings the projected peak (and the consensus) even while the temp
    is flat; a 20-min mean damps the whole-°C feed jitter that drives it. Backtested
    2026-07-09 (57 days, afternoon betting times): exact-bin 36.1%->39.8% and step-swing
    sd 1.12->0.85 vs the old median-of-3 — better on both. 20 min is the sweet spot;
    25 min (median-5) over-smooths and loses accuracy."""
    w = recent[-4:]
    return sum(w) / len(w)


def _member_extreme(times, temps, day, variable, now, observed, obs_now=None,
                    locked=False):
    """One member's contribution to the high/low sample for `day`.

    For today, blends the realized extreme with the member's forecast over the
    *remaining* hours. Crucially, the remaining forecast is anchored to the
    latest observation: if this member currently reads N° off from reality, its
    remaining hours are shifted by that error before taking the extreme — so once
    the peak has passed and temps are falling, the model follows reality down
    instead of trusting a stale, too-warm forecast. Future days: full-day extreme.

    When `locked` (the extreme has demonstrably passed — see `_extreme_locked`),
    the realized extreme supersedes the forecast entirely: return `observed`, so
    a forecast still projecting more rise/fall can't push past what already happened.
    """
    start, end = local_day_bounds(day)
    day_vals, remaining = [], []
    # Bracket the forecast around `now` so the anchor can be interpolated to the
    # exact time. Snapping fc_now to the last whole hour made it a step function
    # that jumped at the top of each hour while the observation anchor hadn't yet
    # updated — collapsing the offset and dropping the projected extreme (the
    # sawtooth dip visible on the consensus at :00-:01 during the morning climb).
    lo_t = lo_v = hi_t = hi_v = None
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
                if hi_t is None:            # first forecast hour after now
                    hi_t, hi_v = t, v
            else:
                lo_t, lo_v = t, v           # ascending -> latest hour <= now
    if not day_vals:
        return None

    # Forecast interpolated to `now` (linear between the bracketing hours); falls
    # back to whichever bound exists at the ends of the forecast window.
    if lo_v is not None and hi_v is not None and hi_t > lo_t:
        frac = (now - lo_t).total_seconds() / (hi_t - lo_t).total_seconds()
        fc_now = lo_v + (hi_v - lo_v) * frac
    else:
        fc_now = lo_v if lo_v is not None else hi_v

    is_today = now is not None and start <= now < end
    if not is_today:
        # Pure / full-day reference: a source that never saw this extreme's
        # occurrence window (a now-forward feed on the current day) abstains
        # rather than reporting the wrong tail of the day.
        if not covers_extreme(times, temps, day, variable):
            return None
        return max(day_vals) if variable == "high" else min(day_vals)

    # Extreme already passed: the realized value is the answer; ignore the
    # forecast's projected further rise/fall.
    if locked and observed is not None:
        return observed

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
    m = sum(1 for label in series if label.startswith("ens_")) or 1
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
                     weights=None, locked=False):
    """(values, weights) lists of daily extremes for `day`.

    Bias correction applies only to pure forecasts (skipped while anchoring to a
    live obs). `weights` is an optional {system: weight} map; when absent every
    sample weighs 1.0 (identical to the old equal-weight behavior). `locked`
    collapses each member to the realized extreme once the day's peak/trough has
    passed (see `_extreme_locked`).
    """
    anchoring = obs_now is not None
    wmap = _sample_weights(series, weights) if weights else None
    vals, ws = [], []
    for label, (times, temps) in series.items():
        val = _member_extreme(times, temps, day, variable, now, observed, obs_now,
                              locked=locked)
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


def _trusted_high_max(c_raw, c_robust, fullday, shift):
    """Which continuous HIGH extreme to trust. A lone spike (`c_raw`, above the
    corroborated `c_robust`) is accepted only when the forecast (`fullday`, shifted
    by `shift` to the settlement basis) gave the spike's settled bin at least
    SPIKE_FORECAST_MIN probability — so a real brief peak counts, but a sensor glitch
    far above the forecast doesn't. No lone spike (c_raw <= c_robust) → c_raw."""
    if c_raw is None:
        return c_robust
    if c_robust is None or c_raw <= c_robust:
        return c_raw                       # no lone spike above the corroborated peak
    if not fullday:
        return c_robust
    spike_bin = round_half_up(c_raw)
    support = sum(1 for s in fullday if round_half_up(s + shift) >= spike_bin) / len(fullday)
    return c_raw if support >= SPIKE_FORECAST_MIN else c_robust


def predict_variable(series, obs_series, day, variable, now, calib,
                     settle_offset=None, live=False):
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

    # 'bumpy' (set from the sub-hourly feed below) makes the high's blunt 2°F
    # peak-lock wait for a second confirming reading on a convective afternoon.
    bumpy = False

    # Pure-forecast full-day samples (no obs): the reference spread, and the sanity
    # check for whether a lone continuous high spike is plausible (below).
    bias = (calib or {}).get("bias", {})
    fullday, _fw = _collect_samples(series, day, variable, None, None, bias)

    # Continuous (sub-hourly) observed extreme for the hard bound only: the live
    # 5-minute feed can catch a brief spike the routine :53 reading missed. Reads
    # are whole-°C, so haircut by half a °C (0.9°F) — the bound only tightens past
    # the hourly observed on a genuine spike, never on quantization noise. Fed to
    # the hard bound (not the sample floor) so it can't double-count the CLI offset.
    observed_bound = observed
    observed_cont = None  # sub-hourly extreme that DRIVES the bound/anchor (guarded)
    observed_cont_display = None  # sub-hourly extreme to SHOW (the raw dip/peak the
    # feed touched) — mirrors the high's spike on the low so the shown number doesn't
    # contradict the consensus; never feeds prediction (display-only).
    cont_times, cont_temps = obs_series.get("obs_continuous", (None, None))
    if cont_times and now is not None:
        # Spike-robust: the 5-min feed can report a lone reading a whole °C off
        # (a false high/low), which would wrongly tighten the bound and anchor.
        # High: Kalshi settles on the raw CLI daily max, so a lone sub-hourly spike is
        # trusted (min_support=1) — but only when the forecast gave its settled bin
        # non-trivial probability (a plausible brief peak, not a sensor glitch); else
        # fall back to the ≥2-corroborated peak. The low always keeps corroboration so
        # a lone cold blip on a convective afternoon can't wrongly lock it.
        c_max_raw, c_min_raw = observed_so_far_robust(cont_times, cont_temps, day, now, min_support=1)
        c_max_rob, c_min = observed_so_far_robust(cont_times, cont_temps, day, now)
        if variable == "high":
            shift = (settle_offset or {}).get("high", 0.0)
            c_max = _trusted_high_max(c_max_raw, c_max_rob, fullday, shift)
            if c_max is not None:
                observed_cont = c_max
                observed_cont_display = c_max  # high already shows its trusted spike
                cand = c_max - 0.9
                observed_bound = cand if observed is None else max(observed, cand)
        elif variable == "low" and c_min is not None:
            observed_cont = c_min           # behavior: guarded (≥2-corroborated) min
            observed_cont_display = c_min_raw if c_min_raw is not None else c_min  # show the raw dip
            cand = c_min + 0.9
            observed_bound = cand if observed is None else min(observed, cand)
        # Anchor the nowcast to the live sub-hourly reading (a ~20-min trailing mean,
        # see _anchor_obs_now) so the forecast offset tracks the real temperature
        # continuously without swinging on whole-°C feed jitter. The hourly :53-stepped
        # anchor only updated once an hour, which — against the now-interpolated
        # forecast — left a residual intraday ramp; the continuous anchor flattens it.
        d_start, d_end = local_day_bounds(day)
        recent = [v for t, v in zip(cont_times, cont_temps)
                  if v is not None and d_start <= t.astimezone(TZ) <= now < d_end]
        if recent:
            obs_now = _anchor_obs_now(recent)
        # Bumpy afternoon = the recent sub-hourly readings are swinging (convective
        # clouds) — the signature that a single hourly dip may not be the real peak.
        if len(recent) >= 4:
            bumpy = _std(recent[-6:]) > HIGH_BUMPY_STD

    # Once the day's extreme has clearly passed, lock to what was realized.
    locked = _extreme_locked(obs_times, obs_temps, day, variable, now, bumpy=bumpy) \
        if now is not None else False

    # Full-day extremes (ignoring obs) set the reference spread; nowcast-blended
    # samples carry the realized floor/ceiling and forecast anchored to obs_now.
    var_weights = (calib or {}).get("weights", {}).get(variable)
    samples, weights = _collect_samples(series, day, variable, now, observed, bias,
                                        obs_now, var_weights, locked=locked)
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

    # Warm-night regime for the low bias correction, judged on the forecast
    # consensus BEFORE the CLI settle-shift so the shift can't blur the threshold.
    # Uses the same weighted mean the reported consensus uses.
    regime_low = (sum(w * s for w, s in zip(weights, samples)) / (sum(weights) or 1.0)) \
        if samples else None

    # Kalshi settlement basis: shift the forecast distribution to the CLI basis
    # by a calibrated per-variable offset. Applied to the forecast samples only,
    # NOT the hard observed bound (the offset is an average gap, not a floor) —
    # so consensus/bins move but still-possible bins are not zeroed. A constant
    # shift leaves sigma and locked_ratio unchanged. None => Robinhood, no shift.
    settle_shift, settle_gap_std = _offset_bucket(settle_offset, variable, day, calib)
    # Locked + continuous extreme observed: the CLI settlement value is directly
    # measured. observed_cont already includes any real sub-hourly spike the
    # average offset is meant to approximate, so anchor on the OBSERVED gap
    # (continuous − hourly) and drop the gap-std widening — instead of layering
    # the average offset and its spread on top, which double-counts and (for the
    # high) pushes mass above the realized peak into impossible bins. Applies to
    # high and low alike. Pure-forecast / unlocked days keep the average offset.
    #
    # The high also takes the measured gap once its peak is *realized but not yet
    # locked*: past the solar-noon peak gate the afternoon max is in even if the
    # temp is sitting on a plateau and hasn't retreated enough to lock. Without
    # this the flat +offset gets layered onto an already-continuous observed peak,
    # printing ~1°F too hot in the late-afternoon window (the "95.9 at 6pm when
    # it's 95" bug). The low keeps the strict `locked` gate — its downside is real.
    if settle_offset is not None and observed is not None:
        if variable == "high":
            # Unchanged: once the peak is locked or past the solar-noon gate and
            # the continuous peak is observed, anchor on the measured gap.
            if (locked or _past_high_peak_gate(day, now)) and observed_cont is not None:
                settle_shift = observed_cont - observed
                settle_gap_std = 0.0
        else:
            # Low, CLI basis: prefer the whole-°F daily-summary CLI min (the exact
            # Kalshi settlement variable) over the whole-°C 5-min feed. Use the
            # MEASURED gap instead of the flat average offset. A not-yet-locked low
            # anchors only on the authoritative daily-summary, only when it tightens
            # the low downward (gap < 0), and only `live` (backtest must not get the
            # settled value as lookahead). A locked low keeps its measured gap even
            # at gap == 0 (the settled value must beat the average offset).
            cli_daily = obs_series.get("cli_daily", {}).get(day)
            cli_low = cli_daily[1] if cli_daily else observed_cont
            if cli_low is not None:
                gap = cli_low - observed
                trust = (-MAX_CLI_GAP <= gap <= 0) if locked \
                    else (live and cli_daily is not None and -MAX_CLI_GAP <= gap < 0)
                if trust:
                    settle_shift = gap
                    settle_gap_std = 0.0
    fullday_mean = sum(fullday) / len(fullday)  # unshifted (hourly-basis) forecast center
    if settle_shift:
        samples = [s + settle_shift for s in samples]
        fullday = [s + settle_shift for s in fullday]

    calib_sigma = (calib or {}).get("sigma", {}).get(variable)
    sigma_day_ahead = _day_ahead_sigma(fullday, calib_sigma)
    fullday_sd = _std(fullday)
    locked_ratio = min(1.0, _std(samples) / fullday_sd) if fullday_sd > 1e-6 else 0.0

    # Monotonic "Resolved" for display (distinct from locked_ratio, which scales
    # sigma). Built from ONLY monotonic inputs so it never falls through the day:
    #   * the hard bound — observed_so_far only ratchets (up for the high, down for the
    #     low), so Phi((observed - mean)/sigma) = the pure-forecast mass already ruled
    #     out only ever grows; and
    #   * time through the extreme's window — so a peak landing near the forecast still
    #     resolves toward 100% as the window closes.
    # Deliberately NOT tied to `locked`: that flag flickers (a late peak that exceeds an
    # early false-lock un-locks it), which is exactly what dropped Resolved mid-day. The
    # noisy `locked_ratio` (momentary ensemble agreement) is not used here either.
    resolved = 0.0
    if observed is not None and now is not None and fullday_sd > 1e-6:
        below = _norm_cdf(observed, fullday_mean, fullday_sd)
        collapse = below if variable == "high" else 1.0 - below
        w0, w1 = _HIGH_WINDOW if variable == "high" else _LOW_WINDOW
        lt = now.astimezone(TZ)
        hr = lt.hour + lt.minute / 60.0
        tprog = min(1.0, max(0.0, (hr - w0) / (w1 - w0)))
        resolved = 1.0 - (1.0 - collapse) * (1.0 - tprog)

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

    # Lead-time residual de-bias (self-correction layer): the forward log measures
    # a persistent signed error for this (lead, variable). Subtract it from the
    # forecast samples so both the consensus and the bin mass shift together.
    # Pure-forecast path only (obs_now None) — once obs anchor the day the realized
    # extreme supersedes a forecast bias, exactly like the cooling offset.
    bias_corr = (calib or {}).get("bias_correction", {}).get("by_lead", {})
    bc = (bias_corr.get(str(bucket)) or bias_corr.get(bucket) or {}).get(variable)
    if bc and obs_now is None:
        samples = [s - bc for s in samples]

    # Warm-night low de-bias: on warm forecast nights the consensus runs cold in
    # a way the flat bias misses (warm/cool leans cancel). Add it back. Pure-
    # forecast low path only; regime judged pre-settle-shift (see regime_low).
    wl = (calib or {}).get("bias_correction", {}).get("warm_low") or {}
    if (wl and variable == "low" and obs_now is None
            and regime_low is not None and regime_low >= wl["threshold"]):
        samples = [s - wl["bias"] for s in samples]

    # The CLI settlement offset is an average; its gap has irreducible spread
    # (std from calibration) we can't observe live, so widen sigma by it in
    # quadrature whenever the offset is applied. Center (consensus) is unchanged.
    # The locked + continuous-observed case has already zeroed settle_gap_std
    # above (the gap is measured, not estimated), so it skips this widening.
    if settle_gap_std:
        sigma = math.hypot(sigma, settle_gap_std)

    # Convective downside humility: on a storm-risk day the smooth fields can't
    # see an evening downdraft, so a locked low collapses sigma to ~0.7 and
    # over-reports confidence. Floor the spread for *today's low only* at a sigma
    # scaled by how likely storms actually are (POP, or full on an upstream severe
    # warning — see convective.convective_sigma); the hard bound below then makes
    # the extra spread one-sided (downside). Best-effort and floor-only: it never
    # lowers sigma, shifts the mean, or touches the high/tomorrow. Storm-free days
    # return 0 and never widen. Gated on `live`: it reads live POP and live
    # alerts, so it must not fire in backtest/replay (which calls this with a
    # today-relative `now` on a past day).
    convective_widened = False
    if live and variable == "low" and now is not None and lead_bucket(now, day) == 0:
        try:
            conv_sigma = convective_sigma(day, now)
            if conv_sigma > 0:
                sigma = max(sigma, conv_sigma)
                convective_widened = True
        except Exception:
            pass

    probs = _bin_probabilities(samples, sigma, weights)
    probs = _apply_hard_bound(probs, variable, observed_bound)

    # Reported consensus = the same skill-weighted mean used to center the bins.
    _w = sum(weights) or 1.0
    mean = sum(w * s for w, s in zip(weights, samples)) / _w
    return {
        "probabilities": probs,
        "consensus": round(mean, 1),
        "sample_spread": round(_std(samples), 1),
        "sigma_used": round(sigma, 1),
        "locked_ratio": round(locked_ratio, 2),
        "resolved": round(resolved, 2),
        "n_samples": len(samples),
        "observed_so_far": observed,
        "observed_continuous": observed_cont,
        "observed_continuous_display": observed_cont_display,
        "cooling_applied": cooling_applied,
        "peak_locked": locked,
        "convective_widened": convective_widened,
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


def _fetch_cli_daily(day: date) -> dict:
    """{date: (max_f, min_f)} from the IEM daily summary for `day`, or {} on any
    failure. Best-effort: the CLI daily min is a live *anchor* for the Kalshi low
    (see predict_variable), never a settlement floor — a miss just falls back to
    the hourly/average-offset path."""
    try:
        return fetch_actual_cli(day, day, ttl=CACHE_TTL_SECONDS)
    except Exception:
        return {}


def gather_series(forecast_days: int = 2, continuous_obs: bool = False,
                  now: datetime | None = None):
    """All forecast series merged into one dict, plus the obs series.

    `continuous_obs` adds the sub-hourly observation feed (for the CLI basis's
    spike-aware hard bound); the default hourly obs is always present. When set,
    it also attaches today's daily-summary CLI extremes under the `cli_daily` key
    of the returned obs dict (best-effort — see `_fetch_cli_daily`). `now` ties
    the observation window to the caller's clock so the full settlement day is in
    view (and the two agree across a midnight rollover).
    """
    series = {}
    dropped = []
    forecast_sources = [
        ("open-meteo ensemble", lambda: open_meteo_ensemble.fetch(forecast_days)),
        ("open-meteo models", lambda: open_meteo_models.fetch(forecast_days)),
        ("nws forecast", lambda: nws_forecast.fetch()),
        ("iem mos", lambda: iem_mos.fetch(forecast_days)),
    ]
    for label, fetch in forecast_sources:
        try:
            series.update(fetch())
        except requests.exceptions.RequestException:
            # A slow/dead upstream is dropped so the consensus runs on the
            # remaining models instead of taking the whole page down.
            dropped.append(label)
    # Observations are the settlement anchor — not degradable; let it raise.
    obs = nws_observations.fetch(continuous=continuous_obs, now=now)
    # CLI basis only (Kalshi): the whole-°F daily-summary min anchors today's low
    # (predict_variable). Best-effort — a miss falls back to the hourly path.
    if continuous_obs:
        obs["cli_daily"] = _fetch_cli_daily((now or datetime.now(TZ)).date())
    return series, obs, dropped


def predict(day: date, now: datetime | None = None, calib: dict | None = None,
            forecast_days: int = 2, settle_offset=None) -> dict:
    """Full prediction (high + low) for `day`. `now` enables the nowcast blend
    when `day` is today; pass None to force a pure forecast."""
    if now is None:
        now = datetime.now(TZ)
    series, obs, _dropped = gather_series(forecast_days)
    return _predict_from(series, obs, day, now, calib, settle_offset, live=True)


def _predict_from(series, obs, day, now, calib, settle_offset=None, live=False):
    return {
        "day": day.isoformat(),
        "high": predict_variable(series, obs, day, "high", now, calib, settle_offset, live=live),
        "low": predict_variable(series, obs, day, "low", now, calib, settle_offset, live=live),
    }


def per_source_extremes(series, day):
    """{group: {label: (high, low)}} for the source-transparency panel."""
    from settlement import day_high_low
    out: dict[str, dict[str, tuple]] = {}
    for label, (times, temps) in series.items():
        hi, lo = day_high_low(times, temps, day)
        # Null an extreme the source never observed (now-forward feed on the
        # current day), so the panel doesn't show a spurious evening 'low'.
        if not covers_extreme(times, temps, day, "high"):
            hi = None
        if not covers_extreme(times, temps, day, "low"):
            lo = None
        if hi is None and lo is None:
            continue
        out.setdefault(_group_of(label), {})[label] = (hi, lo)
    return out


def snapshot(calib: dict | None = None, settle_offset=None,
             continuous_obs: bool = False) -> dict:
    """Fetch all sources once and return everything the dashboard needs:
    today + tomorrow predictions, the current observation, and per-source
    extremes for both days. `continuous_obs` enables the CLI basis's sub-hourly
    spike-aware hard bound (passed by the Kalshi page)."""
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    series, obs, dropped = gather_series(
        forecast_days=2, continuous_obs=continuous_obs, now=now)

    obs_times, obs_temps = obs.get("obs", ([], []))
    # Latest routine hourly (:53 METAR) reading, kept alongside the live value so
    # the dashboard can still surface the precise hourly temp under Current Temp.
    current_hourly = None
    if obs_times:
        current_hourly = {"temp": round(obs_temps[-1], 1),
                          "time": obs_times[-1].isoformat(timespec="minutes")}
    # Prefer the sub-hourly (~5-min) feed for the live 'current' reading so it
    # refreshes every few minutes instead of only at the routine :53 METAR; fall
    # back to the hourly series when the continuous feed isn't fetched.
    cont = obs.get("obs_continuous")
    if cont and cont[0]:
        cont_times, cont_temps = cont
        current = {"temp": round(cont_temps[-1], 1),
                   "time": cont_times[-1].isoformat(timespec="minutes")}
    else:
        current = current_hourly

    return {
        "updated": now.isoformat(timespec="seconds"),
        "today": _predict_from(series, obs, today, now, calib, settle_offset, live=True),
        "tomorrow": _predict_from(series, obs, tomorrow, now, calib, settle_offset, live=True),
        "current": current,
        "current_hourly": current_hourly,
        "sources": {"today": per_source_extremes(series, today),
                    "tomorrow": per_source_extremes(series, tomorrow)},
        "dropped_sources": dropped,
    }
