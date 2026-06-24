"""Shared rendering for the KDFW high/low market dashboard pages.

One render path, parameterized by a `MarketAdapter` (see markets.py), so the
Robinhood (ForecastEx) and Kalshi pages stay in sync. Everything market-specific
— the live contract fetch, the model→contract price mapping, and the on-screen
wording — comes from the adapter; all trade logic (edge signals, flip-prob, exit
plans, Top-3 flip/hold, Safest-hold) is identical across exchanges.
"""

from __future__ import annotations

from datetime import date, datetime

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import calibration
import model
from config import STATION_ID

# Most the "Safest hold to $1" box will pay for a contract. Above this the price
# is already near settlement value, so the remaining upside doesn't justify the
# capital — exclude it from the safe-hold pick regardless of edge.
SAFE_HOLD_MAX_ASK = 0.92


@st.cache_data(ttl=30, show_spinner=False)
def _kalshi_implied(day_iso):
    """Kalshi's market-implied expected high/low for `day_iso`, distilled from
    the live contract ladder — shown next to Current temp on both pages. None per
    variable when no priced contracts are live."""
    from sources import kalshi
    out = {}
    for var in ("high", "low"):
        try:
            f = kalshi.implied_forecast(var, date.fromisoformat(day_iso))
        except Exception:
            f = None
        out[var] = f["ev"] if f else None
    return out


@st.cache_data(ttl=120, show_spinner=False)
def _consensus_history():
    """Cached intraday consensus samples (the whole file; filtered per chart)."""
    import consensus_log
    try:
        return consensus_log.load()
    except Exception:
        return []


def consensus_history_df(rows, day_iso, variable, basis, include_temp):
    """Time-indexed df of consensus (+ live temp on today) for one series, for
    st.line_chart. None when fewer than two points exist yet."""
    pts = [r for r in rows
           if r.get("target_date") == day_iso and r.get("variable") == variable
           and r.get("basis", "hourly") == basis]
    if len(pts) < 2:
        return None
    pts.sort(key=lambda r: r["captured_at"])
    data = []
    for r in pts:
        # Naive local wall-clock time: keeps the x-axis labelled in station-local
        # clock time regardless of the viewer's browser timezone, and Altair only
        # accepts naive/UTC datetimes for explicit axis tick values.
        row = {"time": datetime.fromisoformat(r["captured_at"]).replace(tzinfo=None),
               "consensus": r.get("consensus")}
        if include_temp and r.get("current_temp") is not None:
            row["current temp"] = r["current_temp"]
        data.append(row)
    return pd.DataFrame(data).set_index("time")


def consensus_chart(hist, variable):
    """Altair line chart of consensus (and today's live temp) through the day.

    Built by hand (rather than st.line_chart) so we can: label the x-axis with
    clock times (not dates) at 30-minute ticks; hold a readable 50–100°F y-window
    that only expands when the data runs outside it (lows in the 70s shouldn't be
    squashed against a 0–100 axis); and show one combined, swatch-free hover
    readout of the time plus each series.
    """
    df = hist.reset_index()
    value_cols = [c for c in df.columns if c != "time"]
    line_color = "#ff6b6b" if variable == "high" else "#4dabf7"
    others = [c for c in value_cols if c != "consensus"]
    color_scale = alt.Scale(domain=["consensus"] + others,
                            range=[line_color] + ["#adb5bd"] * len(others))

    vals = pd.concat([df[c] for c in value_cols]).dropna()
    lo = min(50.0, float(vals.min()) - 2) if not vals.empty else 50.0
    hi = max(100.0, float(vals.max()) + 2) if not vals.empty else 100.0

    # Explicit half-hour tick positions (Vega chokes on a 30-min `tickCount`
    # interval object). labelOverlap drops labels that would collide once the
    # day's span grows, while keeping the 30-min tick marks themselves.
    t = pd.to_datetime(df["time"])
    ticks = pd.date_range(t.min().floor("30min"), t.max().ceil("30min"),
                          freq="30min").to_pydatetime().tolist()

    long = df.melt("time", value_vars=value_cols,
                   var_name="series", value_name="degF").dropna()
    lines = alt.Chart(long).mark_line(strokeWidth=2.5).encode(
        x=alt.X("time:T", title=None,
                axis=alt.Axis(format="%-I:%M %p", values=ticks,
                              labelOverlap=True, labelAngle=-40)),
        y=alt.Y("degF:Q", title="°F", scale=alt.Scale(domain=[lo, hi])),
        color=alt.Color("series:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top")),
    )

    nearest = alt.selection_point(nearest=True, on="pointerover",
                                  fields=["time"], empty=False)
    selectors = alt.Chart(df).mark_point().encode(
        x="time:T", opacity=alt.value(0)).add_params(nearest)
    rule = alt.Chart(df).mark_rule(color="#868e96").encode(
        x="time:T",
        opacity=alt.condition(nearest, alt.value(0.4), alt.value(0)),
        tooltip=[alt.Tooltip("time:T", title="time", format="%-I:%M %p")] +
                [alt.Tooltip(f"{c}:Q", title=c, format=".1f") for c in value_cols],
    )
    points = lines.mark_point(filled=True).encode(
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)))

    return (lines + selectors + points + rule).properties(height=220)


def reliability_df(bins):
    """Reliability bins -> df for st.line_chart: observed vs the ideal diagonal."""
    if not bins:
        return None
    df = pd.DataFrame(bins).set_index("predicted")[["observed"]]
    df["ideal (perfect)"] = df.index
    return df


def cents(x):
    return "—" if x is None else f"{round(x * 100)}¢"


def spread_c(ask, bid):
    """Bid-ask spread (the round-trip cost of flipping), or None if a side is
    missing. Equals how far the bid must climb just to break even on a flip."""
    if ask is None or bid is None:
        return None
    return ask - bid


def exit_plan(ask, bid):
    """The realistic way out of a long bought at `ask`.

    Flipping for +20% needs the *bid* to reach ask*1.2, so it only makes sense
    when the spread is tighter than that 20% target; a wider spread means the
    friction dwarfs the profit and you should hold to settlement, where the
    spread costs nothing (it pays the full 100¢).
    """
    if ask is None:
        return "—"
    target = ask * 1.2
    if target >= 1.0:
        return "hold (caps 100¢)"
    sp = spread_c(ask, bid)
    if sp is None or sp > 0.2 * ask:
        return "hold to settle"
    return f"flip @ {cents(target)}"


def _flag_hold_only(df, exit_col):
    """Tint the `contract` cell red on rows whose recommended side is too
    wide-spread to flip (exit plan == 'hold to settle', i.e. spread > 20% of the
    ask) — a quick 'don't look at this one to flip before settlement' cue.
    Streamlit renders Styler color/background on data cells, so the contract is a
    hidden-index column here rather than the dataframe index.
    """
    def _row(row):
        styles = [""] * len(row)
        if row.get(exit_col) == "hold to settle":
            styles[row.index.get_loc("contract")] = (
                "background-color: rgba(255,75,75,0.22); color: #ff4b4b")
        return styles
    return df.style.apply(_row, axis=1)


def prob_table(probs: dict, variable: str, observed=None, top: int = 14) -> pd.DataFrame:
    """Bins with non-trivial probability, sorted by temperature.

    `chance %` is the cumulative model probability the value lands at this bin
    or beyond, in the direction that settles the contract: High = this degree
    or hotter (P value >= bin), Low = this degree or colder (P value <= bin).
    It runs from ~100% at the near end down to ~0% at the far tail.
    """
    items = [(k, v) for k, v in probs.items() if v >= 0.005]
    items.sort(key=lambda kv: -kv[1])
    keep = {k for k, _ in items[:top]}
    rows = [(k, probs[k]) for k in probs if k in keep]

    def sort_key(label):
        return (0, -1) if label.startswith("<=") else \
               (2, 1e9) if label.startswith(">=") else (1, int(label))
    rows.sort(key=lambda r: sort_key(r[0]))
    df = pd.DataFrame(rows, columns=["bin", "prob"])
    df["prob %"] = (df["prob"] * 100).round(1)
    cumulative = model.prob_at_least if variable == "high" else model.prob_at_most
    df["chance %"] = [round(cumulative(probs, model.bin_temp(b)) * 100, 1)
                      for b in df["bin"]]
    return df.set_index("bin")


def lock_status(d, variable):
    """Interpret the nowcast lock state into an actionable badge + buy-window note.

    Returns (level, headline, detail). `level` picks the Streamlit box: "success"
    = the extreme is in and σ has collapsed (prime buy window), "info" = still
    developing, "warning" = a colder/hotter reading is still expected later (e.g.
    an evening front that could undercut the morning low). Built from the
    snapshot's `locked_ratio` plus the consensus-vs-observed gap, which is the
    model's own read on whether the day's extreme is still ahead of us.
    """
    lr = d.get("locked_ratio", 1.0)
    resolved = int((1 - lr) * 100)
    obs = d.get("observed_so_far")
    consensus = d["consensus"]
    floor = getattr(model, "_SIGMA_FLOOR", 0.7)

    # No observations yet (Tomorrow, or pre-dawn today): pure, widest forecast.
    if obs is None:
        window = ("late afternoon (≈4–6pm CDT), after the peak"
                  if variable == "high"
                  else "early morning (≈7–9am CDT), after the dawn trough")
        return ("info", "📅 Pure forecast — nothing observed yet",
                f"Spread is at its widest. The {variable} won't lock until "
                f"{window}; σ floors near {floor:.1f}°F once it does.")

    if variable == "high":
        if d.get("peak_locked"):
            return ("success", "🔒 Locked — peak has passed",
                    f"High is in at {obs:.1f}°F — temperature has fallen back from "
                    f"the peak, so it's observationally settled (σ ≈ "
                    f"{d['sigma_used']:.1f}°F). Prime buy window.")
        if consensus > obs + 1.0:
            return ("info", "⏳ Open — peak not reached",
                    f"High still climbing toward ~{consensus:.0f}°F (only "
                    f"{obs:.1f}°F observed so far). Wait for the afternoon peak.")
        if resolved >= 85:
            return ("success", "🔒 Locked — peak has passed",
                    f"High is in at {obs:.1f}°F and σ has collapsed to "
                    f"{d['sigma_used']:.1f}°F (floor ~{floor:.1f}). Prime buy window.")
        return ("info", "🔓 Locking — near the peak",
                f"High ≈ {obs:.1f}°F and tightening ({resolved}% resolved). "
                "Close to the prime window.")

    # variable == "low"
    if d.get("peak_locked"):
        return ("success", "🔒 Locked — dawn trough is in",
                f"Low is in at {obs:.1f}°F — temperature has climbed back from the "
                f"trough, so it's observationally settled (σ ≈ "
                f"{d['sigma_used']:.1f}°F). Prime buy window.")
    if consensus < obs - 1.0:
        return ("warning", "⚠️ Front risk — colder reading expected later",
                f"Coldest so far is {obs:.1f}°F but the model sees ~{consensus:.0f}°F "
                "later (possible evening front before midnight). The morning low is "
                "NOT safe to treat as settled — wait or size down.")
    if resolved >= 85:
        return ("success", "🔒 Locked — dawn trough is in",
                f"Low is in at {obs:.1f}°F with no colder reading expected; σ "
                f"collapsed to {d['sigma_used']:.1f}°F (floor ~{floor:.1f}). Prime buy window.")
    return ("info", "🔓 Locking — past the dawn trough",
            f"Low ≈ {obs:.1f}°F ({resolved}% resolved). Watch the evening for a "
            "front before treating it as final.")


def render_variable(col, title, d, variable, day_iso, adapter, featured=False,
                    safe_min=None, today_iso=None):
    if safe_min is None:
        safe_min = adapter.safe_hold_default
    with col:
        head = f"### {title}"
        if featured:
            head += " ⭐"
        st.markdown(head)
        if d is None:
            st.warning("No data.")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("Consensus", f"{d['consensus']}°F")
        c2.metric("Spread", f"{d['sigma_used']}°F (±1σ)",
                  help="One standard deviation of the model's forecast — its error "
                       "bars. About 68% of outcomes should land within ±this of the "
                       "consensus, ~95% within ±2σ. Wider = more uncertain; this is "
                       "what turns the consensus into contract probabilities. It gets "
                       "inflated for day-ahead forecasts until the scoring log matures.")
        locked_pct = int((1 - d["locked_ratio"]) * 100)
        c3.metric("Resolved", f"{locked_pct}%",
                  help="How much of the day's uncertainty is already settled by "
                       "observations. 100% ≈ the extreme has happened.")
        if d["observed_so_far"] is not None:
            st.caption(f"Observed so far: {d['observed_so_far']:.1f}°F (hard bound)")
        if d.get("cooling_applied"):
            st.caption("🌙 Clear/calm night — extra radiational-cooling offset "
                       "applied to the low.")
        from convective import risk_label
        _conv = risk_label(d)
        if _conv:
            st.caption(_conv)

        level, headline, detail = lock_status(d, variable)
        getattr(st, level)(f"**{headline}** — {detail}")

        # Consensus through the day: how the model's consensus has drifted (one
        # point per ~30 min), with today's live temperature overlaid so you can
        # watch the reading climb/fall toward the predicted peak/trough.
        st.markdown("**Consensus through the day**")
        hist = consensus_history_df(_consensus_history(), day_iso, variable,
                                    adapter.basis, include_temp=(day_iso == today_iso))
        if hist is not None:
            st.altair_chart(consensus_chart(hist, variable),
                            use_container_width=True)
            st.caption("Model consensus (°F) sampled every ~30 min" +
                       (", with the live temperature overlaid — watch it converge "
                        "on the predicted peak/trough." if "current temp" in hist.columns
                        else "."))
        else:
            st.caption("Consensus history builds through the day — a point every "
                       "~30 minutes. Check back as it accumulates.")

        probs = d["probabilities"]
        df = prob_table(probs, variable)
        st.bar_chart(df["prob %"], height=240, color="#ff6b6b" if title.startswith("High") else "#4dabf7")
        st.dataframe(df[["prob %", "chance %"]], width="stretch", height=210)
        chance_dir = "this degree or hotter" if variable == "high" else "this degree or colder"
        st.caption(f"prob % = chance the {variable} lands exactly in that bin. "
                   f"chance % = cumulative chance it's {chance_dir}.")

        # Live market vs the model (contracts + price→model mapping from the adapter).
        st.markdown(adapter.heading(variable))
        if adapter.basis_note:
            st.caption(adapter.basis_note)
        contracts = adapter.fetch(variable, day_iso)
        if not contracts:
            st.caption(adapter.no_market_msg)
            return
        rows = []
        picks = []  # actionable buys, for the Top-3 section below
        holds = []  # safe hold-to-settlement candidates, for the Safest-hold box
        for c in contracts:
            p = adapter.model_prob(probs, c)
            ya, na = c["yes_ask"], c["no_ask"]
            yb, nb = c["yes_bid"], c["no_bid"]
            edge_yes = (p - ya) if ya is not None else -9
            edge_no = ((1 - p) - na) if na is not None else -9
            # Safe hold-to-$1 candidates: scan BOTH sides (the safe side may not be
            # the edge-signal side), keep only high win-prob, positively-priced
            # bets that don't cost more than 92¢ (above that the upside is too thin
            # to be worth the capital), and score by risk-adjusted return
            # edge / sqrt(p*(1-p)).
            for h_side, h_win, h_ask in (("YES", p, ya), ("NO", 1 - p, na)):
                if h_ask is None or h_win < safe_min or h_ask > SAFE_HOLD_MAX_ASK:
                    continue
                h_edge = h_win - h_ask
                if h_edge <= 0:
                    continue
                vol = (h_win * (1 - h_win)) ** 0.5 or 1e-9
                holds.append((h_edge / vol, c["label"], h_side, h_win, h_ask, h_edge))
            # Spread + exit plan for the recommended side: flip for +20% only when
            # the bid-ask spread is tight enough to reach it, else hold to settle.
            spread = plan = "—"
            if edge_yes >= edge_no and edge_yes > 0.03:
                signal = f"BUY YES +{edge_yes*100:.0f}"
                picks.append((c["label"], "YES", p, ya, edge_yes, yb))
                spread = cents(spread_c(ya, yb))
                plan = exit_plan(ya, yb)
            elif edge_no > 0.03:
                signal = f"BUY NO +{edge_no*100:.0f}"
                picks.append((c["label"], "NO", 1 - p, na, edge_no, nb))
                spread = cents(spread_c(na, nb))
                plan = exit_plan(na, nb)
            else:
                signal = "—"
            rows.append({
                "contract": c["label"],
                "model %": f"{p*100:.0f}%",
                "YES (bid/ask)": f"{cents(yb)}/{cents(ya)}",
                "NO (bid/ask)": f"{cents(nb)}/{cents(na)}",
                "last": cents(c["last"]),
                "signal": signal,
                "spread": spread,
                "exit plan": plan,
            })
        st.dataframe(_flag_hold_only(pd.DataFrame(rows), "exit plan"),
                     width="stretch", height=320, hide_index=True)
        st.caption("model % = model's YES probability for that contract. "
                   "signal = buy side with >3pp edge vs the ask. "
                   "spread = ask − bid on the signal's side: how far the bid must "
                   "climb just to break even on a flip. "
                   "exit plan = 'flip @ X' when the spread is tight enough to sell "
                   "for +20%, else 'hold to settle' (where the spread costs nothing). "
                   "A contract shown in 🔴 red is too wide-spread to flip — hold it to "
                   f"settlement. Prices in ¢, live from {adapter.name} (refreshes ~30s).")

        # Top 3 HOLD-TO-SETTLEMENT trades: the model's best value picks to carry to
        # $1. Scored by edge × return-on-cost EV (geometric mean, edge / sqrt(ask)):
        # rewards real mispricing while lifting cheaper contracts, without letting
        # penny longshots dominate. Held to settlement, so the spread is irrelevant.
        # Gated at ≥60% model win-probability so only genuinely confident bets show.
        TOP3_MIN_CONF = 0.60
        st.markdown(f"**🎯 Top 3 {variable} hold-to-settlement trades** — best value "
                    "held to $1")
        scored = []
        for lbl, side, mp, price, edge, bid in picks:
            if mp < TOP3_MIN_CONF:               # confidence gate
                continue
            ask = max(price, 0.01)               # guard div-by-zero on a 0¢ ask
            ev = edge / ask                      # expected return on capital risked
            score = edge / (ask ** 0.5)          # geometric mean of edge and EV
            scored.append((score, lbl, side, mp, ask, edge, ev, bid))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            top = [{
                "contract": lbl,
                "side": side,
                "model %": f"{mp*100:.0f}%",
                "ask": cents(ask),
                "spread": cents(spread_c(ask, bid)),
                "edge (pp)": f"+{edge*100:.0f}",
                "EV %/cost": f"+{ev*100:.0f}%",
                "exit": exit_plan(ask, bid),
            } for _, lbl, side, mp, ask, edge, ev, bid in scored[:3]]
            st.dataframe(_flag_hold_only(pd.DataFrame(top), "exit"),
                         width="stretch", height=140, hide_index=True)
            st.caption("The model's most likely winning bets for the "
                       f"{variable}, ranked by a blend of edge and expected value "
                       "(this is hold-to-settlement value, so the spread does NOT "
                       "affect the ranking — at settlement it costs nothing). "
                       "edge (pp) = model prob for that side minus the ask. "
                       "EV %/cost = expected return per dollar risked (edge ÷ ask). "
                       "spread / exit = liquidity cue if you change your mind: a wide "
                       "spread (🔴) means flipping early isn't viable. Only contracts "
                       "clearing both the 3pp edge threshold and "
                       f"{TOP3_MIN_CONF*100:.0f}% model confidence are shown.")
        else:
            st.caption(f"No contract clears both the 3pp edge and "
                       f"{TOP3_MIN_CONF*100:.0f}% model-confidence bar right now — "
                       "no high-confidence value buy.")

        # Safest hold-to-$1 pick: the highest risk-adjusted-return bet among the
        # high-confidence, positively-priced contracts. Held to settlement, so the
        # spread is irrelevant — this is the low-variance counterweight to the
        # longshot-friendly Top-3 above.
        st.markdown(f"**🛡️ Safest {variable} hold to $1** — lowest-risk bet to hold "
                    "to settlement")
        if holds:
            holds.sort(key=lambda x: x[0], reverse=True)
            _, lbl, side, win, ask, h_edge = holds[0]
            ev_cost = h_edge / ask          # expected return per dollar risked
            win_ret = (1 - ask) / ask       # return if it settles to $1
            hc = st.columns(4)
            hc[0].metric(f"BUY {side} · {lbl}", f"win {win*100:.0f}%",
                         help="Model probability this side settles to $1. Must clear "
                              f"{safe_min*100:.0f}% to be eligible here.")
            hc[1].metric("Cost (ask)", cents(ask),
                         help="What you pay now. Pays 100¢ at settlement if it wins.")
            hc[2].metric("Edge", f"+{h_edge*100:.0f}pp",
                         help="Model win-prob minus the ask — how underpriced it is.")
            hc[3].metric("Loss chance", f"{(1-win)*100:.0f}%",
                         help="Model probability it settles to $0 (your whole risk).")
            st.caption(
                f"Ranked by risk-adjusted return (edge ÷ outcome volatility), so it "
                f"favors confident, fairly-priced bets over cheap longshots. Hold to "
                f"settlement and the spread costs nothing. If it wins it returns "
                f"**+{win_ret*100:.0f}%** ({cents(ask)}→100¢); expected return is "
                f"**+{ev_cost*100:.0f}%** per dollar after the {(1-win)*100:.0f}% loss "
                f"chance. Must clear {safe_min*100:.0f}% model win-prob and "
                "positive edge.")
        else:
            st.caption(f"No contract clears the {safe_min*100:.0f}% "
                       "win-probability + positive-edge bar right now — no low-risk "
                       "hold available (the market isn't underpricing a safe side).")


def _render_accuracy(load_accuracy, calib=None):
    """The '📊 Model accuracy' expander body — backtest table + reliability charts
    + live self-scoring. `load_accuracy` is the cached () -> (bt, live) callable."""
    bt, live = load_accuracy()
    corr = calibration.active_corrections(calib)
    if corr:
        st.markdown("**Active self-corrections** — adjustments the model has "
                    "learned from its own settled forecasts and is applying now: "
                    + "; ".join(corr) + ".")
    if not bt:
        st.caption("Backtest unavailable (archive fetch failed).")
    else:
        st.markdown("**Backtest** — replays the pipeline over recent settled days. "
                    "Brier/CRPS lower = better; coverage should track its target.")
        mrows = []
        for var, m in bt.items():
            mrows.append({
                "variable": var, "days": m["n_days"],
                "exact bin": f"{m['exact_peak']:.0f}%", "within ±1°F": f"{m['within1']:.0f}%",
                "Brier": m["brier"], "CRPS": m["crps"],
                "MAE °F": m["mae"], "MAE base": m["mae_baseline"],
                "50% cov": f"{m['coverage_50']:.0f}%", "80% cov": f"{m['coverage_80']:.0f}%",
            })
        st.dataframe(pd.DataFrame(mrows).set_index("variable"), width="stretch")
        st.caption("**exact bin** = how often the model's top (peak) bin is the exact "
                   "settled degree; **within ±1°F** forgives a one-degree miss. These come "
                   "from the deterministic backtest with a flat spread and no same-day "
                   "anchoring, so treat them as a *relative* A/B harness (config vs config "
                   "on the same days), not the live hit rate — see live self-scoring below.")
        st.markdown(
            "**How to read this table** — each row scores the model's high (or low) "
            "predictions over the last *days* settled days:\n"
            "- **Brier** — accuracy of the per-bin probabilities (0 = perfect, lower "
            "is better). Penalizes being both wrong *and* confident.\n"
            "- **CRPS** — like Brier but aware of *how far off* in degrees, so a near "
            "miss is forgiven more than a big one. Lower is better.\n"
            "- **MAE °F** — average error of the single best-guess (consensus) "
            "temperature, in degrees. **MAE base** is the same for a dumb no-bias, "
            "wide-spread baseline; **MAE °F should be lower than MAE base** — that gap "
            "is what the calibration buys you.\n"
            "- **50% cov / 80% cov** — how often the actual temperature landed inside "
            "the model's stated 50% / 80% range. These should sit *near* 50% and 80%. "
            "Much higher = the model is too cautious (ranges too wide); much lower = "
            "overconfident (ranges too tight)."
        )
        st.caption("Rule of thumb: lower Brier/CRPS/MAE = sharper and more accurate; "
                   "coverage near its target = honest uncertainty.")
        rc = st.columns(2)
        for i, var in enumerate(("high", "low")):
            rdf = reliability_df(bt.get(var, {}).get("reliability"))
            if rdf is not None:
                rc[i].caption(f"{var.title()} reliability — predicted vs observed")
                rc[i].line_chart(rdf, height=220)
        st.caption("**Reliability charts:** x = the probability the model gave, y = how "
                   "often it actually happened. The closer the *observed* line hugs the "
                   "*ideal* diagonal, the better calibrated the model — e.g. things it "
                   "called 30% likely should happen ~30% of the time.")

    if live and live.get("n_settled"):
        st.markdown(f"**Live self-scoring** — {live['n_settled']} settled predictions "
                    "logged from this dashboard (grows daily). This is the *honest* "
                    "exact-bin hit rate: the full live pipeline, graded against settlement.")

        def _pct(v):
            return f"{v:.0f}%" if v is not None else "—"

        lrows = [{"variable": var, "days": m["n"],
                  "exact bin": _pct(m.get("exact_peak")),
                  "within ±1°F": _pct(m.get("within1")), "Brier": m["brier"]}
                 for var, m in live.get("by_variable", {}).items()]
        if lrows:
            st.dataframe(pd.DataFrame(lrows).set_index("variable"), width="stretch")

        # Per-lead breakout: same-day (anchored) vs day-ahead exact-bin accuracy.
        lead_names = {0: "same-day", 24: "day-ahead", 36: "2-day"}
        leadrows = []
        for bucket, vars_ in sorted(live.get("by_lead", {}).items(), key=lambda kv: int(kv[0])):
            for var, m in vars_.items():
                leadrows.append({
                    "lead": lead_names.get(int(bucket), f"{bucket}h"), "variable": var,
                    "days": m["n"], "exact bin": _pct(m.get("exact_peak")),
                    "within ±1°F": _pct(m.get("within1")),
                })
        if leadrows:
            st.caption("Exact-bin accuracy by lead time — same-day is anchored to live "
                       "observations, so it should beat day-ahead.")
            st.dataframe(pd.DataFrame(leadrows).set_index(["lead", "variable"]),
                         width="stretch")

        mkt = live.get("market")
        if mkt and mkt.get("n"):
            st.markdown(f"**Market vs model** — {mkt['n']} settled days where the live "
                        "Kalshi price was logged. Point-forecast error (°F) of the "
                        "market's implied temperature vs the model's consensus, against "
                        "CLI settlement. Lower is better.")
            mrows = [{"variable": var, "days": m["n"],
                      "model MAE": m["model_mae"], "market MAE": m["market_mae"],
                      "market closer": f"{m['market_closer_pct']:.0f}%"}
                     for var, m in mkt.get("by_variable", {}).items()]
            if mrows:
                st.dataframe(pd.DataFrame(mrows).set_index("variable"), width="stretch")
            st.caption("If *market MAE* beats *model MAE* (and 'market closer' > 50%), the "
                       "market is the sharper forecast and deserves weight; if not, the "
                       "model's independence is the edge. Builds as days settle.")
    else:
        st.caption("Live self-scoring will appear here once logged predictions "
                   "start settling (one day's lead).")


def render_page(snap, calib, adapter, load_accuracy):
    """Draw the full dashboard body for one market. `snap`/`calib` come from the
    cached snapshot loader; `adapter` selects the exchange; `load_accuracy` is the
    cached () -> (bt, live) callable for the accuracy expander."""
    st_autorefresh(interval=60_000, key=f"refresh_{adapter.name}")

    st.title(f"🌡️  {STATION_ID} Daily High / Low — {adapter.name} ({adapter.exchange})")

    cur = snap.get("current")
    ki = _kalshi_implied(snap["today"]["day"])      # Kalshi market-implied hi/lo (today)
    top = st.columns(6)
    if cur:
        top[0].metric("Current temp", f"{cur['temp']}°F", help=f"as of {cur['time']}")
    top[1].metric("Kalshi high (mkt)",
                  f"{ki['high']:.1f}°F" if ki.get("high") is not None else "—",
                  help="Today's market-implied expected high, from Kalshi's live "
                       "contract ladder (shown on both pages for reference).")
    top[2].metric("Kalshi low (mkt)",
                  f"{ki['low']:.1f}°F" if ki.get("low") is not None else "—",
                  help="Today's market-implied expected low, from Kalshi's live "
                       "contract ladder (shown on both pages for reference).")
    top[3].metric("Updated", snap["updated"].split("T")[1])
    if calib:
        top[4].metric("Calib bias (hi/lo)",
                      f"{calib['bias']['deterministic']['high']:+.1f}/"
                      f"{calib['bias']['deterministic']['low']:+.1f}°F")
        top[5].metric("Day-ahead σ (hi/lo)",
                      f"{calib['sigma']['high']:.1f}/{calib['sigma']['low']:.1f}°F")

    day = st.sidebar.radio("Day", ["Today", "Tomorrow"], index=0,
                           key=f"day_{adapter.name}")
    st.sidebar.caption("Tomorrow = pure forecast (no observations yet), so wider. "
                       "Best for the early-morning low before bed.")

    safe_pct = st.sidebar.slider(
        "🛡️ Safe-hold risk floor", min_value=int(adapter.safe_hold_min * 100),
        max_value=95, value=int(adapter.safe_hold_default * 100), step=5,
        format="%d%%", key=f"safe_{adapter.name}",
        help="Minimum model win-probability for the 'Safest hold to $1' box. Higher = "
             "only surface more certain bets (fewer, safer); lower = allow more "
             "candidates (more reward, more risk).")
    safe_min = safe_pct / 100
    st.sidebar.caption(f"Safe-hold box shows the best bet with ≥{safe_pct}% model "
                       "win-probability and positive edge, held to settlement.")

    key = "today" if day == "Today" else "tomorrow"
    pred = snap[key]

    st.subheader(f"{day} — {pred['day']}")
    # Feature the low on Tomorrow (the user's primary before-bed bet).
    feature_low = (key == "tomorrow")
    cols = st.columns(2)
    today_iso = snap["today"]["day"]
    render_variable(cols[0], "High", pred["high"], "high", pred["day"], adapter,
                    featured=not feature_low, safe_min=safe_min, today_iso=today_iso)
    render_variable(cols[1], "Low", pred["low"], "low", pred["day"], adapter,
                    featured=feature_low, safe_min=safe_min, today_iso=today_iso)

    with st.expander("Per-source breakdown"):
        src = snap["sources"][key]
        rows = []
        for group, members in src.items():
            for label, (hi, lo) in sorted(members.items()):
                rows.append({"group": group, "source": label, "high": hi, "low": lo})
        if rows:
            sdf = pd.DataFrame(rows)
            st.caption(f"{len(sdf)} series across {sdf['group'].nunique()} groups "
                       "(ensemble members aggregated into the distribution above).")
            st.dataframe(sdf.set_index("source"), width="stretch", height=300)

    with st.expander("📊 Model accuracy"):
        if adapter.accuracy_note:
            st.caption(adapter.accuracy_note)
        _render_accuracy(load_accuracy, calib)

    st.caption(adapter.settle_footer)
