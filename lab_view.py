"""Lab page — scored forward-log experiments.

Section A: shadow (candidate) consensus vs production, from forecast-log rows
that carry candidate_consensus. Section B: per-model scoreboard from the
per-source means, incl. MOS at matched lead. Pure data assembly here;
streamlit only in render — mirrors edge_view.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import market_view


def head_to_head(rows: list[dict], settled: dict) -> dict:
    """{(variable, lead_bucket): {n, prod_mae, cand_mae, prod_wins, cand_wins,
    ties, days}} for every settled rolling CLI row carrying a candidate
    consensus. Rolling rows only (the 0900 cohort would double-count the day).
    `days` = [{date, prod_err, cand_err}] ascending, for the error chart."""
    out: dict = {}
    for r in rows:
        if r.get("candidate_consensus") is None or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli" or r.get("consensus") is None:
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        key = (r["variable"], r.get("lead_bucket") or 0)
        g = out.setdefault(key, {"n": 0, "_pa": 0.0, "_ca": 0.0, "prod_wins": 0,
                                 "cand_wins": 0, "ties": 0, "days": []})
        pe = abs(r["consensus"] - actual)
        ce = abs(r["candidate_consensus"] - actual)
        g["n"] += 1
        g["_pa"] += pe
        g["_ca"] += ce
        if round(pe, 2) == round(ce, 2):
            g["ties"] += 1
        elif pe < ce:
            g["prod_wins"] += 1
        else:
            g["cand_wins"] += 1
        g["days"].append({"date": r["target_date"], "prod_err": round(pe, 2),
                          "cand_err": round(ce, 2)})
    for g in out.values():
        g["prod_mae"] = round(g.pop("_pa") / g["n"], 2)
        g["cand_mae"] = round(g.pop("_ca") / g["n"], 2)
        g["days"].sort(key=lambda d: d["date"])
    return out


# Same-day lows logged by now-forward feeds before this date carry the
# wrong-tail covers_extreme bug (fixed 2026-07-18, commit 14a2a3a) — a feed
# refreshed mid-morning reported the evening tail (~84) as the "low" on
# settled-77 nights. Verified in the live log: nws/guidance/mos_lav same-day
# lows all show the +2 to +3.5 evening-tail bias signature pre-fix, while the
# full-day feeds (deterministic/ensemble) are clean.
_NOW_FORWARD = ("mos_lav", "mos_nbs", "nws", "guidance")
_SAME_DAY_LOW_FIX = "2026-07-19"


def per_model_scores(rows: list[dict], settled: dict) -> dict:
    """{(source, variable, lead_bucket): {n, mae, bias}} from the per-source
    means the forecast log records (ensemble / deterministic / nws / mos_*).
    bias = mean(model − settled): positive means the source ran hot. Rolling
    rows only, matched lead by construction (the sources were captured at the
    same moment as the row's consensus)."""
    out: dict = {}
    for r in rows:
        src = r.get("sources")
        if not src or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli":
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        lead = r.get("lead_bucket") or 0
        for name, val in src.items():
            if val is None:
                continue
            if (name in _NOW_FORWARD and r.get("variable") == "low"
                    and lead == 0 and r["target_date"] < _SAME_DAY_LOW_FIX):
                continue
            g = out.setdefault((name, r["variable"], lead),
                               {"n": 0, "_a": 0.0, "_s": 0.0})
            g["n"] += 1
            g["_a"] += abs(val - actual)
            g["_s"] += val - actual
    for g in out.values():
        g["mae"] = round(g.pop("_a") / g["n"], 2)
        g["bias"] = round(g.pop("_s") / g["n"], 2)
    return out


_LEAD_LABEL = {0: "Same-Day", 24: "Day-Ahead"}


def chart_frame(h2h: dict) -> list[dict]:
    """Long-form records for the per-day error chart: one Production and one
    Candidate point per scored (day, variable, lead)."""
    recs = []
    for (variable, lead), g in sorted(h2h.items()):
        for d in g["days"]:
            for series, key in (("Production", "prod_err"),
                                ("Candidate", "cand_err")):
                recs.append({"date": d["date"], "variable": variable,
                             "lead": lead, "series": series,
                             "abs_err": d[key]})
    return recs


def _error_chart(recs: list[dict]):
    """Tap-to-pin absolute-error chart (same touch pattern as the History
    page's equity curve: click/tap a point to pin its readout)."""
    import altair as alt
    import pandas as pd
    df = pd.DataFrame(recs)
    enc = alt.Chart(df).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("abs_err:Q", title="Abs Error (°F)"),
        color=alt.Color("series:N", legend=alt.Legend(title=None, orient="top")))
    line = enc.mark_line(strokeWidth=2)
    pick = alt.selection_point(on="click", nearest=True,
                               fields=["date", "series"], empty=False,
                               clear="dblclick")
    dots = enc.mark_point(filled=True, opacity=1).encode(
        size=alt.condition(pick, alt.value(150), alt.value(60)),
        tooltip=[alt.Tooltip("date:T", title="date"),
                 alt.Tooltip("series:N", title="series"),
                 alt.Tooltip("abs_err:Q", title="abs error", format=".1f")],
    ).add_params(pick)
    labels = df.assign(label=df.apply(
        lambda r: (f"{r['series']} · "
                   f"{pd.to_datetime(r['date']).strftime('%b %-d')}\n"
                   f"{r['abs_err']:.1f}°F Off"), axis=1))
    pinned = alt.Chart(labels).mark_text(
        align="left", baseline="top", x=6, y=4, fontSize=13, fontWeight="bold",
        lineBreak="\n", lineHeight=15,
    ).encode(text="label:N", color="series:N").transform_filter(pick)
    return ((line + dots + pinned)
            .properties(height=240, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def render(lab_loader) -> None:
    import pandas as pd

    market_view._theme_controls()
    st.title("Lab")
    try:
        h2h, models = lab_loader()
    except Exception:
        h2h, models = {}, {}

    # --- Section A: shadow consensus vs production ---
    st.subheader("Shadow Consensus vs Production")
    st.caption("The candidate model set runs silently beside production; both "
               "are scored here once days settle. Promotion needs the "
               "candidate to win at true day-ahead lead.")
    if not h2h:
        st.info("Accumulating — no settled shadow days yet. This fills in "
                "daily as candidate-logged days settle.")
    else:
        n = sum(g["n"] for g in h2h.values())
        pw = sum(g["prod_wins"] for g in h2h.values())
        cw = sum(g["cand_wins"] for g in h2h.values())
        with st.container(key="metrics2_lab"):
            c = st.columns(3)
        c[0].markdown(market_view.metric_card(
            "Scored Rows", str(n),
            "Settled (day, variable, lead) rows where both consensuses were "
            "logged."), unsafe_allow_html=True)
        c[1].markdown(market_view.metric_card(
            "Production Wins", str(pw),
            "Rows where the live consensus was closer to settlement."),
            unsafe_allow_html=True)
        c[2].markdown(market_view.metric_card(
            "Candidate Wins", str(cw),
            "Rows where the shadow (expanded model set) consensus was closer."),
            unsafe_allow_html=True)
        table = [{"Variable": v.capitalize(),
                  "Lead": _LEAD_LABEL.get(lead, f"{lead}h"),
                  "Number": g["n"],
                  "Production MAE": g["prod_mae"], "Candidate MAE": g["cand_mae"],
                  "Ahead": ("Tie" if g["prod_mae"] == g["cand_mae"] else
                            "Production" if g["prod_mae"] < g["cand_mae"]
                            else "Candidate")}
                 for (v, lead), g in sorted(h2h.items())]
        market_view._html_table(pd.DataFrame(table))
        recs = chart_frame(h2h)
        for variable in ("high", "low"):
            sub = [r for r in recs if r["variable"] == variable]
            if sub:
                st.caption(f"{variable.capitalize()} — Absolute Error By Day "
                           "(Tap A Point To Pin Its Readout)")
                st.altair_chart(_error_chart(sub), use_container_width=True)

    # --- Section B: per-model scoreboard ---
    st.markdown("---")
    st.subheader("Per-Model Scoreboard")
    st.caption("Each source's own logged forecast vs settlement at matched "
               "lead — the evidence base for MOS weighting. Bias is model "
               "minus settled: positive ran hot, negative ran cold.")
    if not models:
        st.info("Accumulating — per-model source logging began 2026-07-17; "
                "rows appear as those days settle.")
        return
    for lead in (24, 0):
        rows = [{"Source": name, "Variable": v.capitalize(), "Number": g["n"],
                 "MAE": g["mae"], "Bias": f'{g["bias"]:+.2f}'}
                for (name, v, ld), g in sorted(models.items()) if ld == lead]
        if rows:
            st.markdown(f"**{_LEAD_LABEL.get(lead, f'{lead}h')}**")
            market_view._html_table(pd.DataFrame(rows))
