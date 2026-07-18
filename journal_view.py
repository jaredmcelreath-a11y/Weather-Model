"""Journal page — the trading diary: one scorecard per settled day.

Pure data assembly here (streamlit only inside render), mirroring edge_view.
Grading is recap.day_scorecard — the same function the Morning Recap uses, so
the two can never disagree about a day.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import market_view
import recap


def _day_flags(forecast_rows: list[dict], day_iso: str) -> list[str]:
    """Regime badges for the day, from the CLI forecast-log rows' latched
    flags: 'storm' (convective_widened) and/or 'front' (front_widened)."""
    flags = set()
    for r in forecast_rows:
        if r.get("target_date") != day_iso or r.get("basis", "hourly") != "cli":
            continue
        if r.get("convective_widened"):
            flags.add("storm")
        if r.get("front_widened"):
            flags.add("front")
    return sorted(flags)


def _summary(days: list[dict]) -> dict:
    """Headline strip over the (newest-first) day entries: last-7-settled-days
    exact-bin hit rates, total realized P&L, and the current both-exact streak
    (a day missing either grade breaks it)."""
    def hits(entries, var):
        graded = [e for e in entries if var in e]
        return [sum(1 for e in graded if e[var]["exact"]), len(graded)]

    last7 = days[:7]
    pnl = [e["pnl"]["net"] for e in days if e.get("pnl")]
    streak = 0
    for e in days:
        if e.get("high", {}).get("exact") and e.get("low", {}).get("exact"):
            streak += 1
        else:
            break
    return {"high_hits7": hits(last7, "high"), "low_hits7": hits(last7, "low"),
            "pnl_total": round(sum(pnl), 2) if pnl else None, "streak": streak}


def assemble(today: date, settled_map: dict, forecast_rows: list[dict],
             bet_rows: list[dict] | None = None) -> dict:
    """{summary, days}: one graded entry per settled day, newest first. Today
    is excluded even if a (preliminary) settlement row exists — the journal
    records finished days only. Days with no gradeable forecast are skipped."""
    days = []
    for day in sorted((d for d in settled_map if d < today), reverse=True):
        entry = recap.day_scorecard(day, settled_map, forecast_rows, bet_rows)
        if entry is None:
            continue
        entry["flags"] = _day_flags(forecast_rows, day.isoformat())
        days.append(entry)
    return {"summary": _summary(days), "days": days}
