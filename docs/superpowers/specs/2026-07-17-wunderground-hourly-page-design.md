# Wunderground Hourly Page — Design

**Date:** 2026-07-17
**Status:** Approved (design), pending implementation plan

## Goal

Add a new dashboard page that mirrors the Wunderground hourly forecast for KDFW
(`wunderground.com/hourly/KDFW`), styled to match the existing model UI, so the
user can watch WU's hourly forecast from inside the model instead of visiting the
site. When WU's hourly numbers change, this page changes with them.

## Background / key findings

- WU's hourly page is powered by The Weather Company (TWC) API at
  `api.weather.com`. The `v3/wx/forecast/hourly/2day` endpoint returns the exact
  48-hour hourly forecast that renders on the site (temp, feels-like, precip %,
  cloud cover, humidity, wind). Verified working with the WU web app's shared API
  key.
- The PWS the user linked (`KTXEULES41`) is a hobbyist Ambient Weather station in
  a backyard in Euless, TX — **not** the official KDFW airport ASOS. It updates
  every few minutes but is a different location/sensor and can drift 1–2°F from
  the airport. It is treated strictly as a fast "live" reference, never as the
  official reading.
- The TWC feed is unofficial (accessed via the web app's embedded key). Accepted
  risk for a personal single-user dashboard; if the key ever rotates it needs a
  one-line fix. This is documented in code.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Current-temp source | Show **both**: official KDFW (anchor) + Euless PWS (live reference), side by side |
| Table columns | Temp, Feels-like, Precip %, Cloud cover, Wind, Humidity ("most of it") |
| Layout | Temperature chart on top, detailed hourly table below (mirrors WU) |
| Horizon | 48 hours (today + tomorrow), matching the default TWC feed |
| Nav placement | "Hourly" as the 2nd nav item: Forecast · Hourly · Accuracy · Edge · History |

## Architecture

Follows the existing `sources/` data-layer + view-function-per-page pattern.

### New file: `sources/wunderground.py`

Data layer. Both functions route through `sources.common.get_json`, inheriting the
on-disk TTL cache, per-host circuit breaker, and retry/backoff.

- **`hourly() -> list[dict]`**
  - `GET https://api.weather.com/v3/wx/forecast/hourly/2day`
  - params: `geocode=32.90,-97.04` (KDFW), `format=json`, `units=e`,
    `language=en-US`, `apiKey=<WEB_KEY>`
  - TWC returns parallel arrays; parse into one dict per hour with keys:
    `time` (local datetime from `validTimeLocal`), `temp` (`temperature`),
    `feels` (`temperatureFeelsLike`), `precip_pct` (`precipChance`),
    `cloud_pct` (`cloudCover`), `humidity` (`relativeHumidity`),
    `wind_mph` (`windSpeed`), `wind_dir` (`windDirectionCardinal`).
  - TTL ~300s — fresh enough to track WU without hammering the endpoint.
- **`pws_current() -> dict | None`**
  - `GET https://api.weather.com/v2/pws/observations/current`
  - params: `stationId=KTXEULES41`, `format=json`, `units=e`, `apiKey=<WEB_KEY>`
  - returns `{"temp": float, "obs_time": datetime}` from
    `observations[0].imperial.temp` / `obsTimeLocal`; `None` on empty/missing.
  - TTL ~60s — this is the "live" number.
- **`WEB_API_KEY`** — module constant with a comment: this is the WU web app's
  shared key; if TWC rotates it, refresh here.
- Official KDFW current temp is **not** fetched here. The page reads it from
  `sources.nws_observations.fetch(continuous=True)` and takes the latest 5-minute
  reading — the raw display value with no settlement/Kalshi logic, matching the
  documented "current temp = fast 5-min reading, decoupled from the settlement
  basis" preference. This keeps the page a lightweight standalone view that does
  not need to build the full model snapshot.

### New file: `hourly_view.py`

Page renderer, mirroring the structure of `accuracy_view.py`.

- **Theme:** `market_view._inject_theme(market_view._seed_theme())`, `st.title("Hourly")`.
- **Header:** two `market_view.metric_card` tiles side by side —
  "KDFW (official)" from the model's KDFW obs, and "Euless PWS (live)" from
  `wunderground.pws_current()` — each with its obs timestamp caption. Clearly
  labeled by origin so the two numbers are never conflated.
- **Chart:** Altair line chart (`st.altair_chart`) of Temp + Feels-like over the
  48h, styled like the consensus chart in `market_view` — ~2.5px stroke,
  x-axis `%-I %p`, y-axis `°F`, legend on top.
- **Table:** hand-rolled HTML (reusing the existing table CSS), one row per hour,
  Title-Case headers (Hour, Temp, Feels, Rain %, Cloud, Wind, Hum), rows grouped
  and labeled by day (Today / Tomorrow or weekday).

### Change: `app.py`

- Add a cached loader:
  ```python
  @st.cache_data(ttl=60, show_spinner=False)
  def load_hourly():
      from sources import wunderground
      return wunderground.hourly(), wunderground.pws_current()
  ```
  (TTL 60s matches the page autorefresh; the underlying source TTLs prevent
  refetch churn.)
- Add `def hourly_page(): hourly_view.render(load_hourly)`. `hourly_view.render`
  obtains the official KDFW current temp itself via
  `sources.nws_observations.fetch(continuous=True)` (kept behind a try/except so a
  KDFW obs failure only blanks that one tile).
- Insert `st.Page(hourly_page, title="Hourly")` as the 2nd nav entry.

## Data flow

1. Page load → `load_hourly()` (60s cache) → `wunderground.hourly()` +
   `wunderground.pws_current()` (each disk-cached in `sources.common`).
2. Official KDFW current temp comes from `sources.nws_observations.fetch(continuous=True)` (latest 5-min reading).
3. `hourly_view.render()` draws header tiles → chart → table.

## Error handling

- If `wunderground.hourly()` raises/returns empty, the page shows a warning
  banner (same spirit as the `dropped_sources` banner) and still renders the
  official KDFW current temp. Never crashes.
- If `pws_current()` fails, its tile shows an em dash; the rest of the page is
  unaffected.
- All source calls already fail-soft via `common.get_json`'s circuit breaker.

## Testing

- **`tests/test_wunderground.py`** — parse a captured TWC JSON fixture (hourly +
  PWS) offline (no live HTTP in CI); assert row count, key shape, type coercion,
  and empty/missing-field handling.
- **`tests/test_hourly_view.py`** — smoke test under the existing streamlit-stub
  pattern used by the other view tests; assert `render()` runs without error given
  a stub loader and degrades gracefully when the loader raises.

## Out of scope (YAGNI)

- 10-day/240h extended hourly.
- Extra WU columns beyond the six chosen (dew point, pressure, precip amount,
  conditions icon).
- Any betting/model logic on this page — it is a pure mirror/reference view.
