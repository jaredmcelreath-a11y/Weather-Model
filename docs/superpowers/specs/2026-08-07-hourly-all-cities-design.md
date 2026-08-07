# Hourly page — all Kalshi weather cities

Date: 2026-08-07

## Problem

The Hourly page mirrors Wunderground's hourly forecast for one station, chosen
with a two-option Dallas/Austin segmented control. Kalshi lists high/low
temperature contracts on **20 cities**, and the Screen page already surfaces
brackets in all of them — but there is no way to look at the hourly forecast for
Atlanta or Miami without leaving the app.

Make the Hourly page cover all 20, selected from a dropdown labelled with each
city's climate station.

## Scope

Reference/mirror only. No model, no bins, no Kalshi prices, no trading. The 18
new cities are display cities: hourly forecast, current temperature, official
climate report, radar.

## City set — verified live 2026-08-07

Kalshi's Climate-and-Weather series list was fetched on 2026-08-07 and still
covers exactly the 20 cities already mapped in `scan_cities.py`; no city has
been added since the 2026-08-03 screen work. That module stays the single
source of truth for coordinates.

Every row below was verified by resolving the coordinate through
`api.weather.gov/points/{lat},{lon}` (first returned station, `timeZone`) and by
fetching + parsing that city's CLI product with the existing
`nws_cli.parse_cli`:

| key  | city          | station | timezone            | climate_tz | CLI |
|------|---------------|---------|---------------------|------------|-----|
| DAL  | Dallas        | KDFW    | America/Chicago     | Etc/GMT+6  | DFW |
| AUS  | Austin        | KAUS    | America/Chicago     | Etc/GMT+6  | AUS |
| ATL  | Atlanta       | KATL    | America/New_York    | Etc/GMT+5  | ATL |
| BOS  | Boston        | KBOS    | America/New_York    | Etc/GMT+5  | BOS |
| CHI  | Chicago       | KORD    | America/Chicago     | Etc/GMT+6  | ORD |
| DC   | Washington DC | KDCA    | America/New_York    | Etc/GMT+5  | DCA |
| DEN  | Denver        | KDEN    | America/Denver      | Etc/GMT+7  | DEN |
| HOU  | Houston       | KIAH    | America/Chicago     | Etc/GMT+6  | IAH |
| LAX  | Los Angeles   | KLAX    | America/Los_Angeles | Etc/GMT+8  | LAX |
| LV   | Las Vegas     | KLAS    | America/Los_Angeles | Etc/GMT+8  | LAS |
| MIA  | Miami         | KMIA    | America/New_York    | Etc/GMT+5  | MIA |
| MIN  | Minneapolis   | KMSP    | America/Chicago     | Etc/GMT+6  | MSP |
| NOLA | New Orleans   | KMSY    | America/Chicago     | Etc/GMT+6  | MSY |
| NYC  | New York      | KNYC    | America/New_York    | Etc/GMT+5  | NYC |
| OKC  | Oklahoma City | KOKC    | America/Chicago     | Etc/GMT+6  | OKC |
| PHIL | Philadelphia  | KPHL    | America/New_York    | Etc/GMT+5  | PHL |
| PHX  | Phoenix       | KPHX    | America/Phoenix     | Etc/GMT+7  | PHX |
| SATX | San Antonio   | KSAT    | America/Chicago     | Etc/GMT+6  | SAT |
| SEA  | Seattle       | KSEA    | America/Los_Angeles | Etc/GMT+8  | SEA |
| SFO  | San Francisco | KSFO    | America/Los_Angeles | Etc/GMT+8  | SFO |

Non-obvious entries, all confirmed rather than guessed:

- **Chicago is KORD / CLI `ORD`**, not a "CHI" code — no such CLI location exists.
- **Houston is KIAH / CLI `IAH`.** A CLI location `HOU` (Hobby) also exists and
  is the wrong airport for the Kalshi series.
- **New York is KNYC, Central Park**, not an airport — carried over from
  `scan_cities`, where the same exception is already documented.
- **Phoenix is `America/Phoenix`** (no DST), so its display timezone and its
  fixed-LST climate zone coincide year-round.

`climate_tz` is the station's standard-time fixed offset, which is what an NWS
climate day runs on — the same rule `config.CLIMATE_TZ` encodes for KDFW.

## Design

### 1. `hourly_cities.py` (new)

A frozen dataclass and an ordered registry:

```python
@dataclass(frozen=True)
class HourlyCity:
    key: str            # "ATL" — the scan_cities city code
    name: str           # "Atlanta"
    station: str        # "KATL"
    lat: float
    lon: float
    timezone: str       # IANA, for every displayed time
    climate_tz: str     # fixed LST, for the CLI climate-day gate
    cli_location: str   # "ATL"
    modeled: str | None # config station code for Dallas/Austin, else None
```

Public surface: `CITIES` (ordered), `city(key)`, `keys()`, `label(key)` →
`"Atlanta (KATL)"`, and `default_key()` → `"DAL"`.

Ordering is Dallas, Austin, then the other 18 alphabetically by name.

**Coordinates are not duplicated.** The 18 reference cities read theirs from
`scan_cities`, which currently exposes `_CITY_COORDS`/`_CITY_NAMES` privately; a
public accessor is added there rather than copying the table. Dallas and Austin
build theirs from `config.station()` instead — the same pattern by which
`config` builds its KDFW entry from module constants — so their rendered page is
identical to today's, down to the geocode used for the TWC request and the radar
centre.

### 2. Source-layer changes (additive)

Three small extractions. Existing signatures and behaviour are preserved; the
model, logging and trading paths are untouched.

- `sources/wunderground.py`: `hourly_at(lat, lon, tz)` holds the body;
  `hourly(station=...)` delegates. **This carries the timezone fix.** Today
  `hourly()` stamps every row with the module-level `America/Chicago`, so a
  Miami or Los Angeles feed would render its hours in the wrong zone.
- `sources/nws_observations.py`: `latest(station_id)` → `{"temp", "time"}` or
  `None`, reading the newest usable reading from
  `/stations/{id}/observations?limit=10` with a UTC-aware timestamp. It takes a
  raw station id, so it serves cities `config` has never heard of. This is the
  same sub-hourly feed the page's tile already shows, from a much cheaper call.
- `sources/nws_cli.py`: `fetch_latest_for(location, ttl)` holds the body;
  `fetch_latest_cli(station=...)` delegates through `config`'s `cli_location`.
  The existing parser already handles both office time formats and was verified
  against all 20 products.

**Accepted regression:** the Hourly current-temp tile loses the IEM outage
fallback carried by `fetch(continuous=True)`, because that fallback resolves its
station through `config` and cannot serve KATL. During an NWS observation gap
the tile shows `—` rather than a stale reading. The Forecast page's Current
Temp — the number that drives betting — keeps the fallback and is unchanged.

### 3. `hourly_view.render(city, ...)`

Takes an `HourlyCity` instead of a station code. The module-level `TZ` constant
is removed; every displayed time derives from `ZoneInfo(city.timezone)`:

- hour rows from `wunderground.hourly_at`,
- `today` for the Today/Tomorrow section labels,
- the "as of" caption under the current-temp tile,
- the CLI report's issued time.

Title becomes `"{city.name} Hourly"`, caption names `city.station`. Radar
centres on `city.lat/lon` at the existing zoom. The Euless PWS tile stays
Dallas-only — the existing `pws is None` branch already renders a single
full-width official card for everyone else.

### 4. CLI climate-day gate

`app.load_cli_report` gates on `settlement.climate_day_of(now, station)`, which
is `config`-station-keyed. For reference cities the loader instead compares the
parsed `report_date` against `now.astimezone(ZoneInfo(city.climate_tz)).date()`
— the identical rule, without teaching `settlement` about unmodeled cities.

This gate is load-bearing, not decorative: probing at midday Central on
2026-08-07, the newest CLI product for LAX, LAS, PHX, SEA and SFO was still
**yesterday's**. Ungated, those cities would label yesterday's high as today's
every morning.

### 5. `app.py`

`hourly_page()` renders an `st.selectbox` over the 20 labels, keyed on its own
session-state entry (`hourly_city`) and defaulting to Dallas. It is deliberately
**independent** of the sticky Dallas/Austin `city` state that `city_view`
maintains: selecting Miami on Hourly must never propagate to Forecast, Journal,
Edge, History or Status, none of which have data for it.

Three cached loaders keyed by city key, with today's TTLs: the TWC hourly feed
(60 s), the current temperature, and the CLI report (300 s).

No `?city=` deep-link support for the new cities — the existing deep link stays
Dallas/Austin and is not extended.

## Testing

Unit tests (streamlit stubbed, per the project's usual pattern):

- registry: all 20 cities present, Dallas/Austin first then alphabetical,
  labels read `"Name (KXXX)"`, every `key` exists in `scan_cities`, Dallas and
  Austin agree with `config.station()` on name/id/lat/lon/timezone.
- `wunderground.hourly_at` parses a fixture into rows stamped in the passed
  timezone; `hourly(station=...)` still returns Central-stamped rows.
- `nws_observations.latest` picks the newest non-null reading and returns an
  aware timestamp; empty feed → `None`.
- `nws_cli.fetch_latest_for` hits the location endpoint; `fetch_latest_cli`
  still routes through `config`.
- `hourly_view.render` smoke test for a non-Central city, asserting the rendered
  hour labels match the city's zone — this is what pins the timezone fix.
- the CLI climate-day gate rejects a report whose `report_date` is behind the
  city's LST date.

Live check: `scripts/verify_hourly_cities.py` re-runs the verification performed
for this spec — for each city, resolve the coordinate and assert the first
station and `timeZone` match the table, then fetch and parse the CLI product.
Kept as a script (not a unit test) since it needs the network, mirroring the
2026-08-03 coordinate verification.

## Out of scope

- Any model, bin, probability or Kalshi price for the 18 reference cities.
- Extending `config.STATIONS`. Adding these cities there would grow
  `STATION_CODES`, which `city_view`'s "Both", the scheduled loggers and the
  trader all iterate — they would attempt to model 20 cities.
- A PWS tile for cities other than Dallas.
- Per-city convective/storm logic.
