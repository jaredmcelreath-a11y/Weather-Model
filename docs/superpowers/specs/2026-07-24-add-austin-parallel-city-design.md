# Add Austin (KAUS) as a full parallel city

**Date:** 2026-07-24
**Status:** Design approved, pending spec review

## Goal

Add Austin, TX as a first-class second city alongside Dallas–Fort Worth (KDFW),
covering the daily high **and** low temperature Kalshi markets. Austin is a full
peer from day one: its own forecast/probabilities, settlement basis, calibration
and self-scoring, betting log, retrospective analytics, and its own autonomous
trader. Dallas behavior must remain byte-identical throughout — Austin is
additive, not a rewrite.

## Scope decisions (locked in brainstorm)

- **Full parallel city**, not phased. Every page becomes two-city aware.
- **Kalshi / CLI settlement basis ONLY for Austin.** Do NOT build the
  hourly-settlement-basis (Robinhood / ForecastEx) path for Austin: no
  `"hourly"` `record_basis`, no second `load_snapshot`/`load_accuracy` pair, no
  Robinhood page for Austin. Austin has exactly one basis (continuous CLI).
  Dallas keeps its already-retired-but-in-code Robinhood path unchanged; Austin
  simply never grows one.
- **Kalshi lists both Austin daily-high and daily-low** (user confirmed) — Austin
  gets the full two-variable model + trader.
- The **"Hourly" nav page** (the Wunderground.com/hourly mirror — a forecast
  reference, unrelated to the retired Robinhood basis) DOES get Austin.

## Architecture — backend station dimension

### Station registry (`config.py`)

Turn the flat KDFW constant module into a registry:

- `STATIONS: dict[str, StationConfig]` keyed by `"KDFW"` / `"KAUS"`. Each entry
  holds the genuinely station-specific values:
  - `id`, `lat`, `lon`, `timezone`, `climate_tz`
  - CLI product code (`CLIDFW` / `CLIAUS`)
  - market bin range (`BIN_LOW` / `BIN_HIGH`)
  - the convective upstream-county map (per-metro geography)
  - station-tunable knobs: `WARM_LOW_THRESHOLD`, low/high-lock params,
    convective POP thresholds
- Physics-ish constants that should not differ by city (e.g. `PEAK_LOCK_DROP`,
  `HIGH_LOCK_*`) stay as module-level shared defaults; a station overrides only
  what it needs.
- `DEFAULT_STATION = "KDFW"` and a `station(code)` accessor.
- Existing bare module constants (`STATION_ID`, `LAT`, …) are kept as aliases
  pointing at the `KDFW` entry so nothing referencing them breaks mid-refactor.

### Threading `station` through the pipeline

Everything that reads a config constant today takes a `station` argument
(threaded from the page): `model.py`, `settlement.py`, the `sources/*` fetchers,
`calibration.py`, `scoring.py`, `backtest.py`, alerts, recap. **This is the bulk
of the effort — larger than the UI.** Default the argument to `DEFAULT_STATION`
so partially-threaded call sites still resolve to Dallas during the refactor.

### Data namespacing — leave Dallas untouched, namespace Austin

- **Every existing Dallas file stays exactly where it is** (`forecast_log.jsonl`,
  `settlements.jsonl`, `consensus_history.jsonl`, `betting_log`, calibration,
  bet history). Zero migration risk to live persisted history and the GitHub
  data branch.
- Austin gets parallel files under a per-station path (e.g. `data/KAUS/…` or a
  `.KAUS` suffix — pick one in the plan).
- A single `data_path(name, station)` helper returns the **legacy bare path for
  KDFW** and the namespaced path for any other station. Same convention on the
  GitHub data branch used for cloud persistence.
- Austin starts clean; Dallas's history and calibration are never touched.

### Actions / crons

- The scheduled logging Action loops over stations (matrix or in-process loop),
  writing each city's namespaced files.
- A second CLI-report / ntfy alert stream for Austin (own topic or city-tagged
  messages).
- The autonomous trader gets a **second, fully independent per-station instance**
  on the trade branch: own kill switch, own mode (safe/shadow/live), own
  daily-loss-cap, own params and state. Austin ships DISABLED + shadow, same as
  Dallas did.

## Architecture — UI

### Shared city control

A single reusable `city_control(page_key)` component. **Sticky city**: the last
explicit `Dallas`/`Austin` pick is stored in `st.session_state` and becomes the
default when landing on a 2-way page. 3-way pages default to `Both` on first
visit but remember their own last selection within the page.

### Per-page behavior

| Page | City control | Default |
|------|-------------|---------|
| Forecast | `Dallas \| Austin` | sticky |
| Hourly | `Dallas \| Austin` | sticky |
| History | `Dallas \| Austin \| Both` | Both |
| Journal | `Dallas \| Austin \| Both` (city-tagged day cards, interleaved) | Both |
| Lab | `Dallas \| Austin \| Both` | Both |
| Edge | `Dallas \| Austin \| Both` | Both |
| Accuracy | `Dallas \| Austin \| Both` | Both |
| Status | **both at once**, no toggle (both pipelines' health always visible) | — |
| Trader | combined safety summary (both traders' mode / kill-switch / loss-cap) **+** a `Dallas \| Austin` toggle for editing one city's params | sticky |

- Page header reflects the active city ("Austin Daily High & Low"); `Both` shows
  a combined header.
- The browser-tab title (`st.set_page_config`) goes generic
  (e.g. "Texas Daily High & Low").
- Status **never** hides a city — a stale Austin feed must be visible without
  switching.
- Trader shows both traders' safety state at a glance up top; editing params is
  scoped to the toggled city so control stays deliberate and per-city.

## Prerequisites & risks

1. **Settlement-basis verification for Austin — BLOCKING first task.** Mirror the
   rigor applied to KDFW (CLIDFW, climate day = LST window). Confirm the CLI
   product (`CLIAUS`), its climate-day boundary, and **which physical station
   Kalshi settles Austin on** — Austin has both Camp Mabry (KATT) and
   Austin-Bergstrom (KAUS). Kalshi's choice dictates the lat/lon and the
   observation feed. Nothing downstream is trustworthy until this is nailed.
2. **Station-specific convective geography.** KDFW's upstream storm-approach
   counties are hand-mapped for its position between Dallas and Fort Worth.
   Austin needs its own county map, or the convective-downside guard runs
   degraded there until it is built.
3. **Calibration cold-start.** Austin begins with zero settled days and runs on
   the interim per-lead sigma inflation until it accrues history — the same
   bootstrap Dallas went through. Accuracy / Edge / Lab for Austin will read thin
   for a few weeks; expected, not a bug.

## Suggested phasing (within this single spec)

Dallas must behave identically at every step (it is just `station="KDFW"` with
legacy paths):

1. Config registry + `data_path()` helper (Dallas aliases keep old call sites
   working).
2. Thread `station` through model / settlement / sources / calibration / scoring.
3. Austin data files + Actions loop + settlement-basis verification landed.
4. UI city control + per-page wiring.
5. Second Austin trader + alerts (ships disabled + shadow).

## Out of scope

- Robinhood / hourly-settlement-basis path for Austin (explicitly excluded).
- Any migration of Dallas's existing data files.
- A third city (registry makes it cheap later, but not now).
