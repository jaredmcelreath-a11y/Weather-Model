# Hourly Page Radar — Design

**Date:** 2026-07-17
**Status:** Approved (design), implementing

## Goal

Add an animated storm radar to the bottom of the Hourly page: a loop of the
recent past radar (storms moving) that continues into a short-range forecast
nowcast (future movement), dark-themed to match the dashboard.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Source | RainViewer (free, no key). Past ~1-2h + ~30 min nowcast. Fully styleable. |
| Placement | Very bottom of the page, below the day tables. |
| Coverage | Regional, KDFW-centered, ~150 mi view (zoom ~7). |
| Future horizon | ~30 min (RainViewer nowcast). Longer horizons (Windy) declined. |

## Architecture

No new backend / `sources/` module. The radar is a self-contained Leaflet map
embedded via `streamlit.components.v1.html`. All radar data is fetched
**client-side in the browser** directly from RainViewer, so the Python page makes
no new network call and cannot break if RainViewer is down (the map degrades to
the base map + a small "radar unavailable" note handled in JS).

### `hourly_view.py`

- **`_radar_html(lat=KDFW_LAT, lon=KDFW_LON, zoom=7) -> str`** — pure string
  builder returning the full HTML document for the component:
  - Leaflet CSS/JS from unpkg CDN.
  - Dark base map: CartoDB `dark_all` tiles (with required attribution).
  - Map centered on `(lat, lon)` at `zoom`.
  - JS `fetch("https://api.rainviewer.com/public/weather-maps.json")` → uses
    `radar.past` + `radar.nowcast` frame lists. Each frame's tile URL is
    `${host}${frame.path}/256/{z}/{x}/{y}/<color>/1_1.png` (Leaflet fills
    `{z}/{x}/{y}`), overlaid at ~0.7 opacity.
  - Animation: cycle past→nowcast frames on a ~500 ms timer; a play/pause button
    and a timestamp label that shows a "FORECAST" tag while on nowcast frames.
  - `fetch` failure → show "Radar unavailable" text over the base map.
- **`render()`** — at the very bottom, after the day tables:
  `st.subheader("Radar")`, a one-line caption (past loop + ~30 min forecast),
  then `components.html(_radar_html(), height=460)`.

### Constants

`KDFW_LAT = 32.90`, `KDFW_LON = -97.04` (reuse the geocode already used by
`sources/wunderground.py`; define locally in the view to avoid a source import
just for two numbers).

## Auto-refresh interaction

The page's 60s `st_autorefresh` remounts the component each cycle, restarting the
animation. Because frames are fetched client-side on mount, the radar stays
current; the only cost is a brief loop restart each minute. Accepted; noted in a
code comment.

## Error handling

- Python: none needed — `_radar_html` is a pure string; `components.html` embeds
  it. No new server-side call.
- Client: `fetch` errors are caught in JS and render an unobtrusive message; the
  base map still displays.

## Testing

- **`tests/test_hourly_view.py`** — assert `_radar_html()` contains: the
  RainViewer API endpoint, the KDFW coordinates, the Leaflet CDN, the CartoDB
  dark base-tile URL, and the play/pause control hook. Assert custom `lat/lon/zoom`
  args propagate into the string. Existing render tests continue to cover the
  render path (components stubbed in the dev env).

## Out of scope (YAGNI)

- Hours-ahead model radar (Windy/HRRR).
- Satellite/other overlays, location search, or user-movable center.
- Server-side caching of radar frames (fetched client-side).
