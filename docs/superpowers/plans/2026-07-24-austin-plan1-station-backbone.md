# Austin — Plan 1: Station-Parameterized Backbone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-station KDFW pipeline into a station-parameterized one — a station registry, a per-station data-path helper, and a `station` argument threaded through sources, the model, settlement, logs, calibration, and scoring — with Dallas behaving byte-identically.

**Architecture:** `config.py` becomes a registry (`STATIONS` dict of `StationConfig`, keyed `"KDFW"`/`"KAUS"`) while keeping its old bare constants as aliases to the KDFW entry so nothing breaks mid-refactor. A new `paths.data_path(name, station)` returns the legacy bare path for KDFW and a namespaced path otherwise. Every function that currently reads a station-specific config constant gains a `station: str = DEFAULT_STATION` parameter; with the default, existing call sites and the whole test suite are unchanged.

**Tech Stack:** Python 3.9, Streamlit, pytest, Open-Meteo + NWS + IEM HTTP sources.

## Global Constraints

- **Dallas byte-identical.** At every commit the full existing test suite must pass unchanged. `station="KDFW"` (the default everywhere) must produce identical behavior, URLs, and file paths to today's code. This is the acceptance bar for every task.
- **Legacy paths preserved.** KDFW data files stay at their current bare locations (`forecast_log.jsonl`, `settlements.jsonl`, `consensus_history.jsonl`, `calibration.json`, betting log). No migration. `data_path(name, "KDFW")` returns the bare path.
- **Kalshi/CLI basis only for Austin** — but Plan 1 changes no basis logic; it only threads the parameter. The hourly/Robinhood path is untouched.
- **Python 3.9** — no `match` statements, no `X | Y` runtime unions in isinstance; type-hint unions in annotations are fine (the codebase already uses `from __future__ import annotations`).
- **KAUS working values (unverified — confirmed in Plan 2):** lat `30.1975`, lon `-97.6664`, NWS station id `KAUS`, CLI product location `AUS`, `timezone="America/Chicago"`, `climate_tz="Etc/GMT+6"`, bin range `-10..115`. Austin's convective upstream-county map ships **empty** in Plan 1 (the convective guard runs degraded for Austin until its own map is built in a later plan — this is intended, not a placeholder).

---

## File Structure

- `config.py` — **modify.** Add `StationConfig` dataclass, `STATIONS` dict, `station()` accessor, `DEFAULT_STATION`, `STATION_CODES`. Keep existing bare constants as aliases to `STATIONS["KDFW"]`. `bin_labels()`/`lead_bucket()` gain an optional station arg.
- `paths.py` — **create.** `data_path(name, station)` + `github_path(name, station)` helpers.
- `sources/nws_observations.py`, `sources/nws_cli.py`, `sources/station_history.py` — **modify.** Station-parameterized identity/URLs (Task 3).
- `sources/open_meteo_models.py`, `sources/open_meteo_ensemble.py`, `sources/nws_forecast.py`, `sources/iem_mos.py`, `sources/wunderground.py` — **modify.** Station-parameterized lat/lon (Task 4).
- `model.py` — **modify.** `gather_series`/`predict`/`snapshot`/`predict_variable` accept and thread `station`; convective map read from station config (Task 5).
- `settlement.py`, `settlements.py` — **modify.** Climate tz / bins / paths per station (Task 6).
- `forecast_log.py`, `consensus_log.py`, `betting_log.py`, `calibration.py`, `scoring.py` — **modify.** Route paths via `data_path`, thread `station` (Task 7).
- `tests/test_station_registry.py`, `tests/test_data_path.py`, `tests/test_station_threading.py` — **create.**

---

### Task 1: Station registry in config.py

**Files:**
- Modify: `config.py`
- Test: `tests/test_station_registry.py`

**Interfaces:**
- Produces:
  - `config.StationConfig` — a `@dataclass(frozen=True)` with fields: `code: str`, `id: str`, `lat: float`, `lon: float`, `timezone: str`, `climate_tz: str`, `cli_location: str`, `bin_low: int`, `bin_high: int`, `nws_user_agent: str`, `convective_counties: dict[str, tuple[str, str]]`, `warm_low_threshold: float`.
  - `config.STATIONS: dict[str, StationConfig]` (keys `"KDFW"`, `"KAUS"`).
  - `config.DEFAULT_STATION: str = "KDFW"`, `config.STATION_CODES: list[str]`.
  - `config.station(code: str | None = None) -> StationConfig` — `None`/empty → the `DEFAULT_STATION` entry; unknown code raises `KeyError`.
  - Existing bare names (`STATION_ID`, `LAT`, `LON`, `TIMEZONE`, `CLIMATE_TZ`, `NWS_USER_AGENT`, `BIN_LOW`, `BIN_HIGH`, `CONVECTIVE_UPSTREAM_COUNTIES`, `CONVECTIVE_UPSTREAM_UGC`, `WARM_LOW_THRESHOLD`) remain, now assigned from `STATIONS["KDFW"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_registry.py
import config


def test_kdfw_entry_matches_legacy_constants():
    dfw = config.station("KDFW")
    assert dfw.id == "KDFW"
    assert dfw.lat == 32.90 and dfw.lon == -97.04
    assert dfw.timezone == "America/Chicago"
    assert dfw.climate_tz == "Etc/GMT+6"
    assert dfw.cli_location == "DFW"
    assert dfw.bin_low == -10 and dfw.bin_high == 115
    # Convective map is the real KDFW geography (non-empty).
    assert dfw.convective_counties["TXC113"] == ("Dallas", "metro")


def test_bare_aliases_still_point_at_kdfw():
    assert config.STATION_ID == config.station("KDFW").id
    assert config.LAT == config.station("KDFW").lat
    assert config.CONVECTIVE_UPSTREAM_UGC == tuple(config.station("KDFW").convective_counties)


def test_default_and_lookup():
    assert config.DEFAULT_STATION == "KDFW"
    assert config.station() is config.station("KDFW")
    assert config.station("").code == "KDFW"
    assert "KAUS" in config.STATION_CODES


def test_kaus_entry_present_with_empty_convective_map():
    aus = config.station("KAUS")
    assert aus.id == "KAUS"
    assert aus.cli_location == "AUS"
    assert aus.lat == 30.1975 and aus.lon == -97.6664
    assert aus.convective_counties == {}  # degraded guard until Austin's map is built


def test_unknown_station_raises():
    import pytest
    with pytest.raises(KeyError):
        config.station("KXXX")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_registry.py -q`
Expected: FAIL (`AttributeError: module 'config' has no attribute 'StationConfig'` / `station`).

- [ ] **Step 3: Implement the registry**

In `config.py`, add the dataclass and registry near the top (after imports), and convert the existing station block to be sourced from it. Concretely:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StationConfig:
    code: str
    id: str
    lat: float
    lon: float
    timezone: str
    climate_tz: str
    cli_location: str
    bin_low: int
    bin_high: int
    nws_user_agent: str
    convective_counties: dict
    warm_low_threshold: float


_KDFW_CONVECTIVE = {
    "TXC497": ("Wise", "NW"), "TXC237": ("Jack", "NW"), "TXC367": ("Parker", "W"),
    "TXC363": ("Palo Pinto", "W"), "TXC503": ("Young", "NW"), "TXC121": ("Denton", "N"),
    "TXC097": ("Cooke", "N"), "TXC337": ("Montague", "NW"), "TXC251": ("Johnson", "SW"),
    "TXC221": ("Hood", "SW"), "TXC425": ("Somervell", "SW"), "TXC143": ("Erath", "SW"),
    "TXC139": ("Ellis", "S"), "TXC439": ("Tarrant", "metro"), "TXC113": ("Dallas", "metro"),
}

STATIONS: dict[str, StationConfig] = {
    "KDFW": StationConfig(
        code="KDFW", id="KDFW", lat=32.90, lon=-97.04,
        timezone="America/Chicago", climate_tz="Etc/GMT+6", cli_location="DFW",
        bin_low=-10, bin_high=115,
        nws_user_agent="kdfw-weather-model (jaredmcelreath@gmail.com)",
        convective_counties=_KDFW_CONVECTIVE, warm_low_threshold=76,
    ),
    "KAUS": StationConfig(
        code="KAUS", id="KAUS", lat=30.1975, lon=-97.6664,
        timezone="America/Chicago", climate_tz="Etc/GMT+6", cli_location="AUS",
        bin_low=-10, bin_high=115,
        nws_user_agent="kaus-weather-model (jaredmcelreath@gmail.com)",
        convective_counties={},  # Austin storm-approach map TBD in a later plan
        warm_low_threshold=76,
    ),
}
DEFAULT_STATION = "KDFW"
STATION_CODES = list(STATIONS)


def station(code: str | None = None) -> StationConfig:
    return STATIONS[code or DEFAULT_STATION]
```

Then replace the existing bare constant assignments so they derive from the KDFW entry (delete the old literal lines they replace):

```python
_DFW = STATIONS["KDFW"]
STATION_ID = _DFW.id
LAT = _DFW.lat
LON = _DFW.lon
TIMEZONE = _DFW.timezone
CLIMATE_TZ = _DFW.climate_tz
NWS_USER_AGENT = _DFW.nws_user_agent
BIN_LOW = _DFW.bin_low
BIN_HIGH = _DFW.bin_high
WARM_LOW_THRESHOLD = _DFW.warm_low_threshold
CONVECTIVE_UPSTREAM_COUNTIES = _DFW.convective_counties
CONVECTIVE_UPSTREAM_UGC = tuple(CONVECTIVE_UPSTREAM_COUNTIES)
```

Leave `bin_labels()`, `lead_bucket()`, `LEAD_*`, and all the lock/bias tuning constants exactly as they are (shared defaults).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_registry.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite (Dallas-identical gate)**

Run: `python -m pytest -q`
Expected: PASS — same count as before this task (the alias assignments preserve every existing import).

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_station_registry.py
git commit -m "feat: station registry in config (KDFW aliases unchanged, KAUS added)"
```

---

### Task 2: Per-station data-path helper

**Files:**
- Create: `paths.py`
- Test: `tests/test_data_path.py`

**Interfaces:**
- Produces:
  - `paths.data_path(name: str, station: str = config.DEFAULT_STATION) -> str` — absolute path. For `"KDFW"` returns the legacy bare path `<repo>/<name>`. For any other station returns `<repo>/data/<STATION>/<name>`.
  - `paths.github_path(name: str, station: str = config.DEFAULT_STATION) -> str` — the path used on the GitHub data branch: `name` for KDFW, `data/<STATION>/<name>` otherwise (forward slashes).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_path.py
import os
import config
import paths


def test_kdfw_uses_legacy_bare_path():
    p = paths.data_path("forecast_log.jsonl", "KDFW")
    assert os.path.basename(p) == "forecast_log.jsonl"
    assert os.path.dirname(p) == os.path.dirname(os.path.abspath(config.__file__))
    # default arg is KDFW
    assert paths.data_path("settlements.jsonl") == paths.data_path("settlements.jsonl", "KDFW")


def test_other_station_is_namespaced():
    p = paths.data_path("forecast_log.jsonl", "KAUS")
    assert p.endswith(os.path.join("data", "KAUS", "forecast_log.jsonl"))


def test_github_path_shape():
    assert paths.github_path("settlements.jsonl", "KDFW") == "settlements.jsonl"
    assert paths.github_path("settlements.jsonl", "KAUS") == "data/KAUS/settlements.jsonl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_data_path.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'paths'`).

- [ ] **Step 3: Implement `paths.py`**

```python
"""Per-station data-path routing. KDFW keeps its legacy bare paths (zero
migration); every other station is namespaced under data/<STATION>/."""
from __future__ import annotations

import os

import config

_ROOT = os.path.dirname(os.path.abspath(config.__file__))


def data_path(name: str, station: str = config.DEFAULT_STATION) -> str:
    if station == config.DEFAULT_STATION:
        return os.path.join(_ROOT, name)
    return os.path.join(_ROOT, "data", station, name)


def github_path(name: str, station: str = config.DEFAULT_STATION) -> str:
    if station == config.DEFAULT_STATION:
        return name
    return f"data/{station}/{name}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_data_path.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add paths.py tests/test_data_path.py
git commit -m "feat: per-station data_path helper (KDFW bare, others namespaced)"
```

---

### Task 3: Parameterize observation & CLI sources on station

**Files:**
- Modify: `sources/nws_observations.py`, `sources/nws_cli.py`, `sources/station_history.py`
- Test: `tests/test_station_threading.py` (create)

**Interfaces:**
- Consumes: `config.station()`.
- Produces (each keeps its existing signature and appends `station: str = config.DEFAULT_STATION` as the LAST param):
  - `nws_observations.fetch(continuous=False, now=None, start=None, station=config.DEFAULT_STATION)`
  - `nws_cli.fetch_latest_cli(ttl=None, station=config.DEFAULT_STATION)`
  - `station_history.fetch_actual_cli(day, ..., station=config.DEFAULT_STATION)` and any sibling fetchers in that module (append the same param).
  The station's `id` builds the observations URL; the station's `cli_location` builds the CLI list URL. Module-level `OBS_URL`/`LIST_URL` constants are replaced by per-call URL builders (keep a KDFW-valued module constant only if another module imports it — grep first).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_threading.py
import config
from sources import nws_observations, nws_cli


def test_obs_url_defaults_to_kdfw():
    assert nws_observations.obs_url() == \
        "https://api.weather.gov/stations/KDFW/observations"


def test_obs_url_for_austin():
    assert nws_observations.obs_url("KAUS") == \
        "https://api.weather.gov/stations/KAUS/observations"


def test_cli_list_url_by_station():
    assert nws_cli.list_url() == \
        "https://api.weather.gov/products/types/CLI/locations/DFW"
    assert nws_cli.list_url("KAUS") == \
        "https://api.weather.gov/products/types/CLI/locations/AUS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_threading.py -q`
Expected: FAIL (`AttributeError: ... has no attribute 'obs_url'`).

- [ ] **Step 3: Implement URL builders + thread the param**

In `sources/nws_observations.py`, replace the module constant with a builder and use it inside `fetch`:

```python
def obs_url(station: str = config.DEFAULT_STATION) -> str:
    return f"https://api.weather.gov/stations/{config.station(station).id}/observations"
```

Add `station: str = config.DEFAULT_STATION` to `fetch(...)` and use `obs_url(station)` where `OBS_URL` was used; use `config.station(station).nws_user_agent` where the bare `NWS_USER_AGENT` was used. In `sources/nws_cli.py`, add:

```python
def list_url(station: str = config.DEFAULT_STATION) -> str:
    return f"https://api.weather.gov/products/types/CLI/locations/{config.station(station).cli_location}"
```

Thread `station` through `fetch_latest_cli` (and its `_DATE_RE`/parse helpers stay station-agnostic). In `sources/station_history.py`, append `station=config.DEFAULT_STATION` to each fetcher and route its station id / CLI location through `config.station(station)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_threading.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count (defaults preserve KDFW URLs).

- [ ] **Step 6: Commit**

```bash
git add sources/nws_observations.py sources/nws_cli.py sources/station_history.py tests/test_station_threading.py
git commit -m "feat: parameterize obs/CLI/history sources on station (KDFW default)"
```

---

### Task 4: Parameterize forecast sources on station lat/lon

**Files:**
- Modify: `sources/open_meteo_models.py`, `sources/open_meteo_ensemble.py`, `sources/nws_forecast.py`, `sources/iem_mos.py`, `sources/wunderground.py`
- Test: `tests/test_station_threading.py` (extend)

**Interfaces:**
- Produces (append `station: str = config.DEFAULT_STATION` as the LAST param to each public fetch, replacing inlined `LAT`/`LON` with `config.station(station).lat/.lon`):
  - `open_meteo_models.fetch(forecast_days=..., models=None, station=config.DEFAULT_STATION)`
  - `open_meteo_ensemble.fetch(forecast_days=..., models=None, station=config.DEFAULT_STATION)`
  - `nws_forecast.fetch(station=config.DEFAULT_STATION)`
  - `iem_mos.fetch(forecast_days=..., station=config.DEFAULT_STATION)`
  - `wunderground.hourly(station=config.DEFAULT_STATION)`, `wunderground.pws_current(station=config.DEFAULT_STATION)` (Wunderground's PWS/geocode identity is station-derived; for KAUS it uses the station lat/lon — the PWS-vs-airport nuance is a display concern deferred to the UI plan).

- [ ] **Step 1: Write the failing test (extend tests/test_station_threading.py)**

```python
def test_open_meteo_params_use_station_latlon(monkeypatch):
    from sources import open_meteo_models
    captured = {}

    def fake_get_json(url, params=None, **kw):
        captured["params"] = params
        return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr(open_meteo_models, "get_json", fake_get_json, raising=False)
    open_meteo_models.fetch(2, station="KAUS")
    assert captured["params"]["latitude"] == 30.1975
    assert captured["params"]["longitude"] == -97.6664
```

Note: match the real HTTP shim name used in `open_meteo_models.py` (grep for the request helper — likely `get_json` from `sources.common`); adjust `monkeypatch.setattr` target accordingly.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_threading.py::test_open_meteo_params_use_station_latlon -q`
Expected: FAIL (`fetch() got an unexpected keyword argument 'station'`).

- [ ] **Step 3: Implement**

In each forecast source, add the `station` param and replace every `"latitude": LAT, "longitude": LON` (5 occurrences in `open_meteo_models.py`, plus the equivalents in the others) with `"latitude": config.station(station).lat, "longitude": config.station(station).lon`. For `nws_forecast.py`, the gridpoint/lat-lon lookup uses `config.station(station).lat/.lon`. For `iem_mos.py`, its station identifier uses `config.station(station).id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_threading.py -q`
Expected: PASS.

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count.

- [ ] **Step 6: Commit**

```bash
git add sources/open_meteo_models.py sources/open_meteo_ensemble.py sources/nws_forecast.py sources/iem_mos.py sources/wunderground.py tests/test_station_threading.py
git commit -m "feat: parameterize forecast sources on station lat/lon (KDFW default)"
```

---

### Task 5: Thread station through the model pipeline

**Files:**
- Modify: `model.py`
- Test: `tests/test_station_threading.py` (extend)

**Interfaces:**
- Consumes: the station-aware source fetches from Tasks 3–4; `config.station()`.
- Produces (append `station: str = config.DEFAULT_STATION` as the LAST param, threaded downward):
  - `gather_series(forecast_days=2, continuous_obs=False, now=None, det_models=None, ens_models=None, station=config.DEFAULT_STATION)` — passes `station` to every source fetch and to `_fetch_cli_daily`.
  - `predict(day, now=None, calib=None, forecast_days=2, settle_offset=None, station=config.DEFAULT_STATION)`
  - `snapshot(calib=None, settle_offset=None, continuous_obs=False, include_candidate=False, station=config.DEFAULT_STATION)` — records `station` into the returned dict under key `"station"`.
  - `predict_variable(...)` and `_predict_from(...)` take `station` and use `config.station(station).convective_counties` / `.warm_low_threshold` where the module currently reads the bare `config.CONVECTIVE_*` / `WARM_LOW_THRESHOLD`.
  - Internal `_storm_status`, convective helpers, and `_fetch_cli_daily` take `station`.

- [ ] **Step 1: Write the failing test**

```python
def test_snapshot_tags_station(monkeypatch):
    import model

    def fake_gather(*a, **k):
        assert k.get("station") == "KAUS"  # station reaches the source layer
        # minimal empty series/obs so predict paths no-op gracefully
        return {}, {"obs": ([], []), "obs_continuous_display": (None, None)}, []

    monkeypatch.setattr(model, "gather_series", fake_gather)
    snap = model.snapshot(station="KAUS")
    assert snap["station"] == "KAUS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_threading.py::test_snapshot_tags_station -q`
Expected: FAIL (`snapshot() got an unexpected keyword argument 'station'`).

- [ ] **Step 3: Implement threading**

Add `station=config.DEFAULT_STATION` to `gather_series`, `predict`, `_predict_from`, `predict_variable`, `snapshot`, `_storm_status`, `_fetch_cli_daily`, and the convective helper(s). Pass it into each `fetch(...)` call inside `gather_series` (`open_meteo_ensemble.fetch(..., station=station)`, etc.). In `snapshot`, add `"station": station` to the returned dict. Where the model reads `config.CONVECTIVE_UPSTREAM_COUNTIES` / `config.CONVECTIVE_UPSTREAM_UGC` / `config.WARM_LOW_THRESHOLD`, replace with `config.station(station).convective_counties` / `tuple(config.station(station).convective_counties)` / `config.station(station).warm_low_threshold`. (Grep `model.py` and `convective.py` for those names; `convective.py` helpers gain the same param.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_threading.py -q`
Expected: PASS.

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count. (Existing snapshot tests may now see an extra `"station"` key; if any assert exact-dict-equality on the snapshot, update that test to expect `"station": "KDFW"` — this is a legitimate additive change, not a Dallas behavior change.)

- [ ] **Step 6: Commit**

```bash
git add model.py convective.py tests/test_station_threading.py
git commit -m "feat: thread station through gather_series/predict/snapshot + convective"
```

---

### Task 6: Thread station through settlement

**Files:**
- Modify: `settlement.py`, `settlements.py`
- Test: `tests/test_station_threading.py` (extend)

**Interfaces:**
- Produces:
  - `settlement.local_day_bounds(day, station=config.DEFAULT_STATION)`, `settlement.climate_day_of(moment, station=config.DEFAULT_STATION)`, `settlement.open_prior_day(moment, station=config.DEFAULT_STATION)` — build their `ZoneInfo` from `config.station(station).climate_tz` instead of the module-level `_CLIMATE_TZ`.
  - `settlements.load(path=None, station=config.DEFAULT_STATION)`, `settlements.record(today=None, path=None, station=config.DEFAULT_STATION)`, `settlements.as_map(basis, station=config.DEFAULT_STATION)` — default `path` resolves via `paths.data_path("settlements.jsonl", station)`; GitHub cfg path via `paths.github_path`.

- [ ] **Step 1: Write the failing test**

```python
def test_settlement_bounds_station_tz():
    import settlement
    from datetime import date
    # KDFW default unchanged
    s, e = settlement.local_day_bounds(date(2026, 7, 1))
    assert s.utcoffset().total_seconds() == -6 * 3600
    # KAUS is also Etc/GMT+6 for now — same offset, but the call must accept station
    s2, _ = settlement.local_day_bounds(date(2026, 7, 1), station="KAUS")
    assert s2.utcoffset().total_seconds() == -6 * 3600


def test_settlements_path_by_station():
    import settlements
    import paths
    # load() with no rows on disk for KAUS returns [] (namespaced path, absent file)
    assert settlements.load(station="KAUS") == []
    # KDFW still reads the legacy bare file
    assert isinstance(settlements.load(station="KDFW"), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_threading.py -k settlement -q`
Expected: FAIL (`local_day_bounds() got an unexpected keyword argument 'station'`).

- [ ] **Step 3: Implement**

In `settlement.py`, replace uses of module-level `_CLIMATE_TZ` inside the three functions with `ZoneInfo(config.station(station).climate_tz)` (keep `_CLIMATE_TZ` as the KDFW default for any remaining bare references). Where `BIN_LOW`/`BIN_HIGH` are used, read from `config.station(station)`. In `settlements.py`, change the default-path logic from `path or _PATH` to `path or paths.data_path("settlements.jsonl", station)`, thread `station` through `load`/`record`/`as_map`/`_fetch`, and route the GitHub cfg `path` key through `paths.github_path("settlements.jsonl", station)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_threading.py -k settlement -q`
Expected: PASS.

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count.

- [ ] **Step 6: Commit**

```bash
git add settlement.py settlements.py tests/test_station_threading.py
git commit -m "feat: thread station through settlement + settlements paths"
```

---

### Task 7: Thread station through logs, calibration, and scoring

**Files:**
- Modify: `forecast_log.py`, `consensus_log.py`, `betting_log.py`, `calibration.py`, `scoring.py`
- Test: `tests/test_station_threading.py` (extend)

**Interfaces:**
- Produces (each `load`/`record`/entry point appends `station=config.DEFAULT_STATION`; default file path resolves via `paths.data_path(<basename>, station)`; GitHub cfg path via `paths.github_path`):
  - `forecast_log.load(path=None, station=...)`, `forecast_log.record(snap, basis=..., path=None, station=...)`
  - `consensus_log.load(path=None, station=...)`, `consensus_log.record(snap, basis=..., station=...)`
  - `betting_log.load(path=None, station=...)` and its writer(s)
  - `calibration.get(refresh=False, station=...)` — caches to `paths.data_path("calibration.json", station)`; its internal fetch/score helpers take `station` and pass it to `settlements`/`station_history`.
  - `scoring.score(basis="hourly", station=...)`, `scoring.market_accuracy(station=...)`, `scoring.same_day_cohort(..., station=...)` — actuals fetched via the station-aware `station_history` / `settlements`.

- [ ] **Step 1: Write the failing test**

```python
def test_logs_route_paths_by_station():
    import forecast_log, consensus_log, paths
    assert forecast_log.load(station="KAUS") == []          # namespaced, absent
    assert consensus_log.load(station="KAUS") == []
    assert isinstance(forecast_log.load(station="KDFW"), list)  # legacy bare file


def test_calibration_station_path(tmp_path, monkeypatch):
    import calibration, paths
    # KAUS calibration writes to the namespaced path, not the KDFW bare file.
    p = paths.data_path("calibration.json", "KAUS")
    assert p.endswith("data/KAUS/calibration.json".replace("/", __import__("os").sep))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_station_threading.py -k "logs_route or calibration_station" -q`
Expected: FAIL (`load() got an unexpected keyword argument 'station'`).

- [ ] **Step 3: Implement**

For each log module, change `path or _PATH` → `path or paths.data_path("<basename>.jsonl", station)`, thread `station` through `load`/`record`/`_write`, and route the `_github_cfg()` `path` value through `paths.github_path("<basename>.jsonl", station)`. In `calibration.py`, replace `_PATH` usage with `paths.data_path("calibration.json", station)`, thread `station` into `get()` and its `_forecast_daily_extremes`/settlement/history calls. In `scoring.py`, thread `station` into `score`, `market_accuracy`, `same_day_cohort`, `_settled_records`, `_actuals_for`, and pass it to the `station_history`/`settlements` fetches. Ensure the `data/<STATION>/` directory is created on first write (`os.makedirs(os.path.dirname(path), exist_ok=True)` in each `_write`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_station_threading.py -k "logs_route or calibration_station" -q`
Expected: PASS.

- [ ] **Step 5: Full-suite Dallas-identical gate**

Run: `python -m pytest -q`
Expected: PASS — unchanged count.

- [ ] **Step 6: Commit**

```bash
git add forecast_log.py consensus_log.py betting_log.py calibration.py scoring.py tests/test_station_threading.py
git commit -m "feat: thread station through logs/calibration/scoring via data_path"
```

---

### Task 8: KAUS end-to-end smoke + regression gate

**Files:**
- Test: `tests/test_station_threading.py` (extend)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write a station-agnostic smoke test (network-guarded)**

```python
def test_kaus_snapshot_smoke(monkeypatch):
    """snapshot(station='KAUS') runs end-to-end against stubbed sources and
    routes every source call with station='KAUS'."""
    import model
    seen = {"stations": set()}

    def fake_gather(*a, **k):
        seen["stations"].add(k.get("station"))
        return {}, {"obs": ([], []), "obs_continuous_display": (None, None)}, []

    monkeypatch.setattr(model, "gather_series", fake_gather)
    snap = model.snapshot(station="KAUS")
    assert snap["station"] == "KAUS"
    assert seen["stations"] == {"KAUS"}
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_station_threading.py::test_kaus_snapshot_smoke -q`
Expected: PASS.

- [ ] **Step 3: Full-suite final gate + count check**

Run: `python -m pytest -q`
Expected: PASS. Record the total count; it must equal the pre-Plan-1 baseline **plus** the new `test_station_*` / `test_data_path` tests — no existing test removed or changed except any additive snapshot-dict assertions noted in Task 5.

- [ ] **Step 4: Commit**

```bash
git add tests/test_station_threading.py
git commit -m "test: KAUS end-to-end snapshot smoke + station-threading regression gate"
```

---

## Self-Review

**Spec coverage (Plan 1 subset):** The spec's "station registry" → Task 1. "data namespacing / `data_path`" → Task 2. "thread `station` through model/settlement/sources/calibration/scoring" → Tasks 3–7. "Dallas byte-identical at every step" → the full-suite gate that closes Tasks 1,3–8 and the Global Constraints. Convective cold-start (empty Austin map) → Task 1 KAUS entry. **Not in Plan 1 (deferred to later plans, by design):** Austin data files seeding + Actions loop + settlement-basis verification (Plan 2), UI city control + per-page wiring (Plan 3), Austin trader + alerts (Plan 4). These consume Plan 1's `config.station()` / `data_path()` / `snapshot(station=...)` API.

**Placeholder scan:** No TBD/TODO steps. The one "TBD"-labeled value (Austin convective map = `{}`) is an intended empty initial value with a code comment, tested explicitly in Task 1, not a missing step.

**Type consistency:** `station: str = config.DEFAULT_STATION` is the uniform new param name and default across every signature in Tasks 3–8. `config.station(code)` returns `StationConfig` everywhere. `paths.data_path(name, station)` / `paths.github_path(name, station)` signatures are used identically in Tasks 2, 6, 7. `snapshot(...)["station"]` is written in Task 5 and asserted in Tasks 5 and 8.

---

## Follow-on plans (to be written after Plan 1 lands)

Each is its own `docs/superpowers/plans/` document, written once Plan 1's API is concrete:

- **Plan 2 — Austin online (data + Actions + settlement verification).** Verify CLIAUS basis + KATT-vs-KAUS station (blocking), seed `data/KAUS/` on the data branch, make the scheduled logging/CLI-report Actions loop over `config.STATION_CODES`, second ntfy stream.
- **Plan 3 — UI city control + per-page wiring.** `city_control(page_key)` component with sticky session-state city; per-page arity from the spec's table (2-way Forecast/Hourly, 3-way analytics, both-at-once Status/Trader); generic tab title.
- **Plan 4 — Austin autonomous trader + alerts.** Second per-station trader instance on the trade branch (own kill switch/mode/loss-cap), Trader-page combined safety summary + per-city edit toggle. Ships DISABLED + shadow.
