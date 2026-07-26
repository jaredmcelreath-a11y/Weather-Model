# Austin — Plan 2: Austin Online (Data + Actions + Verification) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get Austin's full data pipeline running in the cloud — verified settlement basis, its convective county map, a station-aware Kalshi market layer, and the scheduled logging Action looping over both stations — so Austin's forecast/self-scoring/settlement/calibration/market data accumulates on the `data` branch the same way Dallas's does, with Dallas byte-identical.

**Architecture:** Builds directly on Plan 1's `config.station()` / `paths.data_path()` / `station=`-threaded pipeline (merged 42f6bc4). Plan 2 verifies and fills Austin's config values, threads `station` through the one remaining data-layer module (`kalshi.py`), and makes the batch entry points (`scheduled_log.py`, `log.yml`, `alerts`) iterate over `config.STATION_CODES` instead of implicitly serving KDFW.

**Tech Stack:** Python 3.9, GitHub Actions (`log.yml`), NWS CLI + Kalshi trade API, IEM archive, ntfy.

## Global Constraints

- **Dallas byte-identical.** Full existing suite must pass unchanged at every commit; `station="KDFW"` defaults preserve every KDFW URL, ticker, path, and alert title.
- **KDFW `_PATH` anchors preserved** (Plan 1 convention): per-station files route via `data_path` for non-KDFW only.
- **Python 3.9**; `from __future__ import annotations` already in use.
- **Best-effort batch steps stay best-effort.** Every per-station step in the cron is independently guarded so one station's failure never blocks the other (matches the existing try/except idiom).
- **Austin values are VERIFIED, not assumed.** Task 1 is blocking: no Austin market/settlement code ships against a guessed ticker or station. Working assumptions to confirm: settlement station **KAUS (Bergstrom)** vs **KATT (Camp Mabry)**; CLI product **CLIAUS** at NWS location **AUS**; Kalshi series **KXHIGHTAUS / KXLOWTAUS** (Dallas uses `KXHIGHTDAL/KXLOWTDAL`, i.e. city code `AUS` not the airport code).

---

## File Structure

- `docs/benchmarks/2026-07-26-austin-basis/FINDINGS.md` — **create.** The Task-1 verification record (mirrors the KDFW `docs/benchmarks/2026-07-14/climate-day/` rigor).
- `config.py` — **modify.** Fill/confirm the KAUS `StationConfig` (settlement station id + lat/lon, `cli_location`); add `kalshi_high_series`/`kalshi_low_series` fields to `StationConfig` (KDFW = the current DAL tickers); populate the KAUS convective-county map.
- `sources/kalshi.py` — **modify.** Replace the module-level `SERIES` dict with a per-station lookup; thread `station` through `implied_forecast`/`implied_block`/`ask_rows` (and any market-list helper).
- `scheduled_log.py` — **modify.** `main()` and its helpers iterate `config.STATION_CODES`, threading `station` through snapshot/logs/calibration/settlements/det_models/market/betting/alerts; per-station alert state files.
- `alerts.py` — **modify.** Station-tagged titles + per-station event-state path.
- `.github/workflows/log.yml` — **modify.** Restore + publish `data/KAUS/*` alongside the bare KDFW files.
- `tests/test_station_kalshi.py`, `tests/test_station_scheduled_log.py`, `tests/test_austin_config.py` — **create.**

---

### Task 1: Verify Austin settlement basis + fill config (BLOCKING)

Investigative task — no TDD loop; it produces a findings doc and a config change, gated by a parse smoke test. Everything downstream depends on it.

**Files:**
- Create: `docs/benchmarks/2026-07-26-austin-basis/FINDINGS.md`
- Modify: `config.py` (KAUS entry)
- Test: `tests/test_austin_config.py`

- [ ] **Step 1: Confirm the NWS CLI product for Austin parses**

Run (the Plan-1 `list_url`/`fetch_latest_cli` already accept a station):
```bash
python -c "from sources import nws_cli; \
print(nws_cli.list_url('KAUS')); \
c=nws_cli.fetch_latest_cli(ttl=0, station='KAUS'); print(c)"
```
Expected: the list URL is `.../CLI/locations/AUS`; `c` is a dict with `report_date`, `high_f`, `low_f`. Open the raw product text (from the `@graph[0]` product) and record **which physical station the CLIAUS report is issued for** (the header names Camp Mabry or Austin-Bergstrom) and its **climate-day boundary** (the "CLIMATE SUMMARY FOR …" / valid-time language — confirm midnight-to-midnight LST like CLIDFW).

- [ ] **Step 2: Confirm the Kalshi Austin series tickers + settlement source**

```bash
# Find the Austin high/low series. Try the AUS analogue of the Dallas tickers:
python -c "from sources import kalshi, kalshi_auth; import requests; \
print(kalshi._markets_for('KXHIGHTAUS'))"   # adapt to the real list helper name
```
If `KXHIGHTAUS`/`KXLOWTAUS` don't resolve, browse the Kalshi web app for the Austin daily-high/low market and read its `rules_primary`/settlement text via the trade API (`GET /markets/{ticker}`). Record: the exact **series tickers**, and the **settlement source** language (which confirms CLIAUS + the physical station — it must agree with Step 1).

- [ ] **Step 3: Write the findings doc**

Create `docs/benchmarks/2026-07-26-austin-basis/FINDINGS.md` recording: settlement station (KAUS vs KATT) + its lat/lon, CLI location code, climate-day boundary, Kalshi series tickers, and any disagreement between the airport code and the CLI product. This is the durable evidence trail.

- [ ] **Step 4: Write the config test to the VERIFIED values**

```python
# tests/test_austin_config.py
import config


def test_kaus_settlement_values_verified():
    aus = config.station("KAUS")
    # Fill these from FINDINGS.md once verified (example shape shown):
    assert aus.cli_location == "AUS"
    assert aus.id in ("KAUS", "KATT")          # whichever the CLI report uses
    assert aus.kalshi_high_series.startswith("KXHIGHT")
    assert aus.kalshi_low_series.startswith("KXLOWT")
    # lat/lon match the settlement station within a small tolerance
    assert 30.0 < aus.lat < 30.4 and -98.0 < aus.lon < -97.4
```

- [ ] **Step 5: Add the series fields to `StationConfig` and update both entries**

In `config.py`, add `kalshi_high_series: str` and `kalshi_low_series: str` to the `StationConfig` dataclass. KDFW: `kalshi_high_series="KXHIGHTDAL"`, `kalshi_low_series="KXLOWTDAL"`. KAUS: the verified tickers. Correct KAUS `id`/`lat`/`lon`/`cli_location` to the verified settlement station.

- [ ] **Step 6: Run the config test + full suite**

Run: `python -m pytest tests/test_austin_config.py -q && python -m pytest -q`
Expected: PASS; existing count unchanged (KDFW entry only gained two fields).

- [ ] **Step 7: Commit**

```bash
git add config.py tests/test_austin_config.py docs/benchmarks/2026-07-26-austin-basis/FINDINGS.md
git commit -m "verify: Austin settlement basis + Kalshi series; fill KAUS config"
```

---

### Task 2: Austin convective upstream-county map

**Files:**
- Modify: `config.py` (KAUS `convective_counties`)
- Test: `tests/test_austin_config.py` (extend)

**Interfaces:**
- Consumes: `convective._station_zones`/`_station_counties` (Plan 1) already read `config.station(station).convective_counties`; filling the map activates the guard for Austin with no code change.

- [ ] **Step 1: Write the failing test**

```python
def test_kaus_convective_map_populated():
    aus = config.station("KAUS")
    m = aus.convective_counties
    assert m, "Austin convective map must be non-empty"
    # Travis County (the airport) is the metro anchor.
    assert any(v[1] == "metro" for v in m.values())
    # Values are (county_name, approach_direction) like KDFW's.
    assert all(isinstance(v, tuple) and len(v) == 2 for v in m.values())
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_austin_config.py::test_kaus_convective_map_populated -q`
Expected: FAIL (`convective_counties` is `{}`).

- [ ] **Step 3: Populate the KAUS convective map**

Mirror KDFW's methodology (N/NW approach counties, SW/W approach counties, metro county), using real TXC UGC codes for the Austin metro. Verify each FIPS against NWS UGC before committing (`https://api.weather.gov/zones/county/TXC453` etc.). Concrete starting set (confirm names/directions/FIPS in Step 4):

```python
convective_counties={
    "TXC491": ("Williamson", "N"),
    "TXC053": ("Burnet", "NW"),
    "TXC031": ("Blanco", "W"),
    "TXC209": ("Hays", "SW"),
    "TXC055": ("Caldwell", "S"),
    "TXC021": ("Bastrop", "E"),
    "TXC453": ("Travis", "metro"),
},
```

- [ ] **Step 4: Verify each UGC resolves to the named county**

```bash
python -c "import requests; \
[print(c, requests.get(f'https://api.weather.gov/zones/county/{c}').json()['properties']['name']) \
 for c in ('TXC491','TXC053','TXC031','TXC209','TXC055','TXC021','TXC453')]"
```
Expected: each prints the matching county name. Fix any mismatch in the map.

- [ ] **Step 5: Run the test + full suite**

Run: `python -m pytest tests/test_austin_config.py -q && python -m pytest -q`
Expected: PASS; unchanged existing count.

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_austin_config.py
git commit -m "feat: Austin convective upstream-county map (activates the storm guard)"
```

---

### Task 3: Station-aware Kalshi market layer

**Files:**
- Modify: `sources/kalshi.py`
- Test: `tests/test_station_kalshi.py` (create)

**Interfaces:**
- Consumes: `config.station(station).kalshi_high_series/.kalshi_low_series` (Task 1).
- Produces (append `station: str = config.DEFAULT_STATION` as the LAST param):
  - a `series_for(variable, station=config.DEFAULT_STATION) -> str | None` helper replacing the module `SERIES` lookup;
  - `implied_forecast(variable, day, station=config.DEFAULT_STATION)`, `implied_block(today, tomorrow, station=config.DEFAULT_STATION)`, `ask_rows(variable, day, station=config.DEFAULT_STATION)`, and any internal market-list helper — all resolving the series via `series_for`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_kalshi.py
import config
from sources import kalshi


def test_series_for_by_station():
    assert kalshi.series_for("high") == config.station("KDFW").kalshi_high_series
    assert kalshi.series_for("low", "KAUS") == config.station("KAUS").kalshi_low_series
    assert kalshi.series_for("bogus") is None


def test_implied_block_routes_station(monkeypatch):
    seen = {}

    def fake_implied_forecast(variable, day, station=config.DEFAULT_STATION):
        seen[(variable, station)] = True
        return None

    monkeypatch.setattr(kalshi, "implied_forecast", fake_implied_forecast)
    from datetime import date
    kalshi.implied_block(date(2026, 7, 26), date(2026, 7, 27), station="KAUS")
    assert all(st == "KAUS" for (_v, st) in seen)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_kalshi.py -q`
Expected: FAIL (`series_for` undefined / `implied_block` rejects `station`).

- [ ] **Step 3: Implement**

Replace `SERIES = {...}` usage with:
```python
def series_for(variable: str, station: str = config.DEFAULT_STATION) -> str | None:
    s = config.station(station)
    return {"high": s.kalshi_high_series, "low": s.kalshi_low_series}.get(variable)
```
Add `import config`. Thread `station` through `implied_forecast`, `implied_block` (pass to each `implied_forecast` call), and `ask_rows` / the market-list helper, using `series_for(variable, station)`. Keep `SERIES` as a KDFW back-compat constant only if another module imports it (grep first).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_kalshi.py -q`
Expected: PASS.

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count (KDFW default preserves the DAL series). Fix any market-mock signatures that now forward `station` (widen to accept it, behavior-preserving).

- [ ] **Step 6: Commit**

```bash
git add sources/kalshi.py tests/test_station_kalshi.py
git commit -m "feat: station-aware Kalshi series/market helpers (KDFW default)"
```

---

### Task 4: `scheduled_log.py` loops over stations

**Files:**
- Modify: `scheduled_log.py`, `alerts.py`
- Test: `tests/test_station_scheduled_log.py` (create)

**Interfaces:**
- Consumes: everything above + Plan 1's `station=`-threaded pipeline.
- Produces: `main()` iterates `config.STATION_CODES`; the per-station body is a new `run_station(code)` that threads `station=code` through `calibration.get`, `model.snapshot`, `forecast_log.record`/`consensus_log.record` (they auto-resolve from the snapshot tag, but pass explicitly for clarity), `settlements.record`, `open_meteo_models.write_published` (per-station `det_models` path), the market attach + betting capture (guarded), and the alert helpers. Alert state files are per-station: `<name>_alert_state.<STATION>.json` for non-KDFW.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_scheduled_log.py
import config
import scheduled_log


def test_main_runs_every_station(monkeypatch):
    seen = []
    monkeypatch.setattr(scheduled_log, "run_station", lambda code: seen.append(code))
    scheduled_log.main()
    assert seen == config.STATION_CODES  # KDFW then KAUS, both attempted


def test_one_station_failure_does_not_block_the_other(monkeypatch):
    seen = []

    def flaky(code):
        seen.append(code)
        if code == "KDFW":
            raise RuntimeError("KDFW pipeline down")

    monkeypatch.setattr(scheduled_log, "run_station", flaky)
    scheduled_log.main()  # must not raise
    assert "KAUS" in seen
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_scheduled_log.py -q`
Expected: FAIL (`run_station` undefined / `main` doesn't loop).

- [ ] **Step 3: Refactor `main()` into a per-station loop**

Extract the existing model-logging body into `run_station(code: str)` that threads `station=code` through every call (per the Interfaces block). Market attach + betting capture stay guarded and only run where `kalshi.series_for("high", code)` is set (both stations after Task 1). Give per-station state-file paths via a helper:
```python
def _state_path(name: str, station: str) -> str:
    base = os.path.join(os.path.dirname(__file__), name)
    return base if station == config.DEFAULT_STATION else base.replace(".json", f".{station}.json")
```
Rewrite `main()` to:
```python
def main() -> None:
    for code in config.STATION_CODES:
        try:
            run_station(code)
        except Exception as e:
            print(f"[{code}] scheduled run failed: {e}")
```
Move `_maybe_alert_cli`/`_maybe_alert_resolved` and `_publish_det_models` inside/under `run_station` with `station=code`; `write_published(path, station=code)` writes to a per-station `det_models` path (KDFW keeps `det_models.json`, KAUS `data/KAUS/det_models.json`).

- [ ] **Step 4: Station-tag the alerts**

In `alerts.py`, thread `station` into `maybe_fire_events` (title prefix from `config.station(station)` — "Dallas"/"Austin"; derive a display name, e.g. add a `name` field to `StationConfig` or map code→name in `alerts`), and use a per-station `EVENT_STATE_PATH`. In `scheduled_log`, the CLI-report / resolved alert titles become `f"{name} Climate Report"` / `f"{name} {var} Locked In"`.

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest tests/test_station_scheduled_log.py -q && python -m pytest -q`
Expected: PASS. Widen any `scheduled_log`/`alerts` test mocks that now receive `station` (behavior-preserving).

- [ ] **Step 6: Commit**

```bash
git add scheduled_log.py alerts.py tests/test_station_scheduled_log.py
git commit -m "feat: scheduled_log + alerts loop over stations (per-station state/titles)"
```

---

### Task 5: `log.yml` persists per-station data-branch files

**Files:**
- Modify: `.github/workflows/log.yml`

**Interfaces:**
- Consumes: Task 4's per-station files (`data/KAUS/forecast_log.jsonl`, `consensus_history.jsonl`, `settlements.jsonl`, `betting_log.jsonl`, `calibration.json`, `det_models.json`, and the `.KAUS` alert-state files).

- [ ] **Step 1: Restore the KAUS files before the run**

In the "Restore existing logs" step, after the KDFW `git show origin/data:*` lines, add (guarded so a first run with no Austin files still succeeds):
```bash
mkdir -p data/KAUS
for f in forecast_log.jsonl consensus_history.jsonl settlements.jsonl betting_log.jsonl calibration.json det_models.json; do
  git show origin/data:data/KAUS/$f > data/KAUS/$f 2>/dev/null || true
done
```

- [ ] **Step 2: Publish the KAUS files after the run**

In the "Publish the logs" step, before `git init`, copy the KAUS dir into the temp publish tree, and `git add -f data/KAUS/*` after checkout:
```bash
[ -d data/KAUS ] && mkdir -p "$tmp/data/KAUS" && cp data/KAUS/* "$tmp/data/KAUS/" 2>/dev/null || true
# ...after git checkout -b data...
[ -d data/KAUS ] && git add -f data/KAUS/ || true
```

- [ ] **Step 3: Validate the workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/log.yml'))" && echo "YAML OK"`
Expected: `YAML OK`. (The real cloud run is validated after merge by watching the first Action produce `data/KAUS/*` on the `data` branch.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/log.yml
git commit -m "ci: restore+publish per-station data/KAUS files on the data branch"
```

---

### Task 6: End-to-end KAUS cron smoke + regression gate

**Files:**
- Test: `tests/test_station_scheduled_log.py` (extend)

- [ ] **Step 1: Write a stubbed KAUS run_station smoke**

```python
def test_run_station_kaus_threads_everything(monkeypatch):
    import scheduled_log, model
    calls = {"snap": [], "settle": [], "cal": []}
    monkeypatch.setattr(model, "snapshot",
                        lambda *a, **k: {"station": k.get("station"),
                                         "today": {"day": "2026-07-26"},
                                         "tomorrow": {"day": "2026-07-27"}})
    monkeypatch.setattr(scheduled_log.calibration, "get",
                        lambda refresh=True, station=None: calls["cal"].append(station) or {})
    monkeypatch.setattr(scheduled_log.settlements, "record",
                        lambda today=None, path=None, station=None: calls["settle"].append(station))
    monkeypatch.setattr(scheduled_log.forecast_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log.consensus_log, "record", lambda *a, **k: None)
    scheduled_log.run_station("KAUS")
    assert calls["cal"] == ["KAUS"] and calls["settle"] == ["KAUS"]
```

- [ ] **Step 2: Run it + final full-suite gate**

Run: `python -m pytest tests/test_station_scheduled_log.py::test_run_station_kaus_threads_everything -q && python -m pytest -q`
Expected: PASS. Record the final count = pre-Plan-2 baseline + the new station tests.

- [ ] **Step 3: Commit**

```bash
git add tests/test_station_scheduled_log.py
git commit -m "test: KAUS cron run_station smoke + Plan 2 regression gate"
```

---

## Self-Review

**Spec coverage (spec §Prereqs & §Architecture-Actions):** Settlement-basis verification (§Prereq 1) → Task 1. Austin convective geography (§Prereq 2) → Task 2. "Actions loop over stations / 2nd ntfy stream" (§Architecture-Actions) → Tasks 4–5. The Kalshi market layer (implicit in "Austin online" — needed for the market block / Edge / trading) → Task 3. Cold-start calibration (§Prereq 3) needs no code — `calibration.get(station="KAUS")` already computes-or-empties per Plan 1. **Deferred to later plans:** UI city control (Plan 3), Austin autonomous trader (Plan 4) — both consume Task 3's `kalshi.series_for` and the now-flowing `data/KAUS/*`.

**Placeholder scan:** Task 1 is deliberately investigative; its config values are filled from `FINDINGS.md` at execution, and the example test asserts *shapes/ranges*, not guessed literals — the real literals are written once verified. The convective FIPS in Task 2 are concrete but Step 4 verifies each against the NWS API before commit. No `TBD`/`TODO` steps.

**Type consistency:** `station: str = config.DEFAULT_STATION` is the uniform new param across Tasks 3–4. `kalshi.series_for(variable, station)` is defined in Task 3 and consumed in Task 4. `StationConfig.kalshi_high_series/kalshi_low_series` are added in Task 1 and read in Task 3. `run_station(code)` is defined in Task 4 and asserted in Tasks 4 and 6.

**Risks called out:** Task 1 is a hard external dependency — if Kalshi lists no Austin low market (only high), Task 3's low path stays dormant and Task 4 guards it; note that in `FINDINGS.md` and proceed high-only rather than blocking. The cloud persistence (Task 5) is only truly validated by watching the first post-merge Action write `data/KAUS/*`.

## Follow-on

- **Plan 3 — UI city control** (consumes `data/KAUS/*` + `kalshi.series_for`). Carries the standing UI constraint: everything new must be Title-Cased, mobile-friendly, tooltip'd, and consistent with the dark serif dashboard.
- **Plan 4 — Austin autonomous trader + alerts** (second per-station trader on the trade branch).
