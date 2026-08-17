"""The Timeseries page — 36 hours of a city's own observations.

Mirrors what weather.gov/wrh/timeseries shows, on our theme and in one place
for all twenty cities. That NWS page is a front end over the Synoptic API using
a token embedded in NWS's own site; we read the same 5-minute MADIS data
straight from api.weather.gov, which needs no key.

Two kinds of row arrive in that one feed and they are NOT equally precise. The
routine ~:53 METAR carries tenths of a degree and its raw text; the 5-minute
readings are whole degC and carry neither. A whole-degC value displays at the
BOTTOM of its bucket -- 38C renders 100.4F when the true reading is anywhere
below 102.2F -- so it can read up to 1.8F low, and it cannot represent 101F at
all. That is the settlement wall this page exists to watch, so the Feed column
names which feed every row came from rather than letting them blur together.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_autorefresh import st_autorefresh

import hourly_cities
import market_view

_EM = "—"

# 16-point compass, in the order the bearing divides into.
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_MPH_PER_KMH = 0.621371


# How far off the whole-degC grid a value must sit to prove it carries tenths.
# The feed states Celsius natively, so this is a float-noise guard, not a
# conversion tolerance -- the same role the matching constant plays in
# screen_rules._reading_slack_f.
_WHOLE_C_TOLERANCE = 0.05


def is_hourly(props: dict) -> bool:
    """Whether this row is the routine METAR rather than a 5-minute reading.

    PRECISION is the discriminator, not the raw text and not the minute of the
    timestamp. `rawMessage` looks like the obvious answer and is wrong: it LAGS
    the numeric fields by up to an hour. Measured 2026-08-16 across KDFW, KLAS
    and KATL, the newest METAR carried tenths with an empty rawMessage while the
    one an hour older carried its full text --

        KDFW 22:53  temp=38.9  rawlen=0
        KDFW 21:53  temp=38.9  rawlen=69

    -- so keying off raw text alone mislabels the newest METAR, which is the one
    row on this page anyone is watching. Raw text still counts when it IS there;
    it just cannot be required.

    A METAR landing exactly on the whole-degC grid is unresolvable and reads as
    5-minute. That is the conservative direction, for the reason
    screen_rules._reading_slack_f gives: overstating precision the reading does
    not have is the error with a cost."""
    if ((props or {}).get("rawMessage") or "").strip():
        return True
    celsius = _value(props, "temperature")
    if celsius is None:
        return False
    return abs(float(celsius) - round(float(celsius))) >= _WHOLE_C_TOLERANCE


def compass(degrees) -> str:
    """A bearing as a 16-point compass name, or '' when there is no wind."""
    if degrees is None:
        return ""
    return _COMPASS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]


def _c_to_f(celsius):
    return None if celsius is None else round(float(celsius) * 9.0 / 5.0 + 32.0, 1)


def _value(props: dict, key: str):
    return ((props or {}).get(key) or {}).get("value")


def reading(props: dict, tz):
    """One display row in the city's own zone, or None with no temperature.

    A row without a temperature has nothing this page exists to show, so it is
    dropped rather than rendered as a line of em dashes."""
    temp = _c_to_f(_value(props, "temperature"))
    if temp is None:
        return None
    try:
        when = datetime.fromisoformat(
            str((props or {}).get("timestamp")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    speed = _value(props, "windSpeed")
    return {
        "time": when.astimezone(tz),
        "temp_f": temp,
        "dewpoint_f": _c_to_f(_value(props, "dewpoint")),
        # NWS publishes wind in km/h, not mph. Reading it raw understates every
        # gust by ~38% and looks entirely plausible.
        "wind_mph": None if speed is None else round(float(speed) * _MPH_PER_KMH, 1),
        "wind_dir": compass(_value(props, "windDirection")),
        "raw": ((props or {}).get("rawMessage") or "").strip(),
        "hourly": is_hourly(props),
    }


def readings(rows: list, tz) -> list:
    """Every usable row from a window_for_id payload, newest first."""
    out = [reading(p, tz) for p in rows or []]
    return [r for r in out if r is not None]


def day_extremes(rows: list, day: date, climate_tz: str) -> dict:
    """{'high': (temp, time), 'low': (temp, time)} over one LST climate day.

    The window is fixed standard time, never the city's local time: a climate
    day ends at 01:00 local during daylight saving, so the local date is a day
    ahead for that hour and an hour of the wrong day lands in the extreme. The
    same trap city_consensus.series_extreme documents.

    Computed from the values as PUBLISHED, so with a whole-degC row in the mix
    the high is a floor on the true high and the low a ceiling on the true low.
    The caller says so on the page.
    """
    zone = ZoneInfo(climate_tz)
    inside = [r for r in rows or []
              if r.get("temp_f") is not None
              and r["time"].astimezone(zone).date() == day]
    if not inside:
        return {"high": None, "low": None}
    hi = max(inside, key=lambda r: r["temp_f"])
    lo = min(inside, key=lambda r: r["temp_f"])
    return {"high": (hi["temp_f"], hi["time"]), "low": (lo["temp_f"], lo["time"])}


_COLUMNS = ["Time", "Temp", "Dew pt", "Wind", "Feed", "Raw METAR"]


def _table(columns: list, rows: list) -> str:
    """A themed .wtbl table from display-string row dicts.

    Deliberately its own renderer rather than screen_view's. That one is a
    private name on another display page -- importing it would point this page
    at another's internals and drag the whole Screen import graph in -- and its
    header cell binds its tooltip default to the fade table's tip map. This
    table carries no column tooltips: six self-describing columns, with the one
    caveat worth stating (precision) already a caption under the extremes.
    Same .wtbl markup, themed by market_view exactly as every other table is."""
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = []
    for r in rows:
        body.append("<tr>")
        body.append("".join(f"<td>{html.escape(str(r.get(c, '')))}</td>"
                            for c in columns))
        body.append("</tr>")
    return ('<div class="wtbl-wrap"><table class="wtbl"><thead><tr>'
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>")


def _temp(value) -> str:
    return _EM if value is None else f"{float(value):.1f}°"


def _wind(mph, direction) -> str:
    """'SW 10', 'Calm', or an em dash when the station reported no wind at all.

    Calm is named rather than printed as a bearing: a 0 mph reading arrives with
    direction 0, which the compass renders 'N', inventing a northerly the
    station never reported."""
    if mph is None:
        return _EM
    if round(float(mph)) == 0:
        return "Calm"
    return f"{direction} {mph:.0f}".strip()


def table_rows(rows: list) -> list:
    """Display-string dicts for `_table`, in the order given (newest first)."""
    return [{"Time": r["time"].strftime("%-I:%M %p"),
             "Temp": _temp(r.get("temp_f")),
             "Dew pt": _temp(r.get("dewpoint_f")),
             "Wind": _wind(r.get("wind_mph"), r.get("wind_dir") or ""),
             "Feed": "METAR" if r.get("hourly") else "5-min",
             "Raw METAR": r.get("raw") or _EM}
            for r in rows or []]


def _extreme(pair) -> str:
    """'102.2° at 4:25 PM', or an em dash when the day has none yet."""
    if not pair:
        return _EM
    value, when = pair
    return f"{float(value):.1f}° at {when.strftime('%-I:%M %p')}"


def extreme_caption(extremes: dict) -> str:
    """The climate day's running extremes, as one line."""
    return (f"High so far {_extreme((extremes or {}).get('high'))}  ·  "
            f"Low so far {_extreme((extremes or {}).get('low'))}")


def render(load_window, city=None, now=None) -> None:
    """Draw the Timeseries page.

    `load_window` is a cached () -> list of NWS observation `properties` dicts,
    newest first (nws_observations.window_for_id). `city` is an
    hourly_cities.HourlyCity; None means the default city.
    """
    from datetime import timezone as _utc

    c = city or hourly_cities.city(hourly_cities.DEFAULT_KEY)
    tz = ZoneInfo(c.timezone)
    market_view._theme_controls()      # theme CSS + .wtbl/.wtbl-wrap + Settings
    # Five minutes, matching the feed: a new MADIS reading exists only that
    # often, so a faster refresh would re-render the same table.
    st_autorefresh(interval=300_000, key="refresh_timeseries")
    st.title(f"{c.name} Timeseries")
    st.caption(f"The last 36 hours of {c.station}'s own observations, five "
               "minutes apart — the same feed weather.gov/wrh/timeseries draws.")

    try:
        rows = readings(load_window(), tz)
    except Exception as e:             # noqa: BLE001 - one dead station must
        st.info(f"No observations for {c.station} right now ({e}).")
        return
    if not rows:
        st.info(f"No observations for {c.station} in the last 36 hours.")
        return

    day = hourly_cities.climate_day(c, now or datetime.now(_utc.utc))
    st.markdown(f"#### Climate day {day:%b %-d}")
    st.caption(extreme_caption(day_extremes(rows, day, c.climate_tz)))
    st.caption("Read as published: with a whole-°C row in the mix the high is a "
               "FLOOR on the true high and the low a CEILING on the true low.")
    st.markdown(_table(_COLUMNS, table_rows(rows)), unsafe_allow_html=True)
