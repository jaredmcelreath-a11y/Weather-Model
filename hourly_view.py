"""Hourly page — mirrors Wunderground's hourly forecast (The Weather Company
feed) for any city Kalshi lists temperature contracts on, styled to match the
rest of the dashboard. A temperature chart on top, the detailed hourly table
below, and the selected city's official current temperature — plus, for Dallas,
the Euless PWS as a fast "live" reference.

Every displayed time is in the selected city's own zone: the page spans four US
timezones, so nothing here may fall back to the project default."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import hourly_cities
import market_view

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


def freshest(nws: dict | None, metar: dict | None, tzname: str) -> dict | None:
    """The newer of the two readings, localised to `tzname`.

    Both carry the same station's temperature and differ only in delivery: the
    5-minute api.weather.gov feed lands ~20 minutes late and in whole degrees
    Celsius, the routine :53 METAR in ~2 minutes and in tenths. Newest wins,
    and a tie goes to the METAR as the finer of the two.

    `tzname` rather than the project default because this page spans four
    timezones — a Miami reading must not be stamped Central."""
    best = None
    for reading in (nws, metar):          # METAR second, so it wins a tie
        if reading and (best is None or reading["time"] >= best["time"]):
            best = reading
    if best is None:
        return None
    return {"temp": best["temp"],
            "time": best["time"].astimezone(ZoneInfo(tzname))}


def _current(city) -> dict | None:
    """Official current temp for the city = its station's newest reading
    (display only, no settlement logic), converted to the city's own zone.

    Two feeds carry it; see `freshest` for which one wins."""
    from sources import metar_tgftp, nws_observations
    return freshest(nws_observations.latest(city.station),
                    metar_tgftp.latest_for_id(city.station),
                    city.timezone)


def _temp_chart(rows: list[dict], series_colors=None):
    """Temp/Feels line chart. `series_colors` recolors [Temp, Feels] for the
    active palette (Charcoal: green + cream); None keeps Vega's defaults."""
    frame = chart_frame(rows)
    df = pd.DataFrame([{**r, "time": r["time"].replace(tzinfo=None)} for r in frame])
    temps = [r["degF"] for r in frame]
    lo, hi = min(temps) - 3, max(temps) + 3
    scale = (alt.Scale(domain=["Temp", "Feels"], range=series_colors)
             if series_colors else alt.Undefined)
    return (alt.Chart(df).mark_line(strokeWidth=2.5, clip=True).encode(
                x=alt.X("time:T", title=None,
                        axis=alt.Axis(format="%-I %p", labelAngle=-40,
                                      labelOverlap=True)),
                y=alt.Y("degF:Q", title="°F", scale=alt.Scale(domain=[lo, hi])),
                color=alt.Color("series:N", scale=scale,
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
  html,body{height:100%;margin:0;background:__BG__;
    font-family:'Bitter',Georgia,serif;}
  #map{position:absolute;inset:0;border-radius:10px;background:__BG__;}
  .bar{position:absolute;top:10px;left:10px;z-index:500;display:flex;
    align-items:center;gap:8px;background:__SURFACE__;
    border:1px solid __BORDER__;border-radius:8px;
    padding:5px 9px;color:__INK__;font-size:13px;}
  .bar button{cursor:pointer;background:transparent;border:none;color:__INK__;
    font-size:15px;line-height:1;padding:0 2px;}
  .bar input[type=range]{width:150px;accent-color:__ACCENT__;cursor:pointer;
    vertical-align:middle;}
  #ts{min-width:104px;display:inline-block;}
  .fc{color:__ACCENT_STRONG__;font-weight:700;letter-spacing:0.03em;}
  .msg{position:absolute;top:50%;left:0;right:0;text-align:center;z-index:400;
    color:__MUTED__;font-size:14px;display:none;}
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
  // Warm the base map toward the charcoal theme with a translucent tint on its
  // own pane — above the base tiles but below the radar (which sits on a higher
  // pane and keeps its true precipitation colors). The marker rides on top.
  map.createPane('tint');  map.getPane('tint').style.zIndex = 350;
  map.getPane('tint').style.pointerEvents = 'none';
  map.createPane('radar'); map.getPane('radar').style.zIndex = 400;
  map.createPane('mark');  map.getPane('mark').style.zIndex = 500;
  L.rectangle([[-89, -180], [89, 180]], {pane:'tint', stroke:false,
    fillColor:'__BG__', fillOpacity:0.55, interactive:false}).addTo(map);
  L.circleMarker([__LAT__, __LON__], {pane:'mark', radius:4, color:'__ACCENT__',
    weight:2, fill:true, fillColor:'__ACCENT__', fillOpacity:0.9}).addTo(map);

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
        {opacity:0, maxZoom:19, tileSize:256, pane:'radar'}).addTo(map);
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


def _radar_html(lat: float, lon: float, zoom: int = 7,
                palette: dict | None = None) -> str:
    """Self-contained dark Leaflet radar (RainViewer past loop + ~30 min nowcast),
    fetched client-side so the Python page never depends on RainViewer being up.
    `palette` is a market_view THEMES entry so the control bar, slider, and base
    map match the active dashboard theme; defaults to Charcoal."""
    p = palette or market_view.THEMES["Charcoal"]
    return (_RADAR_TEMPLATE
            .replace("__LAT__", str(lat))
            .replace("__LON__", str(lon))
            .replace("__ZOOM__", str(zoom))
            .replace("__BG__", p["bg"])
            .replace("__SURFACE__", p["surface"])
            .replace("__BORDER__", p["border"])
            .replace("__INK__", p["ink"])
            .replace("__MUTED__", p["muted"])
            .replace("__ACCENT_STRONG__", p["accent_strong"])
            .replace("__ACCENT__", p["accent"]))


def cli_report_box(cli, tz=None):
    """(value, issued_caption) for the NWS climate-report box, or None.

    `cli` is the city's parsed CLI report (nws_cli.fetch_latest_for) or None;
    `tz` is the city's zone, since the parser stamps `issued` in the project
    default and a Pacific city must not read its report time as Central."""
    if not cli:
        return None
    value = f'{cli["high_f"]:g}° / {cli["low_f"]:g}°'
    issued = cli["issued"]
    if tz is not None:
        issued = issued.astimezone(tz)
    return value, issued.strftime("%-I:%M %p")


def render(load_hourly, cli_report=None, city=None):
    """Draw the Hourly page. `load_hourly` is the cached () -> (rows, pws)
    callable where `rows` is wunderground.hourly_at() and `pws` is
    wunderground.pws_current() (None for every city but Dallas). `cli_report` is
    the city's parsed CLI report (or None). `city` is an hourly_cities.HourlyCity;
    None means the default city."""
    c = city or hourly_cities.city(hourly_cities.DEFAULT_KEY)
    tz = ZoneInfo(c.timezone)
    market_view._theme_controls()  # sidebar Settings (theme picker) + injects theme
    theme = market_view._seed_theme()
    st_autorefresh(interval=60_000, key="refresh_hourly")
    st.title(f"{c.name} Hourly")
    st.caption(f"Tracking Wunderground's {c.station} hourly forecast "
               "(The Weather Company).")

    cur = _current(c)
    rows, pws = [], None
    try:
        rows, pws = load_hourly()
    except Exception:
        st.warning("Wunderground's hourly feed is unavailable right now — showing "
                   "the current temperature only.")

    cur_val = f"{cur['temp']:.0f}°F" if cur else _EM
    cur_cap = cur["time"].strftime("%-I:%M %p") if cur else None
    official_card = market_view.metric_card(
        f"{c.station} (official)", cur_val,
        help_text=f"Latest {c.station} ASOS reading — the official station this "
        "city's Kalshi market settles on.")
    # Only KDFW has a configured live PWS (Euless). Other cities show just the
    # official reading in a single full-width box rather than a dead '—' card.
    if pws is not None:
        pws_val = f"{pws['temp']:.0f}°F" if pws.get("temp") is not None else _EM
        pws_cap = pws["obs_time"].astimezone(tz).strftime("%-I:%M %p")
        # Wrap in a metrics2_ container so the boxes and their tap tooltips get the
        # shared mobile treatment (2-per-row ≤640px; tooltip as a fixed bottom sheet
        # that never clips off-screen). See the metrics2_ CSS in market_view.
        with st.container(key="metrics2_hourly"):
            cols = st.columns(2)
        cols[0].markdown(official_card, unsafe_allow_html=True)
        cols[1].markdown(
            market_view.metric_card("Euless PWS (live)", pws_val,
                                     help_text="A nearby backyard weather station "
                                     "(KTXEULES41). Updates faster than the airport but "
                                     "can differ by a degree or two."),
            unsafe_allow_html=True)
        if cur_cap or pws_cap:
            st.caption(f"{c.station} as of {cur_cap or _EM} · PWS as of {pws_cap or _EM}")
    else:
        st.markdown(official_card, unsafe_allow_html=True)
        if cur_cap:
            st.caption(f"{c.station} as of {cur_cap}")

    # Official NWS climate report box, under the live-reading boxes. Only appears
    # once today's afternoon CLI is issued — it reports the day's now-settled
    # high/low, the basis Kalshi resolves on.
    cli_box = cli_report_box(cli_report, tz)
    if cli_box:
        value, issued = cli_box
        st.markdown(
            market_view.metric_card("NWS Climate Report", value,
                                     help_text=f"Official NWS CLI{c.cli_location} daily "
                                     "climate report — today's settled high / low, the "
                                     "basis Kalshi resolves on. Issued mid-afternoon."),
            unsafe_allow_html=True)
        st.caption(f"Climate report as of {issued}")

    if not rows:
        return
    st.altair_chart(_temp_chart(rows, market_view._series_colors()),
                    use_container_width=True)
    today = datetime.now(tz).date()
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
    palette = market_view.THEMES.get(theme, market_view.THEMES[market_view.DEFAULT_THEME])
    components.html(_radar_html(lat=c.lat, lon=c.lon, palette=palette), height=460)
