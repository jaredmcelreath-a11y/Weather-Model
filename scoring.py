"""Score the model's own logged predictions against actual KDFW settlements.

This is the live, forward-looking complement to backtest.py: where backtest
replays a simplified pipeline over archived forecasts, this grades exactly what
the dashboard showed (full ensemble + nowcast + every correction), once each
target day has settled. It powers the "Model accuracy" panel and, once enough
days accumulate, feeds empirical per-lead-time spread back into calibration.
"""

from __future__ import annotations

import math
from datetime import date

import forecast_log
from backtest import contract_points, reliability_bins, _brier
from settlement import bin_for_temp
from sources import station_history

# Minimum settled days for a (lead, variable) before we trust its empirical sigma.
MIN_LEAD_DAYS = 10


def _settled_records(today: date | None = None) -> list[dict]:
    rows = forecast_log.load()
    today = today or date.today()
    return [r for r in rows if date.fromisoformat(r["target_date"]) < today]


def _actuals_for(records: list[dict], basis: str = "hourly") -> dict[date, tuple[float, float]]:
    if not records:
        return {}
    days = [date.fromisoformat(r["target_date"]) for r in records]
    fetch = (station_history.fetch_actual_cli if basis == "cli"
             else station_history.fetch_actual)
    return fetch(min(days), max(days))


def score(today: date | None = None, basis: str = "hourly") -> dict:
    """Grade all settled logged predictions.

    Returns per-variable Brier + reliability curve, and per-(lead, variable)
    signed-error stats. Empty/unsettled log -> zeroed structure (never raises on
    no data; network errors during the actuals fetch propagate to the caller).
    """
    records = [r for r in _settled_records(today)
               if r.get("basis", "hourly") == basis]
    empty = {"n_settled": 0, "by_variable": {}, "by_lead": {}}
    if not records:
        return empty
    actual = _actuals_for(records, basis)
    if not actual:
        return empty

    var_points: dict[str, list[tuple]] = {"high": [], "low": []}
    var_brier: dict[str, list[float]] = {"high": [], "low": []}
    lead_resid: dict[tuple, list[float]] = {}  # (bucket, variable) -> signed errors
    n_settled = 0

    for r in records:
        d = date.fromisoformat(r["target_date"])
        if d not in actual:
            continue
        var = r["variable"]
        act = actual[d][0] if var == "high" else actual[d][1]
        probs = r["probabilities"]
        var_brier[var].append(_brier(probs, bin_for_temp(act)))
        var_points[var].extend(contract_points(probs, act, var))
        if r.get("consensus") is not None:
            lead_resid.setdefault((r["lead_bucket"], var), []).append(r["consensus"] - act)
        n_settled += 1

    by_variable = {}
    for var in ("high", "low"):
        if not var_brier[var]:
            continue
        by_variable[var] = {
            "n": len(var_brier[var]),
            "brier": round(sum(var_brier[var]) / len(var_brier[var]), 3),
            "reliability": reliability_bins(var_points[var]),
        }

    by_lead = {}
    for (bucket, var), errs in lead_resid.items():
        b = sum(errs) / len(errs)
        sigma = math.sqrt(sum((e - b) ** 2 for e in errs) / len(errs))
        by_lead.setdefault(bucket, {})[var] = {
            "n": len(errs), "bias": round(b, 2), "sigma": round(sigma, 2),
        }

    return {"n_settled": n_settled, "by_variable": by_variable, "by_lead": by_lead}


def per_lead_sigma(min_days: int = MIN_LEAD_DAYS, today: date | None = None) -> dict:
    """{lead_bucket: {variable: sigma}} for buckets with enough settled days.

    Calibration uses this to override the interim inflation factor once the
    forward log can speak for itself. Buckets below `min_days` are omitted, so
    the model keeps falling back to the static inflation for those.
    """
    out: dict[int, dict[str, float]] = {}
    for bucket, vars_ in score(today, basis="hourly").get("by_lead", {}).items():
        for var, stats in vars_.items():
            if stats["n"] >= min_days:
                out.setdefault(int(bucket), {})[var] = stats["sigma"]
    return out
