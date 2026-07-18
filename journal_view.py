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


_EM = "—"

# Page-local CSS: full-width left-aligned day cards built on the themed
# .wxcard surface (so both themes apply), with compact line spacing.
_CSS = """<style>
.wxjday{width:100%;margin:0 0 10px 0;text-align:left;}
.wxjday .wxcard-l{margin-bottom:6px;}
.wxjl{font-size:0.95rem;line-height:1.55;color:var(--ink,inherit);}
</style>"""


def _var_line(label: str, g: dict | None) -> str:
    if not g:
        return f'<div class="wxjl">{label}: {_EM}</div>'
    mark = "✓ Exact" if g["exact"] else f'{g["diff"]:+.1f}°F'
    mkt = ""
    if g.get("market_closer") is True:
        mkt = " · Market Closer"
    elif g.get("market_closer") is False:
        mkt = " · Model Closer"
    return (f'<div class="wxjl">{label}: Settled {g["settled"]:g} · '
            f'Model {g["model"]:g} · {mark}{mkt}</div>')


def day_card_html(entry: dict) -> str:
    """One settled day as a full-width themed card: date + flag badges, a High
    line, a Low line, and a realized-P&L line when bets settled that day."""
    d = date.fromisoformat(entry["date"])
    badges = ""
    if "storm" in entry.get("flags", ()):
        badges += " ⛈"
    if "front" in entry.get("flags", ()):
        badges += " 🌪"
    pnl_line = ""
    p = entry.get("pnl")
    if p:
        sign = "+" if p["net"] >= 0 else "−"
        amt = f'{sign}${abs(p["net"]):,.2f}'
        pct = f' ({p["pct"]:+.0f}%)' if p.get("pct") is not None else ""
        n = p["n"]
        pnl_line = (f'<div class="wxjl">P&amp;L: {amt}{pct} on {n} Settled '
                    f'Bet{"s" if n != 1 else ""} ({p["wins"]}–{p["losses"]})</div>')
    return (f'<div class="wxcard wxjday">'
            f'<div class="wxcard-l">{d.strftime("%A, %b %-d")}{badges}</div>'
            f'{_var_line("High", entry.get("high"))}'
            f'{_var_line("Low", entry.get("low"))}'
            f'{pnl_line}</div>')


def render(journal_loader) -> None:
    market_view._theme_controls()   # sidebar Settings (theme picker) + theme CSS
    st.title("Journal")
    st.markdown(_CSS, unsafe_allow_html=True)
    try:
        data = journal_loader()
    except Exception:
        data = None
    if not data or not data.get("days"):
        st.info("Accumulating — the journal fills in as days settle "
                "(one card per finished day).")
        return
    s = data.get("summary") or {}

    def frac(pair):
        return f"{pair[0]}/{pair[1]}" if pair and pair[1] else _EM

    with st.container(key="metrics2_journal"):
        c = st.columns(4)
    c[0].markdown(market_view.metric_card(
        "High Hits (7d)", frac(s.get("high_hits7")),
        "Exact-bin hits on the daily high over the last 7 settled days."),
        unsafe_allow_html=True)
    c[1].markdown(market_view.metric_card(
        "Low Hits (7d)", frac(s.get("low_hits7")),
        "Exact-bin hits on the daily low over the last 7 settled days."),
        unsafe_allow_html=True)
    c[2].markdown(market_view.metric_card(
        "Exact Streak", f'{s.get("streak", 0)}d',
        "Consecutive most-recent days where BOTH the high and the low hit "
        "their exact bin."), unsafe_allow_html=True)
    pnl = s.get("pnl_total")
    c[3].markdown(market_view.metric_card(
        "Realized P&L", _EM if pnl is None else f"${pnl:+,.2f}",
        "Net realized Kalshi P&L across all journal days (needs the [kalshi] "
        "secret; blank locally)."), unsafe_allow_html=True)

    for entry in data["days"]:
        st.markdown(day_card_html(entry), unsafe_allow_html=True)
    st.caption("Graded with the same rules as the Morning Recap: the model's "
               "call is the fixed 9am capture when it exists, else the "
               "day-ahead forecast. ⛈ storm-flagged day · 🌪 front-guard day.")
