"""Status page — log-derived system health.

`checks` is a pure function of plain timestamps/counts (assembled by
app.load_status + the cached snapshot) so every green/amber/red threshold is
unit-testable; render is dumb. No new credentials: everything comes from data
the dashboard already reads.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

import market_view

GREEN, AMBER, RED, UNKNOWN = "green", "amber", "red", "unknown"
_DOT = {GREEN: "🟢", AMBER: "🟡", RED: "🔴", UNKNOWN: "⚪"}


def _fmt_age(age_min: float) -> str:
    if age_min < 90:
        return f"{age_min:.0f} Min Ago"
    if age_min < 48 * 60:
        return f"{age_min / 60:.1f} H Ago"
    return f"{age_min / 1440:.1f} D Ago"


def _age_card(label: str, age_min, green_lt: float, amber_lt: float,
              tip: str) -> dict:
    if age_min is None:
        return {"label": label, "value": "No Data", "state": UNKNOWN, "tip": tip}
    state = GREEN if age_min < green_lt else AMBER if age_min < amber_lt else RED
    return {"label": label, "value": _fmt_age(age_min), "state": state,
            "tip": tip}


def checks(inputs: dict, now: datetime) -> list[dict]:
    """Health cards from plain inputs; a missing input reads ⚪ unknown rather
    than guessing. Thresholds are the spec's table."""
    def age(dt):
        return None if dt is None else max(0.0, (now - dt).total_seconds() / 60)

    out = [
        _age_card("Action Heartbeat", age(inputs.get("last_capture")), 25, 60,
                  "Minutes since the scheduled Action's last consensus "
                  "capture. Green under 25 min (10-min cadence); red past an "
                  "hour means the Action or its trigger is down."),
        _age_card("Obs Reading", age(inputs.get("obs_time")), 45, 90,
                  "Age of the newest KDFW temperature reading. Red means at "
                  "least one full METAR cycle was missed (the IEM fallback "
                  "kicks in on NWS outages)."),
    ]
    dropped = inputs.get("dropped_sources")
    if dropped is None:
        out.append({"label": "Forecast Feeds", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Whether every forecast source answered on the "
                           "latest snapshot."})
    else:
        state = GREEN if not dropped else AMBER if len(dropped) == 1 else RED
        value = "All Live" if not dropped else f"{len(dropped)} Down"
        tip = ("Every forecast source answered on the latest snapshot."
               if not dropped else "Down: " + ", ".join(dropped) +
               ". The consensus runs on the remaining sources.")
        out.append({"label": "Forecast Feeds", "value": value, "state": state,
                    "tip": tip})
    out.append(_age_card(
        "Calibration", age(inputs.get("calib_computed")), 36 * 60, 72 * 60,
        "Age of the last calibration recompute (~1×/day when healthy). Red "
        "means the model is running on stale bias/sigma/weights."))
    last = inputs.get("last_settled")
    if last is None:
        out.append({"label": "Settlements", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Most recent day with a recorded CLI settlement."})
    else:
        behind = (now.date() - last).days
        state = GREEN if behind <= 1 else AMBER if behind == 2 else RED
        out.append({"label": "Settlements",
                    "value": f"Through {last.strftime('%b %-d')}",
                    "state": state,
                    "tip": "Most recent day with a recorded CLI settlement. "
                           "Green = settled through yesterday."})
    bt = inputs.get("betting_rows_today")
    if bt is None:
        out.append({"label": "Betting Log", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Betting-time rows captured for today's slots."})
    else:
        out.append({"label": "Betting Log", "value": f"{bt} Rows Today",
                    "state": GREEN if bt > 0 else RED,
                    "tip": "Betting-time rows captured for today's slots "
                           "(morning low + afternoon high). Zero by midday "
                           "means slot capture is broken."})
    return out


def snapshot_inputs(snap: dict | None) -> dict:
    """The live-snapshot-derived check inputs (obs freshness + feed health).
    Pure and total: a missing/partial snapshot contributes nothing rather
    than crashing the page."""
    if not snap:
        return {}
    out: dict = {}
    t = (snap.get("current") or {}).get("time")
    if t:
        try:
            out["obs_time"] = datetime.fromisoformat(t)
        except ValueError:
            pass
    if "dropped_sources" in snap:
        out["dropped_sources"] = snap.get("dropped_sources") or []
    return out


def render(snap: dict | None, inputs: dict, counts: dict) -> None:
    import pandas as pd
    from zoneinfo import ZoneInfo

    from config import TIMEZONE

    market_view._theme_controls()
    st.title("Status")
    st.caption("Log-derived health: every check reads the same data the "
               "dashboard already loads — no extra credentials or probes.")
    now = datetime.now(ZoneInfo(TIMEZONE))
    merged = dict(inputs)
    merged.update(snapshot_inputs(snap))
    cards = checks(merged, now)
    with st.container(key="metrics2_status"):
        c = st.columns(3)
    for i, card in enumerate(cards):
        c[i % 3].markdown(market_view.metric_card(
            card["label"], f'{_DOT[card["state"]]} {card["value"]}',
            card["tip"]), unsafe_allow_html=True)
    if counts:
        st.subheader("Log Sizes")
        market_view._html_table(pd.DataFrame(
            [{"Log": k, "Rows": str(v)} for k, v in sorted(counts.items())]))
        st.caption("Row counts of the persisted data logs. Steady growth is "
                   "healthy; a frozen count means the Action stopped writing.")
