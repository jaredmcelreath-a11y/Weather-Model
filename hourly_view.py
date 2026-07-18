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


def _table_df(rows: list[dict], today) -> pd.DataFrame:
    """Display-string DataFrame; the Day cell shows only on each day's first row."""
    records = []
    last_day = None
    for r in rows:
        d = day_label(r["time"], today)
        records.append({
            "Day": d if d != last_day else "",
            "Time": r["time"].strftime("%-I %p"),
            "Temp": fmt_temp(r.get("temp")),
            "Feels": fmt_temp(r.get("feels")),
            "Rain %": fmt_pct(r.get("precip_pct")),
            "Cloud": fmt_pct(r.get("cloud_pct")),
            "Wind": fmt_wind(r.get("wind_mph"), r.get("wind_dir")),
            "Hum": fmt_pct(r.get("humidity")),
        })
        last_day = d
    return pd.DataFrame(records)


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
    market_view._html_table(_table_df(rows, today))
