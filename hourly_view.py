"""Hourly page — mirrors Wunderground's KDFW hourly forecast (The Weather
Company feed), styled to match the rest of the dashboard. A temperature chart on
top, the detailed hourly table below, and two current-temp tiles: the official
KDFW airport reading plus the Euless PWS as a fast "live" reference."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import market_view
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)

_EM = "—"


def fmt_temp(v) -> str:
    return f"{v:.0f}°" if v is not None else _EM


def fmt_pct(v) -> str:
    return f"{v:.0f}%" if v is not None else _EM


def fmt_wind(mph, direction) -> str:
    if mph is None:
        return _EM
    return f"{direction} {mph:.0f}".strip()


def day_label(dt: datetime, today) -> str:
    """'Today' / 'Tomorrow' for the two betting days, else the weekday name."""
    delta = (dt.date() - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return dt.strftime("%A")


def chart_frame(rows: list[dict]) -> list[dict]:
    """Long-form records for the Altair chart: one Temp and one Feels point per
    hour, skipping hours with a missing value."""
    out = []
    for r in rows:
        for series, key in (("Temp", "temp"), ("Feels", "feels")):
            v = r.get(key)
            if v is not None:
                out.append({"time": r["time"], "series": series, "degF": v})
    return out


def _kdfw_current() -> dict | None:
    """Official KDFW current temp = latest 5-minute ASOS reading (display only,
    no settlement logic), matching the Forecast page's Current Temp source."""
    try:
        from sources import nws_observations
        data = nws_observations.fetch(continuous=True)
        times, temps = data.get("obs_continuous") or data["obs"]
        if temps:
            return {"temp": temps[-1], "time": times[-1]}
    except Exception:
        return None
    return None


def _temp_chart(rows: list[dict]):
    frame = chart_frame(rows)
    df = pd.DataFrame([{**r, "time": r["time"].replace(tzinfo=None)} for r in frame])
    temps = [r["degF"] for r in frame]
    lo, hi = min(temps) - 3, max(temps) + 3
    return (alt.Chart(df).mark_line(strokeWidth=2.5, clip=True).encode(
                x=alt.X("time:T", title=None,
                        axis=alt.Axis(format="%-I %p", labelAngle=-40,
                                      labelOverlap=True)),
                y=alt.Y("degF:Q", title="°F", scale=alt.Scale(domain=[lo, hi])),
                color=alt.Color("series:N",
                                legend=alt.Legend(title=None, orient="top")))
            .properties(height=240, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


_TABLE_COLS = ["Time", "Temp", "Feels", "Dew", "Rain %", "Cloud", "Wind", "Humidity"]


def _day_tables(rows: list[dict], today) -> list[dict]:
    """Group the hours into one day per section (the feed is chronological, so
    grouping consecutive rows suffices). Each item is a dict with the day `label`,
    the forecast `high`/`low` across that day's shown hours (None if all temps
    missing), and a display-string `df`. The day is the section header, not a
    column."""
    groups: list[dict] = []
    for r in rows:
        label = day_label(r["time"], today)
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "temps": [], "recs": []})
        g = groups[-1]
        if r.get("temp") is not None:
            g["temps"].append(r["temp"])
        g["recs"].append({
            "Time": r["time"].strftime("%-I %p"),
            "Temp": fmt_temp(r.get("temp")),
            "Feels": fmt_temp(r.get("feels")),
            "Dew": fmt_temp(r.get("dew")),
            "Rain %": fmt_pct(r.get("precip_pct")),
            "Cloud": fmt_pct(r.get("cloud_pct")),
            "Wind": fmt_wind(r.get("wind_mph"), r.get("wind_dir")),
            "Humidity": fmt_pct(r.get("humidity")),
        })
    return [{
        "label": g["label"],
        "high": max(g["temps"]) if g["temps"] else None,
        "low": min(g["temps"]) if g["temps"] else None,
        "df": pd.DataFrame(g["recs"], columns=_TABLE_COLS),
    } for g in groups]


def render(load_hourly):
    """Draw the Hourly page. `load_hourly` is the cached () -> (rows, pws) callable
    where `rows` is wunderground.hourly() and `pws` is wunderground.pws_current()."""
    market_view._inject_theme(market_view._seed_theme())
    st_autorefresh(interval=60_000, key="refresh_hourly")
    st.title("Hourly")
    st.caption("Tracking Wunderground's KDFW hourly forecast (The Weather Company).")

    kdfw = _kdfw_current()
    rows, pws = [], None
    try:
        rows, pws = load_hourly()
    except Exception:
        st.warning("Wunderground's hourly feed is unavailable right now — showing "
                   "the current temperature only.")

    kdfw_val = f"{kdfw['temp']:.0f}°F" if kdfw else _EM
    pws_val = f"{pws['temp']:.0f}°F" if pws and pws.get("temp") is not None else _EM
    kdfw_cap = kdfw["time"].strftime("%-I:%M %p") if kdfw else None
    pws_cap = pws["obs_time"].astimezone(TZ).strftime("%-I:%M %p") if pws else None
    # Wrap in a metrics2_ container so the boxes and their tap tooltips get the
    # shared mobile treatment (2-per-row ≤640px; tooltip as a fixed bottom sheet
    # that never clips off-screen). See the metrics2_ CSS in market_view.
    with st.container(key="metrics2_hourly"):
        cols = st.columns(2)
    cols[0].markdown(
        market_view.metric_card("KDFW (official)", kdfw_val,
                                 help_text="Latest KDFW airport ASOS reading — the "
                                 "official station the model and Kalshi settle on."),
        unsafe_allow_html=True)
    cols[1].markdown(
        market_view.metric_card("Euless PWS (live)", pws_val,
                                 help_text="A nearby backyard weather station "
                                 "(KTXEULES41). Updates faster than the airport but "
                                 "can differ by a degree or two."),
        unsafe_allow_html=True)
    if kdfw_cap or pws_cap:
        st.caption(f"KDFW as of {kdfw_cap or _EM} · PWS as of {pws_cap or _EM}")

    if not rows:
        return
    st.altair_chart(_temp_chart(rows), use_container_width=True)
    today = datetime.now(TZ).date()
    for t in _day_tables(rows, today):
        hi = f"{t['high']:.0f}°" if t["high"] is not None else _EM
        lo = f"{t['low']:.0f}°" if t["low"] is not None else _EM
        st.subheader(t["label"])
        st.caption(f"High {hi} · Low {lo} (forecast for the hours shown)")
        market_view._html_table(t["df"])
