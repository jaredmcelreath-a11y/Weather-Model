"""Score the model's own logged predictions against actual KDFW settlements.

This is the live, forward-looking complement to backtest.py: where backtest
replays a simplified pipeline over archived forecasts, this grades exactly what
the dashboard showed (full ensemble + nowcast + every correction), once each
target day has settled. It powers the "Model accuracy" panel and, once enough
days accumulate, feeds empirical per-lead-time spread back into calibration.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

import forecast_log
from backtest import contract_points, reliability_bins, _brier
from model import bin_temp
from config import CALIBRATION_WINDOW_DAYS
from settlement import bin_for_temp
from sources import station_history

# Minimum settled days for a (lead, variable) before we trust its empirical sigma.
MIN_LEAD_DAYS = 10
# Self-correction tuning. Shrink a measured bias toward zero by n/(n+SHRINK_K)
# so a noisy short sample is damped and a persistent bias strengthens with data;
# only correct when the bias clears SIG_Z standard errors (distinguishable from 0).
SHRINK_K = 8
SIG_Z = 1.0

# SE(median) ≈ 1.2533 × sd/√n under approximate normality. The bias gate uses
# it because the estimator is now a median: keeping the mean's SE would make
# the significance test quietly easier to pass, the wrong direction.
MEDIAN_SE_FACTOR = 1.2533


def _flagged(rec: dict) -> bool:
    """True when the record was captured under a live storm/front regime — the
    convective floor or front guard was active, so its residual belongs to a
    conditional regime the live model already widens for, not calm-day skill."""
    return bool(rec.get("convective_widened") or rec.get("front_widened"))


def _correction_residuals(today: date | None = None, basis: str = "hourly"
                          ) -> dict[tuple, list[float]]:
    """{(lead_bucket, variable): [signed consensus errors]} for the correction
    estimators.

    Unlike the all-time scoreboard (score()), this pool is windowed to the last
    CALIBRATION_WINDOW_DAYS — so both calibration loops age at the same rate and
    stale regimes/outliers fall out on their own — and drops storm/front-flagged
    records (see _flagged). Records without a consensus contribute nothing.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=CALIBRATION_WINDOW_DAYS)
    records = [r for r in _settled_records(today)
               if r.get("basis", "hourly") == basis
               and date.fromisoformat(r["target_date"]) >= cutoff
               and not _flagged(r)
               and r.get("consensus") is not None]
    if not records:
        return {}
    actual = _actuals_for(records, basis)
    out: dict[tuple, list[float]] = {}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        act = actual[d][0] if r["variable"] == "high" else actual[d][1]
        out.setdefault((r["lead_bucket"], r["variable"]), []).append(r["consensus"] - act)
    return out


def _settled_records(today: date | None = None, cohort: str | None = None) -> list[dict]:
    """Settled logged rows. `cohort` selects the capture cohort: the default None
    returns the rolling rows (no capture_cohort) — so score(), the correction
    residuals, and per_lead_sigma/bias all keep using the rolling captures and the
    fixed-time cohort rows never pollute them. Pass a cohort id (e.g. "0900") for
    that cohort's rows only."""
    rows = forecast_log.load()
    today = today or date.today()
    return [r for r in rows
            if date.fromisoformat(r["target_date"]) < today
            and r.get("capture_cohort") == cohort]


def _actuals_for(records: list[dict], basis: str = "hourly") -> dict[date, tuple[float, float]]:
    if not records:
        return {}
    days = [date.fromisoformat(r["target_date"]) for r in records]
    fetch = (station_history.fetch_actual_cli if basis == "cli"
             else station_history.fetch_actual)
    return fetch(min(days), max(days))


def same_day_cohort(today: date | None = None, basis: str = "hourly",
                    cohort: str = "0900") -> dict:
    """Exact-bin accuracy of the fixed-time same-day cohort — an honest decision-
    time same-day number, since the rolling lead-0 row is dominated by the
    ~11:45pm capture (the day is already settled by then). {variable: {n,
    exact_peak, exact_consensus, within1}} for the settled cohort rows; {} until
    any settle."""
    records = [r for r in _settled_records(today, cohort=cohort)
               if r.get("basis", "hourly") == basis]
    if not records:
        return {}
    actual = _actuals_for(records, basis)
    if not actual:
        return {}
    hits: dict[str, dict[str, list[bool]]] = {
        "high": {"peak": [], "consensus": [], "within1": []},
        "low": {"peak": [], "consensus": [], "within1": []}}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        var = r["variable"]
        act = actual[d][0] if var == "high" else actual[d][1]
        probs = r["probabilities"]
        actual_label = bin_for_temp(act)
        peak_label = max(probs, key=probs.get)
        hits[var]["peak"].append(peak_label == actual_label)
        hits[var]["within1"].append(abs(bin_temp(peak_label) - bin_temp(actual_label)) <= 1)
        if r.get("consensus") is not None:
            hits[var]["consensus"].append(bin_for_temp(r["consensus"]) == actual_label)

    def _pct(flags: list[bool]) -> float | None:
        return round(100 * sum(flags) / len(flags), 0) if flags else None

    out = {}
    for var in ("high", "low"):
        if hits[var]["peak"]:
            out[var] = {"n": len(hits[var]["peak"]),
                        "exact_peak": _pct(hits[var]["peak"]),
                        "exact_consensus": _pct(hits[var]["consensus"]),
                        "within1": _pct(hits[var]["within1"])}
    return out


def score(today: date | None = None, basis: str = "hourly") -> dict:
    """Grade all settled logged predictions.

    Returns per-variable Brier + reliability curve, per-(lead, variable) signed-
    error stats, and the fixed-time same-day cohort (same_day_0900). Empty/
    unsettled log -> zeroed structure (never raises on no data; network errors
    during the actuals fetch propagate to the caller).
    """
    records = [r for r in _settled_records(today)
               if r.get("basis", "hourly") == basis]
    empty = {"n_settled": 0, "by_variable": {}, "by_lead": {}, "same_day_0900": {}}
    if not records:
        return empty
    actual = _actuals_for(records, basis)
    if not actual:
        return empty

    var_points: dict[str, list[tuple]] = {"high": [], "low": []}
    var_brier: dict[str, list[float]] = {"high": [], "low": []}
    # Exact 1°F-bin hits per variable and per (lead, variable). Each entry is a
    # list of bools (peak-bin / consensus-bin hit, ±1-bin near miss).
    var_hits: dict[str, dict[str, list[bool]]] = {
        "high": {"peak": [], "consensus": [], "within1": []},
        "low": {"peak": [], "consensus": [], "within1": []}}
    lead_hits: dict[tuple, dict[str, list[bool]]] = {}
    lead_resid: dict[tuple, list[float]] = {}  # (bucket, variable) -> signed errors
    n_settled = 0

    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        var = r["variable"]
        act = actual[d][0] if var == "high" else actual[d][1]
        probs = r["probabilities"]
        actual_label = bin_for_temp(act)
        var_brier[var].append(_brier(probs, actual_label))
        var_points[var].extend(contract_points(probs, act, var))

        peak_label = max(probs, key=probs.get)
        peak_hit = peak_label == actual_label
        within1 = abs(bin_temp(peak_label) - bin_temp(actual_label)) <= 1
        lh = lead_hits.setdefault((r["lead_bucket"], var),
                                  {"peak": [], "consensus": [], "within1": []})
        for store in (var_hits[var], lh):
            store["peak"].append(peak_hit)
            store["within1"].append(within1)
        if r.get("consensus") is not None:
            cons_hit = bin_for_temp(r["consensus"]) == actual_label
            var_hits[var]["consensus"].append(cons_hit)
            lh["consensus"].append(cons_hit)
            lead_resid.setdefault((r["lead_bucket"], var), []).append(r["consensus"] - act)
        n_settled += 1

    def _pct(flags: list[bool]) -> float | None:
        return round(100 * sum(flags) / len(flags), 0) if flags else None

    by_variable = {}
    for var in ("high", "low"):
        if not var_brier[var]:
            continue
        by_variable[var] = {
            "n": len(var_brier[var]),
            "brier": round(sum(var_brier[var]) / len(var_brier[var]), 3),
            "reliability": reliability_bins(var_points[var]),
            "exact_peak": _pct(var_hits[var]["peak"]),
            "exact_consensus": _pct(var_hits[var]["consensus"]),
            "within1": _pct(var_hits[var]["within1"]),
        }

    by_lead = {}
    for (bucket, var), hits in lead_hits.items():
        errs = lead_resid.get((bucket, var), [])
        entry = {
            "n": len(hits["peak"]),
            "exact_peak": _pct(hits["peak"]),
            "exact_consensus": _pct(hits["consensus"]),
            "within1": _pct(hits["within1"]),
        }
        if errs:
            b = sum(errs) / len(errs)
            entry["bias"] = round(b, 2)
            entry["sigma"] = round(math.sqrt(sum((e - b) ** 2 for e in errs) / len(errs)), 2)
            entry["n_resid"] = len(errs)
        by_lead.setdefault(bucket, {})[var] = entry

    return {"n_settled": n_settled, "by_variable": by_variable, "by_lead": by_lead,
            "same_day_0900": same_day_cohort(today, basis)}


def market_accuracy(today: date | None = None) -> dict:
    """Compare the logged Kalshi market forecast to the model, vs CLI settlement.

    For every settled CLI record that carries a logged `market` block, score the
    market's implied expected temperature and the model's consensus as point
    forecasts against the actual settlement. Returns per-variable MAE for each
    plus how often the market was the closer of the two — the empirical answer to
    'how much should the market influence us'. Empty until market-tagged records
    settle (one day's lead after the logging ships).
    """
    records = [r for r in _settled_records(today)
               if r.get("basis") == "cli" and r.get("market")
               and r.get("consensus") is not None]
    out = {"n": 0, "by_variable": {}}
    if not records:
        return out
    actual = _actuals_for(records, "cli")
    if not actual:
        return out

    agg: dict[str, dict] = {}
    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        var = r["variable"]
        act = actual[d][0] if var == "high" else actual[d][1]
        ev = r["market"].get("ev")
        if ev is None:
            continue
        a = agg.setdefault(var, {"m_err": [], "k_err": [], "k_win": []})
        m_err = abs(r["consensus"] - act)
        k_err = abs(ev - act)
        a["m_err"].append(m_err)
        a["k_err"].append(k_err)
        a["k_win"].append(k_err < m_err)
        out["n"] += 1

    for var, a in agg.items():
        n = len(a["m_err"])
        out["by_variable"][var] = {
            "n": n,
            "model_mae": round(sum(a["m_err"]) / n, 2),
            "market_mae": round(sum(a["k_err"]) / n, 2),
            "market_closer_pct": round(100 * sum(a["k_win"]) / n, 0),
        }
    return out


def per_lead_sigma(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                   basis: str = "hourly") -> dict:
    """{lead_bucket: {variable: sigma}} for buckets with enough settled days.

    An honest std over the correction pool (_correction_residuals: windowed to
    CALIBRATION_WINDOW_DAYS, storm/front-flagged records dropped). Deliberately
    NOT a robust scale estimator: a day-ahead miss on a day that *turned out*
    stormy is legitimate lead-time uncertainty and stays in — the flags only
    ever mark same-day locked records, so this falls out naturally. Buckets
    below `min_days` are omitted, so the model keeps falling back to the static
    inflation there. `basis` selects the settlement cohort (the live site is CLI).
    """
    out: dict[int, dict[str, float]] = {}
    for (bucket, var), errs in _correction_residuals(today, basis).items():
        if len(errs) < min_days:
            continue
        m = sum(errs) / len(errs)
        sigma = math.sqrt(sum((e - m) ** 2 for e in errs) / len(errs))
        out.setdefault(int(bucket), {})[var] = round(sigma, 2)
    return out


def per_lead_bias(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                  basis: str = "hourly") -> dict[int, dict[str, float]]:
    """{lead_bucket: {variable: correction}} signed bias to SUBTRACT from the
    consensus, for buckets the data can speak to.

    The point estimate is the MEDIAN of the correction pool (windowed +
    flag-excluded, see _correction_residuals), not the mean: three storm-night
    outliers once manufactured a lead-0 low correction the median correctly
    reads as zero, and the median also damps any regime day the flags missed.
    The guards keep their shape: >= min_days pool records, significance
    |median| > SIG_Z * MEDIAN_SE_FACTOR * sd/sqrt(n) (the median's own standard
    error), and shrinkage toward zero by n/(n+SHRINK_K). Omitted buckets =>
    the model applies no correction there.
    """
    out: dict[int, dict[str, float]] = {}
    for (bucket, var), errs in _correction_residuals(today, basis).items():
        n = len(errs)
        if n < min_days:
            continue
        med = statistics.median(errs)
        m = sum(errs) / n
        sd = math.sqrt(sum((e - m) ** 2 for e in errs) / n)
        se = MEDIAN_SE_FACTOR * sd / math.sqrt(n)
        if abs(med) <= SIG_Z * se:
            continue  # statistically indistinguishable from zero
        out.setdefault(int(bucket), {})[var] = round(med * n / (n + SHRINK_K), 2)
    return out


def correction_exclusions(today: date | None = None, basis: str = "cli") -> int:
    """How many settled records inside the correction window were dropped for a
    storm/front flag — the dashboard shows this next to the active corrections
    so an exclusion is visible instead of a silent mystery. Counts candidates
    (no settlement join needed): a flagged record is excluded either way."""
    today = today or date.today()
    cutoff = today - timedelta(days=CALIBRATION_WINDOW_DAYS)
    return sum(1 for r in _settled_records(today)
               if r.get("basis", "hourly") == basis
               and date.fromisoformat(r["target_date"]) >= cutoff
               and _flagged(r))
