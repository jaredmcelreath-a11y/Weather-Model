"""Data for the Morning Recap card.

Two pure functions the dashboard renders: `today_setup` distills the day's
state-of-play from the live snapshot, and `yesterday_scorecard` grades the
model's forecast for yesterday against what KDFW actually settled at. Kept
dependency-light (dicts in, dict out) so the display layer stays thin and this
logic is testable without streamlit.
"""
from __future__ import annotations

from datetime import date, timedelta


def _top_bin(probs: dict | None) -> list | None:
    if not probs:
        return None
    label, p = max(probs.items(), key=lambda kv: kv[1])
    return [label, round(p, 4)]


def today_setup(snap: dict, mkt_high: float | None = None,
                mkt_low: float | None = None) -> dict:
    """Today's high & low state from the snapshot, plus the market EVs (passed in
    since the dashboard fetches them separately). Forward-looking briefing half."""
    today = snap.get("today", {}) or {}
    hi = today.get("high") or {}
    lo = today.get("low") or {}
    return {
        "date": today.get("day"),
        "high": {"consensus": hi.get("consensus"),
                 "top_bin": _top_bin(hi.get("probabilities")),
                 "market_ev": mkt_high,
                 "locked": bool(hi.get("peak_locked"))},
        "low": {"observed": lo.get("observed_so_far"),
                "consensus": lo.get("consensus"),
                "market_ev": mkt_low,
                "locked": bool(lo.get("peak_locked"))},
    }


def _pick_forecast(rows: list[dict], day_iso: str, variable: str) -> dict | None:
    """The model's forecast row to grade for (day, variable): prefer the fixed
    09:00 decision-time cohort, then the day-ahead (lead-24) row, then whatever
    lead is available. CLI basis only (the Kalshi settlement cohort)."""
    cands = [r for r in rows if r.get("target_date") == day_iso
             and r.get("variable") == variable
             and r.get("basis", "hourly") == "cli"]
    if not cands:
        return None

    def rank(r):
        if r.get("capture_cohort") == "0900":
            return (0, 0)
        if r.get("lead_bucket") == 24:
            return (1, 0)
        return (2, int(r.get("lead_bucket") or 0))

    return sorted(cands, key=rank)[0]


def _grade(row: dict, settled: float) -> dict | None:
    model = row.get("consensus")
    if model is None:
        return None
    entry = {"settled": settled, "model": model,
             "exact": round(model) == round(settled),
             "diff": round(model - settled, 1)}
    mkt = (row.get("market") or {}).get("ev")
    entry["market"] = mkt
    entry["market_closer"] = (abs(mkt - settled) < abs(model - settled)
                              if mkt is not None else None)
    return entry


def yesterday_scorecard(today: date, settled_map: dict, forecast_rows: list[dict]
                        ) -> dict | None:
    """Grade yesterday's high & low forecast against settlement. `settled_map` is
    {day: (high, low)} (e.g. settlements.as_map("cli")); `forecast_rows` is the
    forecast log. None until yesterday is settled or if no forecast row exists."""
    yday = today - timedelta(days=1)
    hl = settled_map.get(yday)
    if not hl:
        return None
    day_iso = yday.isoformat()
    out: dict = {"date": day_iso}
    for i, var in enumerate(("high", "low")):
        row = _pick_forecast(forecast_rows, day_iso, var)
        if row is not None:
            g = _grade(row, hl[i])
            if g:
                out[var] = g
    if "high" not in out and "low" not in out:
        return None
    return out
