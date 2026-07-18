"""Backtest / calibration scoring.

Replays the distributional pipeline over recent days using archived forecasts
and KDFW actuals, then reports whether the probabilities are *honest*:

  * MAE of the consensus point estimate (sanity).
  * Brier score and CRPS over the bins (lower = sharper + accurate).
  * Interval coverage — of the days, how often the actual fell inside the
    predicted 50% / 80% central intervals. Well-calibrated => ~50% / ~80%.
  * A no-bias-correction baseline, to confirm calibration actually helps.

This validates the core engine (consensus + bias + calibrated spread + binning).
It uses deterministic archived forecasts as input because per-member ensemble
history isn't freely available at scale; the live model adds real ensemble
spread on top, so live sharpness is at least as good as shown here.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import calibration
import model
from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    TIMEZONE, bin_labels)
from model import _bin_probabilities, _MIN_SIGMA
from settlement import bin_for_temp, day_high_low
from sources import open_meteo_models, station_history
from sources.common import to_hourly

_TZ = ZoneInfo(TIMEZONE)

LABELS = bin_labels()


def _label_to_center(label: str) -> float:
    if label.startswith("<="):
        return float(label.split()[-1])
    if label.startswith(">="):
        return float(label.split()[-1])
    return float(label)


def _brier(probs: dict, actual_label: str) -> float:
    return sum((p - (1.0 if lab == actual_label else 0.0)) ** 2
               for lab, p in probs.items())


def _crps(probs: dict, actual: float) -> float:
    """CRPS via the integral of (CDF_pred - 1[x>=actual])^2 over bin centers."""
    cum = 0.0
    total = 0.0
    for lab in LABELS:
        cum += probs[lab]
        x = _label_to_center(lab)
        indicator = 1.0 if x >= actual else 0.0
        total += (cum - indicator) ** 2
    return total


def contract_points(probs: dict, actual: float, variable: str) -> list[tuple]:
    """(predicted_YES, outcome) pairs over the informative part of the ladder.

    For each integer strike where the model's YES probability is non-degenerate
    (between 1% and 99%), pair the predicted probability with whether the
    contract actually resolved YES given the realized `actual` (rounded) extreme.
    These feed the reliability diagram and use the *traded* contract semantics
    via model.prob_for_contract (High = "Greater than T", Low = "Lower than T").
    """
    kind = ">" if variable == "high" else "<"
    pts = []
    for strike in range(BIN_LOW, BIN_HIGH + 1):
        p = model.prob_for_contract(probs, kind, strike)
        if p is None:          # model can't price this strike (inside a tail)
            continue
        if not (0.01 <= p <= 0.99):
            continue
        won = (actual > strike) if variable == "high" else (actual < strike)
        pts.append((p, 1.0 if won else 0.0))
    return pts


def reliability_bins(points: list[tuple], n: int = 10) -> list[dict]:
    """Bin (predicted, outcome) pairs into `n` equal-width probability buckets.

    Returns one row per non-empty bucket: mean predicted probability vs the
    observed hit frequency. A well-calibrated model has predicted ≈ observed.
    """
    buckets: list[list[tuple]] = [[] for _ in range(n)]
    for p, o in points:
        buckets[min(int(p * n), n - 1)].append((p, o))
    out = []
    for b in buckets:
        if not b:
            continue
        out.append({
            "predicted": round(sum(p for p, _ in b) / len(b), 3),
            "observed": round(sum(o for _, o in b) / len(b), 3),
            "n": len(b),
        })
    return out


def _interval_contains(probs: dict, actual_label: str, level: float) -> bool:
    """Does the central `level` mass interval (by cumulative prob) include the
    actual bin?"""
    lo_tail = (1 - level) / 2
    cum = 0.0
    lo_idx = hi_idx = None
    for i, lab in enumerate(LABELS):
        prev = cum
        cum += probs[lab]
        if lo_idx is None and cum > lo_tail:
            lo_idx = i
        if cum >= 1 - lo_tail and hi_idx is None:
            hi_idx = i
            break
    hi_idx = hi_idx if hi_idx is not None else len(LABELS) - 1
    actual_idx = LABELS.index(actual_label)
    return lo_idx <= actual_idx <= hi_idx


def run(days: int = 60, cli: bool = False, settle_offset=None, det_models=None) -> dict:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)
    actual = (station_history.fetch_actual_cli(start, end) if cli
              else station_history.fetch_actual(start, end))
    series = open_meteo_models.fetch_historical(start, end, models=det_models)
    calib = calibration.get(refresh=True) or {}
    bias = calib.get("bias", {}).get("deterministic", {})
    sigma_cfg = calib.get("sigma", {})
    weights_cfg = calib.get("weights") or {}

    bucketed = cli and any(isinstance((settle_offset or {}).get(v), dict)
                           for v in ("high", "low"))
    cond = {}
    if bucketed:
        try:
            cond = open_meteo_models.historical_night_conditions(start, end)
        except Exception:
            cond = {}

    def _offset_for(var, day):
        spec = (settle_offset or {}).get(var) if cli else 0.0
        if isinstance(spec, dict):
            cloud, wind = cond.get(day, (100.0, 100.0))
            b = "clear_calm" if (cloud < CLEAR_CLOUD_MAX and wind < CALM_WIND_MAX) else "other"
            return spec.get(b, 0.0), spec.get(f"{b}_std", 0.0)
        return (spec or 0.0), ((settle_offset or {}).get(f"{var}_std", 0.0) if cli else 0.0)

    metrics = {}
    for var in ("high", "low"):
        sigma_base = max(sigma_cfg.get(var) or 3.0, _MIN_SIGMA)
        rec = {"mae": [], "brier": [], "crps": [], "cov50": [], "cov80": [],
               "mae_base": [], "crps_base": [],
               "exact_peak": [], "exact_consensus": [], "within1": []}
        rel_points: list[tuple] = []
        for day, (act_hi, act_lo) in actual.items():
            act = act_hi if var == "high" else act_lo
            samples, sweights = [], []
            vw = weights_cfg.get(var, {})
            for lab, (t, v) in series.items():
                hi, lo = day_high_low(t, v, day)
                if hi is None:
                    continue
                samples.append(hi if var == "high" else lo)
                sweights.append(vw.get(lab, 1.0))
            if not samples:
                continue
            actual_label = bin_for_temp(act)

            off, gap_std = _offset_for(var, day)
            sigma = math.hypot(sigma_base, gap_std) if gap_std else sigma_base
            corrected = [s - bias.get(var, 0.0) + off for s in samples]
            probs = _bin_probabilities(corrected, sigma, sweights)
            _wsum = sum(sweights) or 1.0
            mu = sum(w * s for w, s in zip(sweights, corrected)) / _wsum
            rec["mae"].append(abs(mu - act))
            rec["brier"].append(_brier(probs, actual_label))
            rec["crps"].append(_crps(probs, act))
            rec["cov50"].append(_interval_contains(probs, actual_label, 0.50))
            rec["cov80"].append(_interval_contains(probs, actual_label, 0.80))
            rel_points.extend(contract_points(probs, act, var))

            # Exact 1°F-bin hit rate — the trader-facing accuracy target. The
            # peak bin is argmax(probs); the consensus bin is round(mu). within1
            # forgives a one-bin (±1°F) miss off the peak.
            peak_label = max(probs, key=probs.get)
            rec["exact_peak"].append(peak_label == actual_label)
            rec["exact_consensus"].append(bin_for_temp(mu) == actual_label)
            rec["within1"].append(
                abs(LABELS.index(peak_label) - LABELS.index(actual_label)) <= 1)

            # Baseline: no bias correction, fixed wide sigma.
            base = _bin_probabilities(samples, 3.0)
            mu0 = sum(samples) / len(samples)
            rec["mae_base"].append(abs(mu0 - act))
            rec["crps_base"].append(_crps(base, act))

        n = len(rec["mae"])
        metrics[var] = {
            "n_days": n,
            "mae": round(sum(rec["mae"]) / n, 2),
            "brier": round(sum(rec["brier"]) / n, 3),
            "crps": round(sum(rec["crps"]) / n, 3),
            "coverage_50": round(100 * sum(rec["cov50"]) / n, 0),
            "coverage_80": round(100 * sum(rec["cov80"]) / n, 0),
            "exact_peak": round(100 * sum(rec["exact_peak"]) / n, 0),
            "exact_consensus": round(100 * sum(rec["exact_consensus"]) / n, 0),
            "within1": round(100 * sum(rec["within1"]) / n, 0),
            "mae_baseline": round(sum(rec["mae_base"]) / n, 2),
            "crps_baseline": round(sum(rec["crps_base"]) / n, 3),
            "reliability": reliability_bins(rel_points),
        }
    return metrics


def run_intraday(days: int = 30, hours=(10, 13, 16, 19), calib=None) -> dict:
    """Same-day exact-bin accuracy by simulated time-of-day, via observation replay.

    The deterministic backtest above is blind to the nowcast: it never feeds live
    observations, so it can't see how same-day accuracy improves as the day plays
    out. This harness fixes that. For each past day it replays the real hourly
    observations through the *actual* same-day path (`model.predict_variable` with
    `now` set), stepping a simulated clock across `hours` and scoring the exact
    (peak) bin against the settlement at each step.

    Limitation (documented): it uses archived *deterministic* forecasts as the
    member set — per-member ensemble history isn't archived — so the pre-anchor
    spread is approximate. That's acceptable here: once observations anchor the
    day the spread collapses toward observation noise (the `locked_ratio`
    mechanism), so the ensemble's contribution to a late-day exact bin is small.
    Returns {hour: {variable: exact_peak_pct}}; an A/B harness for tuning the
    anchoring path, not an absolute live number.
    """
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)
    actual = station_history.fetch_actual(start, end)
    series = open_meteo_models.fetch_historical(start, end)
    obs_times, obs_temps = to_hourly(*station_history._fetch_series(start, end))
    obs = {"obs": (obs_times, obs_temps)}
    calib = calib if calib is not None else (calibration.get(refresh=True) or {})

    hits = {h: {"high": [], "low": []} for h in hours}
    for day, (act_hi, act_lo) in actual.items():
        for h in hours:
            now = datetime(day.year, day.month, day.day, h, tzinfo=_TZ)
            for var, act in (("high", act_hi), ("low", act_lo)):
                out = model.predict_variable(series, obs, day, var, now, calib)
                if not out:
                    continue
                peak = max(out["probabilities"], key=out["probabilities"].get)
                hits[h][var].append(peak == bin_for_temp(act))

    metrics = {}
    for h in hours:
        metrics[h] = {
            var: (round(100 * sum(v) / len(v), 0) if v else None)
            for var, v in hits[h].items()
        }
        metrics[h]["n"] = max(len(hits[h]["high"]), len(hits[h]["low"]))
    return metrics


def _report_intraday(metrics: dict):
    print("\n=== SAME-DAY exact-bin by hour (obs replay) ===")
    for h in sorted(metrics):
        m = metrics[h]
        print(f"  {h:02d}:00 local (n={m['n']:2d})  high={m['high']}%  low={m['low']}%")


def _report(metrics: dict):
    for var, m in metrics.items():
        print(f"\n=== {var.upper()} ({m['n_days']} days) ===")
        print(f"  exact-bin (peak): {m['exact_peak']:.0f}%   "
              f"(consensus {m['exact_consensus']:.0f}%, within±1 {m['within1']:.0f}%)")
        print(f"  consensus MAE   : {m['mae']}°F   (baseline {m['mae_baseline']}°F)")
        print(f"  Brier           : {m['brier']}")
        print(f"  CRPS            : {m['crps']}   (baseline {m['crps_baseline']})")
        print(f"  50% interval cov: {m['coverage_50']:.0f}%   (target ~50%)")
        print(f"  80% interval cov: {m['coverage_80']:.0f}%   (target ~80%)")


if __name__ == "__main__":
    _report(run())
    _report_intraday(run_intraday())
