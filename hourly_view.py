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
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import market_view
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)

_EM = "—"

# KDFW airport — the radar's default center (same point as the wunderground geocode).
KDFW_LAT = 32.90
KDFW_LON = -97.04


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


_RADAR_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{height:100%;margin:0;background:#12151b;
    font-family:'Bitter',Georgia,serif;}
  #map{position:absolute;inset:0;border-radius:10px;background:#12151b;}
  .bar{position:absolute;top:10px;left:10px;z-index:500;display:flex;
    align-items:center;gap:8px;background:rgba(18,21,27,0.82);
    border:1px solid rgba(255,255,255,0.14);border-radius:8px;
    padding:5px 9px;color:#e9e6df;font-size:13px;}
  .bar button{cursor:pointer;background:transparent;border:none;color:#e9e6df;
    font-size:15px;line-height:1;padding:0 2px;}
  .bar input[type=range]{width:150px;accent-color:#f0b34a;cursor:pointer;
    vertical-align:middle;}
  #ts{min-width:104px;display:inline-block;}
  .fc{color:#f0b34a;font-weight:700;letter-spacing:0.03em;}
  .msg{position:absolute;top:50%;left:0;right:0;text-align:center;z-index:400;
    color:#b9b4ab;font-size:14px;display:none;}
</style></head>
<body>
<div id="map"></div>
<div class="bar"><button id="playpause" aria-label="play/pause">&#10073;&#10073;</button>
  <input type="range" id="slider" min="0" max="0" value="0" step="1"
    aria-label="radar time"><span id="ts">&hellip;</span></div>
<div class="msg" id="msg">Radar unavailable right now</div>
<script>
  // zoom control on the top-right so it doesn't sit under the top-left slider bar
  var map = L.map('map', {zoomControl:false, attributionControl:true})
              .setView([__LAT__, __LON__], __ZOOM__);
  L.control.zoom({position:'topright'}).addTo(map);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    {attribution:'&copy; OpenStreetMap, &copy; CARTO', subdomains:'abcd', maxZoom:19})
    .addTo(map);
  L.circleMarker([__LAT__, __LON__], {radius:4, color:'#f0b34a', weight:2,
    fill:true, fillColor:'#f0b34a', fillOpacity:0.9}).addTo(map);

  var host='', frames=[], layers={}, idx=0, playing=true, timer=null;
  var COLOR=4, OPTS='1_1';
  var label=document.getElementById('ts');
  var btn=document.getElementById('playpause');
  var slider=document.getElementById('slider');

  function setPlaying(p){ playing=p; btn.innerHTML=p?'&#10073;&#10073;':'&#9654;'; }

  function showFrame(i){
    var f=frames[i]; if(!f) return;
    if(!layers[f.path]){
      layers[f.path]=L.tileLayer(host+f.path+'/256/{z}/{x}/{y}/'+COLOR+'/'+OPTS+'.png',
        {opacity:0, maxZoom:19, tileSize:256}).addTo(map);
    }
    for(var k in layers){ layers[k].setOpacity(0); }
    layers[f.path].setOpacity(0.7);
    slider.value=i;
    var d=new Date(f.time*1000);
    var hh=d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    label.innerHTML=(f.forecast?'<span class="fc">FORECAST</span> ':'')+hh;
  }
  function step(){ idx=(idx+1)%frames.length; showFrame(idx); }
  function animate(){
    if(timer) clearInterval(timer);
    timer=setInterval(function(){ if(playing && frames.length) step(); }, 500);
    showFrame(0);
  }
  btn.addEventListener('click', function(){ setPlaying(!playing); });
  // Dragging the slider scrubs to that frame and pauses, so you can step at
  // your own pace; hitting play resumes the loop from where you left it.
  slider.addEventListener('input', function(){
    setPlaying(false); idx=+this.value; showFrame(idx);
  });

  fetch('https://api.rainviewer.com/public/weather-maps.json')
    .then(function(r){ return r.json(); })
    .then(function(data){
      host=data.host;
      var past=(data.radar&&data.radar.past)||[];
      var now=(data.radar&&data.radar.nowcast)||[];
      frames=past.concat(now).map(function(f){
        return {time:f.time, path:f.path, forecast: now.indexOf(f)>=0};
      });
      if(!frames.length){ document.getElementById('msg').style.display='block'; return; }
      slider.max=frames.length-1;
      animate();
    })
    .catch(function(){ document.getElementById('msg').style.display='block'; });
</script>
</body></html>"""


def _radar_html(lat: float = KDFW_LAT, lon: float = KDFW_LON, zoom: int = 7) -> str:
    """Self-contained dark Leaflet radar (RainViewer past loop + ~30 min nowcast),
    fetched client-side so the Python page never depends on RainViewer being up."""
    return (_RADAR_TEMPLATE
            .replace("__LAT__", str(lat))
            .replace("__LON__", str(lon))
            .replace("__ZOOM__", str(zoom)))


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

    # Storm radar at the very bottom. Frames are fetched client-side from
    # RainViewer, so this embeds a static HTML string — no server call, nothing
    # that can fail the page. The 60s page autorefresh remounts the component and
    # restarts the loop (it re-fetches fresh frames on each mount, so it stays
    # current); the brief restart every minute is an accepted cost.
    st.subheader("Radar")
    st.caption("Past ~2 h of storm movement, continuing into RainViewer's "
               "~30-minute forecast nowcast. Tap ⏸ to pause.")
    components.html(_radar_html(), height=460)
