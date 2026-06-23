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
from backtest import contract_points, reliability_bins, _brier, LABELS
from settlement import bin_for_temp
from sources import station_history

# Minimum settled days for a (lead, variable) before we trust its empirical sigma.
MIN_LEAD_DAYS = 10
# Self-correction tuning. Shrink a measured bias toward zero by n/(n+SHRINK_K)
# so a noisy short sample is damped and a persistent bias strengthens with data;
# only correct when the bias clears SIG_Z standard errors (distinguishable from 0).
SHRINK_K = 8
SIG_Z = 1.0


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
        within1 = abs(LABELS.index(peak_label) - LABELS.index(actual_label)) <= 1
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

    return {"n_settled": n_settled, "by_variable": by_variable, "by_lead": by_lead}


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


def per_lead_sigma(min_days: int = MIN_LEAD_DAYS, today: date | None = None) -> dict:
    """{lead_bucket: {variable: sigma}} for buckets with enough settled days.

    Calibration uses this to override the interim inflation factor once the
    forward log can speak for itself. Buckets below `min_days` are omitted, so
    the model keeps falling back to the static inflation for those.
    """
    out: dict[int, dict[str, float]] = {}
    for bucket, vars_ in score(today, basis="hourly").get("by_lead", {}).items():
        for var, stats in vars_.items():
            if stats["n"] >= min_days and stats.get("sigma") is not None:
                out.setdefault(int(bucket), {})[var] = stats["sigma"]
    return out


def per_lead_bias(min_days: int = MIN_LEAD_DAYS, today: date | None = None,
                  basis: str = "hourly") -> dict[int, dict[str, float]]:
    """{lead_bucket: {variable: correction}} signed bias to SUBTRACT from the
    consensus, for buckets the data can speak to.

    Built from score()'s by_lead bias (= mean(consensus - actual); positive =
    forecast ran warm). Two guards keep auto-correction safe on small samples: a
    >= min_days gate, plus shrinkage toward zero by n/(n+SHRINK_K) combined with a
    significance test (|bias| must exceed SIG_Z * sigma/sqrt(n)). A noisy bias is
    shrunk away; a persistent one survives and grows as days accumulate. Omitted
    buckets => the model applies no correction there.
    """
    out: dict[int, dict[str, float]] = {}
    for bucket, vars_ in score(today, basis=basis).get("by_lead", {}).items():
        for var, stats in vars_.items():
            n = stats.get("n_resid", stats.get("n", 0))
            bias = stats.get("bias")
            sigma = stats.get("sigma")
            if n < min_days or bias is None or sigma is None:
                continue
            stderr = sigma / math.sqrt(n)
            if abs(bias) <= SIG_Z * stderr:
                continue  # statistically indistinguishable from zero
            out.setdefault(int(bucket), {})[var] = round(bias * n / (n + SHRINK_K), 2)
    return out
