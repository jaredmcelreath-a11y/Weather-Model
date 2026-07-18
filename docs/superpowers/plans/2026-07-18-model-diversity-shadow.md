# Model Diversity via Shadow Consensus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add new global + AI weather models as *candidates* feeding a shadow consensus shown next to the untouched production consensus, so the two can be compared live before any promotion.

**Architecture:** Production model lists stay unchanged. New models live in separate `CANDIDATE_*` config lists. `gather_series`/`snapshot` take optional model-list overrides that default to the production lists, so the live number is identical by construction; only the shadow path passes candidate lists. Validation is champion-vs-challenger (backtest do-no-harm + forward-log both), and promotion is a manual, reversible one-line change.

**Tech Stack:** Python 3.9, Open-Meteo forecast/ensemble/historical-forecast APIs, pytest, Streamlit (display only).

## Global Constraints

- Python 3.9 compatible (no `match`, no `X | Y` runtime type calls; annotations OK via `from __future__ import annotations`).
- No new bias groups: new `det_*` labels group as `deterministic`, new `ens_*` labels as `ensemble` (already handled by `_group_of` in `model.py`).
- The production consensus number must not change. Production callers pass no model-list overrides.
- All new models arrive via the existing single bundled Open-Meteo call — no new source files, no per-model extra HTTP calls.
- Logging/display must never raise into the dashboard (wrap in try/except like existing call sites).
- Follow existing test style: `monkeypatch` + a `_Resp`-style fake, no network in tests.

---

### Task 1: Probe candidate model IDs + archive depth

Confirms which Open-Meteo model IDs actually return live data and how much historical-forecast archive they have at KDFW. Its output decides the exact IDs used in Task 2. This is a network run-and-record task (no TDD).

**Files:**
- Create: `scripts/probe_candidate_models.py`
- Create: `docs/benchmarks/2026-07-18-model-diversity/probe_results.md`

**Interfaces:**
- Produces: a confirmed list of valid deterministic + ensemble candidate IDs, written to `probe_results.md`, consumed by Task 2.

- [ ] **Step 1: Write the probe script**

```python
# scripts/probe_candidate_models.py
"""Probe candidate Open-Meteo models for live availability + archive depth at KDFW.

Run manually. Prints a table and is the source of truth for which candidate IDs
ship into config.CANDIDATE_* in Task 2. Not a unit test — it hits the network.
"""
from __future__ import annotations

from datetime import date, timedelta

from config import LAT, LON, TIMEZONE
from sources.common import get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

DET_CANDIDATES = [
    "ecmwf_aifs025_single",   # ECMWF AIFS (AI)
    "gfs_graphcast025",       # GraphCast (AI)
    "ukmo_seamless",          # UK Met Office
    "jma_seamless",           # JMA
    "meteofrance_seamless",   # Meteo-France ARPEGE/AROME
]
ENS_CANDIDATES = [
    "ukmo_global_ensemble_20km",
    "bom_access_global_ensemble",
]


def _count_temp_cols(data: dict) -> int:
    hourly = (data or {}).get("hourly", {})
    return sum(1 for k in hourly if k.startswith("temperature_2m"))


def _nonnull_frac(data: dict) -> float:
    hourly = (data or {}).get("hourly", {})
    cols = [v for k, v in hourly.items() if k.startswith("temperature_2m")]
    total = sum(len(c) for c in cols) or 1
    good = sum(1 for c in cols for x in c if x is not None)
    return good / total


def probe_live(url: str, model: str) -> dict:
    try:
        data = get_json(url, {
            "latitude": LAT, "longitude": LON,
            "hourly": "temperature_2m", "models": model,
            "temperature_unit": "fahrenheit", "timezone": TIMEZONE,
            "forecast_days": 2,
        }, ttl=0)
        return {"ok": _count_temp_cols(data) > 0,
                "cols": _count_temp_cols(data),
                "nonnull": round(_nonnull_frac(data), 2)}
    except Exception as e:  # noqa: BLE001 - probe reports failures, never raises
        return {"ok": False, "error": type(e).__name__}


def probe_archive(model: str, days: int = 45) -> dict:
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=days)
    try:
        data = get_json(HIST_URL, {
            "latitude": LAT, "longitude": LON,
            "hourly": "temperature_2m", "models": model,
            "temperature_unit": "fahrenheit", "timezone": TIMEZONE,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        }, ttl=0)
        hourly = (data or {}).get("hourly", {})
        n = len(hourly.get("time", []))
        return {"archive_hours": n, "nonnull": round(_nonnull_frac(data), 2)}
    except Exception as e:  # noqa: BLE001
        return {"archive_hours": 0, "error": type(e).__name__}


def main() -> None:
    print("=== DETERMINISTIC candidates (forecast API) ===")
    for m in DET_CANDIDATES:
        print(m, probe_live(FORECAST_URL, m), probe_archive(m))
    print("\n=== ENSEMBLE candidates (ensemble API) ===")
    for m in ENS_CANDIDATES:
        print(m, probe_live(ENSEMBLE_URL, m))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the probe**

Run: `cd "/Users/jared/Desktop/Weather Model" && python scripts/probe_candidate_models.py`
Expected: one line per model. A usable deterministic model shows `{'ok': True, 'cols': 1, 'nonnull': ~1.0}` live and `archive_hours` > 0. A usable ensemble model shows `cols` >= 5 (real member expansion, not just a control series).

- [ ] **Step 3: Record results**

Write `docs/benchmarks/2026-07-18-model-diversity/probe_results.md` capturing, verbatim, the printed table plus a one-line verdict per model: **INCLUDE** (live ok) or **DROP** (no live data / control-only ensemble). This file is the authoritative candidate list for Task 2. Note archive depth per model — models with `archive_hours == 0` still get INCLUDE-d (they flow into live consensus at flat weight) but are flagged "no archive yet".

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_candidate_models.py docs/benchmarks/2026-07-18-model-diversity/probe_results.md
git commit -m "chore: probe candidate Open-Meteo models for availability + archive"
```

---

### Task 2: Candidate model lists in config

Adds the `CANDIDATE_*` lists using only the probe-confirmed IDs. Production lists are left exactly as they are.

**Files:**
- Modify: `config.py` (after the `ENSEMBLE_MODELS` block, ~line 82)
- Test: `tests/test_candidate_config.py`

**Interfaces:**
- Consumes: the INCLUDE list from Task 1's `probe_results.md`.
- Produces: `config.CANDIDATE_DETERMINISTIC_MODELS: list[str]`, `config.CANDIDATE_ENSEMBLE_MODELS: list[str]` — each a **superset** of the production list.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidate_config.py
"""Candidate model lists are supersets of production and add real models."""
import config


def test_candidate_det_is_superset_of_production():
    prod = set(config.DETERMINISTIC_MODELS)
    cand = set(config.CANDIDATE_DETERMINISTIC_MODELS)
    assert prod <= cand
    assert len(cand) > len(prod)  # at least one new model was added


def test_candidate_ens_is_superset_of_production():
    prod = set(config.ENSEMBLE_MODELS)
    cand = set(config.CANDIDATE_ENSEMBLE_MODELS)
    assert prod <= cand  # ensemble candidates may equal prod if none survive probe


def test_production_lists_unchanged():
    # Guards against accidental edits to the live model set.
    assert config.DETERMINISTIC_MODELS == [
        "gfs_seamless", "ecmwf_ifs025", "icon_seamless",
        "gem_seamless", "gfs_hrrr",
    ]
    assert config.ENSEMBLE_MODELS == [
        "gfs_seamless", "icon_seamless", "ecmwf_ifs025", "gem_global_ensemble",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_candidate_config.py -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'CANDIDATE_DETERMINISTIC_MODELS'`.

- [ ] **Step 3: Add the candidate lists**

In `config.py`, immediately after the `ENSEMBLE_MODELS = [...]` block, add (replace the example new-model IDs with exactly the INCLUDE set from `probe_results.md` — drop any the probe marked DROP):

```python
# --- Candidate model sets (shadow consensus) ---
# Superset of the production lists used ONLY by the shadow/candidate consensus
# (see model.snapshot(include_candidate=True)). The production consensus never
# reads these. Promotion = move a model from here into the production list above.
CANDIDATE_DETERMINISTIC_MODELS = DETERMINISTIC_MODELS + [
    "ecmwf_aifs025_single",
    "gfs_graphcast025",
    "ukmo_seamless",
    "jma_seamless",
    "meteofrance_seamless",
]
CANDIDATE_ENSEMBLE_MODELS = ENSEMBLE_MODELS + [
    "ukmo_global_ensemble_20km",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_candidate_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_candidate_config.py
git commit -m "feat: candidate model lists for shadow consensus"
```

---

### Task 3: Null-filter guard in the source parsers

A candidate model can return `None` temperatures inside an otherwise-successful bundled response. Filter them per-series so `None` never reaches the sample set. Times become per-series (each series keeps only the timestamps it has real values for).

**Files:**
- Modify: `sources/open_meteo_models.py` (`_parse`, ~lines 24-33)
- Modify: `sources/open_meteo_ensemble.py` (`_parse`, ~lines 19-29)
- Test: `tests/test_parse_null_filter.py`

**Interfaces:**
- Produces: `_parse` in both modules drops `(time, value)` pairs where value is `None`; each series' times align with its own non-null values.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parse_null_filter.py
"""Both Open-Meteo parsers drop null temperatures per-series (flaky candidate)."""
from sources import open_meteo_models, open_meteo_ensemble


def test_models_parse_drops_null_temps_per_series():
    data = {"hourly": {
        "time": ["2026-07-18T00:00", "2026-07-18T01:00", "2026-07-18T02:00"],
        "temperature_2m_gfs_seamless": [70.0, None, 72.0],
        "temperature_2m_jma_seamless": [None, None, None],
    }}
    out = open_meteo_models._parse(data)
    gfs_times, gfs_temps = out["det_gfs_seamless"]
    assert gfs_temps == [70.0, 72.0]
    assert len(gfs_times) == 2  # times filtered alongside values
    # An all-null series yields empty lists, not a series full of None.
    assert out["det_jma_seamless"] == ([], [])


def test_ensemble_parse_drops_null_temps_per_series():
    data = {"hourly": {
        "time": ["2026-07-18T00:00", "2026-07-18T01:00"],
        "temperature_2m_member01_ukmo_global_ensemble_20km": [80.0, None],
    }}
    out = open_meteo_ensemble._parse(data)
    times, temps = out["ens_member01_ukmo_global_ensemble_20km"]
    assert temps == [80.0]
    assert len(times) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_parse_null_filter.py -v`
Expected: FAIL — current `_parse` returns the shared `times` list and raw values including `None`.

- [ ] **Step 3: Implement the filter in `open_meteo_models._parse`**

Replace the body of `_parse` in `sources/open_meteo_models.py`:

```python
def _parse(data: dict) -> dict[str, tuple[list[datetime], list[float]]]:
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if key == "time" or not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "det_")
        pairs = [(t, v) for t, v in zip(times, values) if v is not None]
        out[label] = ([t for t, _ in pairs], [v for _, v in pairs])
    return out
```

- [ ] **Step 4: Implement the filter in `open_meteo_ensemble._parse`**

Replace the body of `_parse` in `sources/open_meteo_ensemble.py`:

```python
def _parse(data: dict) -> dict[str, tuple[list[datetime], list[float]]]:
    hourly = data["hourly"]
    times = parse_local_times(hourly["time"])
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for key, values in hourly.items():
        if not key.startswith("temperature_2m"):
            continue
        label = key.replace("temperature_2m_", "ens_") if key != "temperature_2m" else "ens_control"
        pairs = [(t, v) for t, v in zip(times, values) if v is not None]
        out[label] = ([t for t, _ in pairs], [v for _, v in pairs])
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_parse_null_filter.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the existing source tests to confirm no regression**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_source_resilience.py tests/test_source_coverage.py -v`
Expected: PASS (all existing tests still green).

- [ ] **Step 7: Commit**

```bash
git add sources/open_meteo_models.py sources/open_meteo_ensemble.py tests/test_parse_null_filter.py
git commit -m "fix: drop null temperatures per-series in Open-Meteo parsers"
```

---

### Task 4: Model-list overrides in the fetch + gather path (production-invariant)

Parameterize the fetchers and `gather_series` so the shadow path can request candidate models, while production callers (passing nothing) get byte-identical behavior.

**Files:**
- Modify: `sources/open_meteo_models.py` (`fetch`, ~line 39)
- Modify: `sources/open_meteo_ensemble.py` (`fetch`, ~line 48)
- Modify: `model.py` (`gather_series`, ~lines 886-928; import already covers these modules)
- Test: `tests/test_gather_overrides.py`

**Interfaces:**
- Consumes: `config.CANDIDATE_DETERMINISTIC_MODELS`, `config.CANDIDATE_ENSEMBLE_MODELS`.
- Produces:
  - `open_meteo_models.fetch(forecast_days=2, models=None)` — `models` defaults to `DETERMINISTIC_MODELS`.
  - `open_meteo_ensemble.fetch(forecast_days=2, models=None)` — `models` defaults to `ENSEMBLE_MODELS`.
  - `model.gather_series(forecast_days=2, continuous_obs=False, now=None, det_models=None, ens_models=None)` — passes the overrides to the two fetchers; `None` means production.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gather_overrides.py
"""gather_series passes model-list overrides; default path is production."""
import config
import model
from sources import open_meteo_models, open_meteo_ensemble


def test_fetch_defaults_to_production_models(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["models"] = params["models"]
        return {"hourly": {"time": ["2026-07-18T00:00"],
                           "temperature_2m_gfs_seamless": [70.0]}}
    monkeypatch.setattr(open_meteo_models, "get_json", fake_get_json)
    open_meteo_models.fetch()
    assert seen["models"] == ",".join(config.DETERMINISTIC_MODELS)


def test_fetch_uses_override_models(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["models"] = params["models"]
        return {"hourly": {"time": ["2026-07-18T00:00"],
                           "temperature_2m_ukmo_seamless": [71.0]}}
    monkeypatch.setattr(open_meteo_models, "get_json", fake_get_json)
    open_meteo_models.fetch(models=config.CANDIDATE_DETERMINISTIC_MODELS)
    assert seen["models"] == ",".join(config.CANDIDATE_DETERMINISTIC_MODELS)


def test_gather_series_routes_overrides(monkeypatch):
    calls = {}

    def fake_det(forecast_days=2, models=None):
        calls["det"] = models
        return {}

    def fake_ens(forecast_days=2, models=None):
        calls["ens"] = models
        return {}
    monkeypatch.setattr(model.open_meteo_models, "fetch", fake_det)
    monkeypatch.setattr(model.open_meteo_ensemble, "fetch", fake_ens)
    monkeypatch.setattr(model.nws_forecast, "fetch", lambda: {})
    monkeypatch.setattr(model.iem_mos, "fetch", lambda forecast_days=2: {})
    monkeypatch.setattr(model.nws_observations, "fetch",
                        lambda continuous=True, now=None: {"obs": ([], [])})

    model.gather_series(det_models=config.CANDIDATE_DETERMINISTIC_MODELS,
                        ens_models=config.CANDIDATE_ENSEMBLE_MODELS)
    assert calls["det"] == config.CANDIDATE_DETERMINISTIC_MODELS
    assert calls["ens"] == config.CANDIDATE_ENSEMBLE_MODELS

    model.gather_series()  # production defaults => None passed through
    assert calls["det"] is None
    assert calls["ens"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_gather_overrides.py -v`
Expected: FAIL — `fetch()` has no `models` kwarg; `gather_series` has no `det_models`/`ens_models`.

- [ ] **Step 3: Add `models` to `open_meteo_models.fetch`**

Replace `fetch` in `sources/open_meteo_models.py`:

```python
def fetch(forecast_days: int = 2, models=None) -> dict[str, tuple[list[datetime], list[float]]]:
    """Live deterministic forecasts, {model_label: (times, temps_f)}.

    `models` overrides the production DETERMINISTIC_MODELS (used by the shadow
    consensus); None keeps production behavior."""
    data = get_json(FORECAST_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(models or DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _parse(data)
```

- [ ] **Step 4: Add `models` to `open_meteo_ensemble.fetch`**

Replace `fetch` in `sources/open_meteo_ensemble.py`:

```python
def fetch(forecast_days: int = 2, models=None) -> dict[str, tuple[list[datetime], list[float]]]:
    """Return {member_label: (times, temps_f)} across all ensemble systems.

    `models` overrides the production ENSEMBLE_MODELS (shadow consensus); None
    keeps production behavior."""
    data = get_json(URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(models or ENSEMBLE_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    })
    return _warn_if_thin(_parse(data))
```

- [ ] **Step 5: Add overrides to `model.gather_series`**

In `model.py`, change the `gather_series` signature and the two Open-Meteo entries in `forecast_sources`:

```python
def gather_series(forecast_days: int = 2, continuous_obs: bool = False,
                  now: datetime | None = None, det_models=None, ens_models=None):
```

and

```python
    forecast_sources = [
        ("open-meteo ensemble", lambda: open_meteo_ensemble.fetch(forecast_days, models=ens_models)),
        ("open-meteo models", lambda: open_meteo_models.fetch(forecast_days, models=det_models)),
        ("nws forecast", lambda: nws_forecast.fetch()),
        ("iem mos", lambda: iem_mos.fetch(forecast_days)),
    ]
```

(Leave the rest of `gather_series` — the docstring can gain one line noting `det_models`/`ens_models` default to production.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_gather_overrides.py tests/test_source_resilience.py -v`
Expected: PASS (new + existing green).

- [ ] **Step 7: Commit**

```bash
git add sources/open_meteo_models.py sources/open_meteo_ensemble.py model.py tests/test_gather_overrides.py
git commit -m "feat: optional model-list overrides in fetch + gather_series"
```

---

### Task 5: Candidate consensus in `snapshot` (production-invariant)

Add an opt-in candidate computation to `snapshot`. When enabled it runs a second, isolated `gather_series` with the candidate lists and attaches candidate predictions. When disabled (default), `snapshot` is unchanged.

**Files:**
- Modify: `model.py` (`snapshot`, ~lines 967-1012)
- Test: `tests/test_shadow_snapshot.py`

**Interfaces:**
- Consumes: `gather_series(det_models=..., ens_models=...)` (Task 4), `config.CANDIDATE_*`.
- Produces: `model.snapshot(calib=None, settle_offset=None, continuous_obs=False, include_candidate=False)`. When `include_candidate=True`, the returned dict gains `snap["candidate"] = {"today": <predict>, "tomorrow": <predict>}`, each shaped like `snap["today"]` (has `["high"]["consensus"]`, `["low"]["consensus"]`). The candidate block is best-effort: on any error it is omitted, never raised.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shadow_snapshot.py
"""snapshot(include_candidate=True) attaches an isolated candidate block."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _series_for(models_marker):
    # A single flat deterministic series so day_high_low returns a real number.
    now = datetime.now(_TZ)
    times = [now.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in range(24)]
    temps = [80.0 + models_marker] * 24
    return {f"det_probe": (times, temps)}


def test_include_candidate_attaches_block_and_uses_candidate_models(monkeypatch):
    seen = {"det_models": []}

    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None):
        seen["det_models"].append(det_models)
        marker = 0 if det_models is None else 1
        return _series_for(marker), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot(include_candidate=True)
    # Production block present and unchanged in shape.
    assert "consensus" in snap["today"]["high"]
    # Candidate block present.
    assert "candidate" in snap
    assert "consensus" in snap["candidate"]["today"]["high"]
    # Two gather calls: one production (None), one candidate (candidate list).
    assert None in seen["det_models"]
    assert config.CANDIDATE_DETERMINISTIC_MODELS in seen["det_models"]


def test_default_snapshot_has_no_candidate_block(monkeypatch):
    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None):
        return _series_for(0), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot()
    assert "candidate" not in snap
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_shadow_snapshot.py -v`
Expected: FAIL — `snapshot` has no `include_candidate` kwarg / no `candidate` key.

- [ ] **Step 3: Implement `include_candidate` in `snapshot`**

Change the `snapshot` signature in `model.py`:

```python
def snapshot(calib: dict | None = None, settle_offset=None,
             continuous_obs: bool = False, include_candidate: bool = False) -> dict:
```

Build the return dict into a local `snap` variable (instead of returning the literal directly), then append the candidate block. Replace the final `return {...}` with:

```python
    snap = {
        "updated": now.isoformat(timespec="seconds"),
        "today": _predict_from(series, obs, today, now, calib, settle_offset, live=True),
        "tomorrow": _predict_from(series, obs, tomorrow, now, calib, settle_offset, live=True),
        "current": current,
        "current_hourly": current_hourly,
        "sources": {"today": per_source_extremes(series, today),
                    "tomorrow": per_source_extremes(series, tomorrow)},
        "storm": _storm_status(today, now),
        "dropped_sources": dropped,
    }
    if include_candidate:
        # Fully isolated second fetch with the candidate model set. Best-effort:
        # the shadow must never break the production snapshot.
        try:
            from config import (CANDIDATE_DETERMINISTIC_MODELS,
                                 CANDIDATE_ENSEMBLE_MODELS)
            c_series, c_obs, _c_dropped = gather_series(
                forecast_days=3, continuous_obs=continuous_obs, now=now,
                det_models=CANDIDATE_DETERMINISTIC_MODELS,
                ens_models=CANDIDATE_ENSEMBLE_MODELS)
            snap["candidate"] = {
                "today": _predict_from(c_series, c_obs, today, now, calib, settle_offset, live=True),
                "tomorrow": _predict_from(c_series, c_obs, tomorrow, now, calib, settle_offset, live=True),
            }
        except Exception:
            pass
    return snap
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_shadow_snapshot.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_shadow_snapshot.py
git commit -m "feat: opt-in candidate (shadow) consensus in model.snapshot"
```

---

### Task 6: Shadow comparison — data helper + Forecast-page display

A pure helper turns a snapshot into comparison rows (tested without Streamlit), then a thin render function shows them under the Forecast title. The Kalshi snapshot loader is switched to request the candidate block.

**Files:**
- Create: `shadow.py`
- Test: `tests/test_shadow_comparison.py`
- Modify: `market_view.py` (`render_page`, after `st.title(...)` ~line 1789)
- Modify: `app.py` (`load_snapshot_kalshi`, ~line 64-71)

**Interfaces:**
- Consumes: a snapshot dict that may carry `snap["candidate"]`.
- Produces: `shadow.consensus_comparison(snap) -> list[dict]`. Empty list when no candidate block. Otherwise one row per (which, variable) with keys: `day` (`"today"`/`"tomorrow"`), `variable` (`"high"`/`"low"`), `production` (float|None), `candidate` (float|None), `gap` (float|None = candidate - production, rounded to 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shadow_comparison.py
"""shadow.consensus_comparison diffs production vs candidate consensus."""
import shadow


def _pred(hi, lo):
    return {"high": {"consensus": hi}, "low": {"consensus": lo}}


def test_no_candidate_block_returns_empty():
    snap = {"today": _pred(95, 78), "tomorrow": _pred(96, 79)}
    assert shadow.consensus_comparison(snap) == []


def test_comparison_rows_and_gap():
    snap = {
        "today": _pred(95.0, 78.0), "tomorrow": _pred(96.0, 79.0),
        "candidate": {"today": _pred(96.2, 77.5), "tomorrow": _pred(95.0, 79.0)},
    }
    rows = shadow.consensus_comparison(snap)
    assert len(rows) == 4
    today_high = next(r for r in rows if r["day"] == "today" and r["variable"] == "high")
    assert today_high["production"] == 95.0
    assert today_high["candidate"] == 96.2
    assert today_high["gap"] == 1.2
    tomorrow_low = next(r for r in rows if r["day"] == "tomorrow" and r["variable"] == "low")
    assert tomorrow_low["gap"] == 0.0


def test_missing_consensus_is_none_safe():
    snap = {
        "today": _pred(None, 78.0), "tomorrow": _pred(96.0, 79.0),
        "candidate": {"today": _pred(96.0, None), "tomorrow": _pred(95.0, 79.0)},
    }
    rows = shadow.consensus_comparison(snap)
    today_high = next(r for r in rows if r["day"] == "today" and r["variable"] == "high")
    assert today_high["production"] is None
    assert today_high["gap"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_shadow_comparison.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shadow'`.

- [ ] **Step 3: Implement `shadow.py`**

```python
# shadow.py
"""Shadow (candidate) consensus comparison — pure data, no Streamlit.

Turns a snapshot that carries an optional `candidate` block (from
model.snapshot(include_candidate=True)) into comparison rows the Forecast page
renders next to the production consensus.
"""
from __future__ import annotations


def _consensus(pred: dict, variable: str):
    d = (pred or {}).get(variable) or {}
    return d.get("consensus")


def consensus_comparison(snap: dict) -> list[dict]:
    """[{day, variable, production, candidate, gap}] for today/tomorrow high/low.

    Empty when the snapshot has no candidate block. `gap` = candidate -
    production (rounded to 0.1), or None if either side is missing.
    """
    candidate = (snap or {}).get("candidate")
    if not candidate:
        return []
    rows: list[dict] = []
    for which in ("today", "tomorrow"):
        prod_pred = snap.get(which) or {}
        cand_pred = candidate.get(which) or {}
        for variable in ("high", "low"):
            p = _consensus(prod_pred, variable)
            c = _consensus(cand_pred, variable)
            gap = round(c - p, 1) if (p is not None and c is not None) else None
            rows.append({"day": which, "variable": variable,
                         "production": p, "candidate": c, "gap": gap})
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_shadow_comparison.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Request the candidate block in the Kalshi snapshot loader**

In `app.py`, update `load_snapshot_kalshi` (lines 63-70) to pass `include_candidate=True`. The only change is adding that one kwarg to the existing `model.snapshot(...)` call:

```python
@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot_kalshi():
    """Snapshot shifted to the Kalshi/CLI settlement basis via the calibrated
    settlement_offset (absent offset -> behaves like the hourly snapshot)."""
    calib = calibration.get(refresh=True)
    snap = model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"),
                          continuous_obs=True, include_candidate=True)
    return snap, calib
```

- [ ] **Step 6: Render the comparison under the Forecast title**

In `market_view.py`, immediately after `st.title("Dallas Daily High & Low")` in `render_page`, add:

```python
    _render_shadow_comparison(snap)
```

Then add this module-level function to `market_view.py` (near the other small render helpers):

```python
def _render_shadow_comparison(snap):
    """Small expander comparing the production consensus to the candidate
    (shadow) model set. Renders only when the snapshot carries a candidate
    block; never raises into the page."""
    try:
        import shadow
        rows = shadow.consensus_comparison(snap)
    except Exception:
        rows = []
    if not rows:
        return
    with st.expander("🧪 Candidate model set (shadow) — not live"):
        st.caption("Second consensus from the expanded model set (AI + extra "
                   "global models). Compare-only; the live numbers above are "
                   "unchanged.")
        lines = ["| Day | Var | Production | Candidate | Gap |",
                 "|---|---|---|---|---|"]
        for r in rows:
            def _f(x):
                return "—" if x is None else f"{x:.1f}°F"
            gap = "—" if r["gap"] is None else f"{r['gap']:+.1f}"
            lines.append(f"| {r['day']} | {r['variable']} | "
                         f"{_f(r['production'])} | {_f(r['candidate'])} | {gap} |")
        st.markdown("\n".join(lines))
```

- [ ] **Step 7: Verify the helper tests still pass (display code is untested-by-design)**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_shadow_comparison.py -v`
Expected: PASS. (The Streamlit render is a thin wrapper over the tested helper; per the repo's local env gaps, `market_view` render code is not unit-tested here.)

- [ ] **Step 8: Commit**

```bash
git add shadow.py tests/test_shadow_comparison.py market_view.py app.py
git commit -m "feat: shadow consensus comparison on the Forecast page"
```

---

### Task 7: Forward-log both consensus numbers head-to-head

Persist the candidate consensus alongside production in the forward log so the real day-ahead comparison accumulates over time and is scored against settlement later. Reuses all existing persistence/GitHub plumbing.

**Files:**
- Modify: `forecast_log.py` (`record`, in the per-variable record build ~lines 165-186)
- Modify: `scheduled_log.py` (`_log_snapshots`, the `model.snapshot(...)` call ~line 40)
- Test: `tests/test_forecast_log_candidate.py`

**Interfaces:**
- Consumes: `snap["candidate"][which][variable]["consensus"]` (Task 5).
- Produces: each forward-log record gains `candidate_consensus` (float) **only when** the snapshot carries a candidate value for that (which, variable). Records without a candidate are byte-identical to today.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forecast_log_candidate.py
"""forecast_log.record stamps candidate_consensus when the snapshot carries it."""
from datetime import datetime
from zoneinfo import ZoneInfo

import forecast_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _pred(day_iso, hi, lo):
    return {
        "day": day_iso,
        "high": {"consensus": hi, "probabilities": {"95": 1.0}},
        "low": {"consensus": lo, "probabilities": {"78": 1.0}},
    }


def _snap(candidate=None):
    now = datetime(2026, 7, 18, 12, 0, tzinfo=_TZ)
    snap = {
        "updated": now.isoformat(timespec="seconds"),
        "today": _pred("2026-07-18", 95, 78),
        "tomorrow": _pred("2026-07-19", 96, 79),
    }
    if candidate is not None:
        snap["candidate"] = candidate
    return snap


def test_candidate_consensus_recorded_when_present(tmp_path):
    path = str(tmp_path / "log.jsonl")
    cand = {"today": _pred("2026-07-18", 96.2, 77.5),
            "tomorrow": _pred("2026-07-19", 95.0, 79.0)}
    forecast_log.record(_snap(cand), path=path)
    rows = forecast_log.load(path)
    today_high = next(r for r in rows if r["target_date"] == "2026-07-18"
                      and r["variable"] == "high" and "capture_cohort" not in r)
    assert today_high["candidate_consensus"] == 96.2


def test_no_candidate_key_when_absent(tmp_path):
    path = str(tmp_path / "log.jsonl")
    forecast_log.record(_snap(), path=path)
    rows = forecast_log.load(path)
    assert all("candidate_consensus" not in r for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_forecast_log_candidate.py -v`
Expected: FAIL — `candidate_consensus` is never written.

- [ ] **Step 3: Stamp `candidate_consensus` in `forecast_log.record`**

In `forecast_log.py`, inside `record`, read the candidate block once near the top of the function (after `sources = snapshot.get("sources", {})`):

```python
    candidate = snapshot.get("candidate", {})
```

Then, in the per-`variable` loop where `rec` is built (right after the `corr = d.get("corrections")` block, before the `src = _source_means(...)` block), add:

```python
            # Shadow/candidate consensus for the expanded model set, logged
            # head-to-head so day-ahead skill can be scored vs production later.
            # Only when present — production-only rows stay byte-identical.
            cand_pred = candidate.get(which) or {}
            cand_c = (cand_pred.get(variable) or {}).get("consensus")
            if cand_c is not None:
                rec["candidate_consensus"] = cand_c
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_forecast_log_candidate.py tests/test_forecast_log_sources.py -v`
Expected: PASS (new + existing forecast-log tests green).

- [ ] **Step 5: Have the scheduled logger produce the candidate block**

In `scheduled_log.py`, `_log_snapshots`, change the CLI snapshot call to request the candidate:

```python
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True,
                              include_candidate=True)
```

- [ ] **Step 6: Run the scheduled-log guard tests**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_scheduled_log_guard.py -v`
Expected: PASS (no regression from the extra snapshot kwarg).

- [ ] **Step 7: Commit**

```bash
git add forecast_log.py scheduled_log.py tests/test_forecast_log_candidate.py
git commit -m "feat: forward-log candidate consensus head-to-head with production"
```

---

### Task 8: Backtest champion-vs-challenger + do-no-harm assessment

Let the backtest score any deterministic model set, run production vs candidate over the same archive window, and write the do-no-harm assessment. (Backtest uses the deterministic historical archive only — the new *deterministic* models are what it compares; new ensemble members are judged by the forward log from Task 7.)

**Files:**
- Modify: `sources/open_meteo_models.py` (`fetch_historical`, ~lines 45-63)
- Modify: `backtest.py` (`run`, ~lines 125-130)
- Create: `docs/benchmarks/2026-07-18-model-diversity/ASSESSMENT.md`
- Test: `tests/test_backtest_det_models.py`

**Interfaces:**
- Consumes: `config.CANDIDATE_DETERMINISTIC_MODELS`.
- Produces:
  - `open_meteo_models.fetch_historical(start, end, ttl=24*3600, models=None)` — `models` defaults to `DETERMINISTIC_MODELS`.
  - `backtest.run(days=60, cli=False, settle_offset=None, det_models=None)` — `det_models` passed through to `fetch_historical`; `None` = production.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_det_models.py
"""backtest.run threads a deterministic model-set override to the archive fetch.

Uses a sentinel raised from the (stubbed) archive fetch so the assertion runs
right after the model list is captured — before run()'s scoring loop, which
would divide by zero on the empty stub series."""
import pytest

import config
import backtest


class _Stop(Exception):
    pass


def test_run_passes_det_models_to_fetch_historical(monkeypatch):
    seen = {}

    def fake_hist(start, end, ttl=24 * 3600, models=None):
        seen["models"] = models
        raise _Stop  # captured the list; stop before the scoring loop

    monkeypatch.setattr(backtest.station_history, "fetch_actual",
                        lambda start, end: {})
    monkeypatch.setattr(backtest.open_meteo_models, "fetch_historical", fake_hist)

    with pytest.raises(_Stop):
        backtest.run(days=5, det_models=config.CANDIDATE_DETERMINISTIC_MODELS)
    assert seen["models"] == config.CANDIDATE_DETERMINISTIC_MODELS

    with pytest.raises(_Stop):
        backtest.run(days=5)  # production default => None
    assert seen["models"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_backtest_det_models.py -v`
Expected: FAIL — `fetch_historical` has no `models` kwarg; `run` has no `det_models`.

- [ ] **Step 3: Add `models` to `fetch_historical`**

Replace `fetch_historical` in `sources/open_meteo_models.py`:

```python
def fetch_historical(start: date, end: date,
                     ttl: int = 24 * 3600, models=None) -> dict[str, tuple[list[datetime], list[float]]]:
    """Archived past *forecasts* over [start, end] for bias calibration.

    `models` overrides DETERMINISTIC_MODELS (for the shadow backtest); None keeps
    production behavior."""
    data = get_json(HISTORICAL_URL, {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "models": ",".join(models or DETERMINISTIC_MODELS),
        "temperature_unit": "fahrenheit",
        "timezone": TIMEZONE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }, ttl=ttl)
    return _parse(data)
```

- [ ] **Step 4: Add `det_models` to `backtest.run`**

In `backtest.py`, change the `run` signature and the historical fetch line:

```python
def run(days: int = 60, cli: bool = False, settle_offset=None, det_models=None) -> dict:
```

and

```python
    series = open_meteo_models.fetch_historical(start, end, models=det_models)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_backtest_det_models.py -v`
Expected: PASS (1 test).

- [ ] **Step 6: Run the champion-vs-challenger comparison**

Run:

```bash
cd "/Users/jared/Desktop/Weather Model" && python -c "
import json, backtest, config
prod = backtest.run(days=45)
cand = backtest.run(days=45, det_models=config.CANDIDATE_DETERMINISTIC_MODELS)
for var in ('high','low'):
    p, c = prod[var], cand[var]
    print(var.upper(), 'n=', p['n_days'], c['n_days'])
    for k in ('mae','brier','crps','coverage_50','coverage_80','exact_peak'):
        print(f'  {k:14s} prod={p[k]}  cand={c[k]}')
"
```

Expected: two metric blocks (HIGH/LOW), each printing production vs candidate MAE / Brier / CRPS / coverage / exact-peak. Capture this output for the assessment.

- [ ] **Step 7: Write the do-no-harm assessment**

Create `docs/benchmarks/2026-07-18-model-diversity/ASSESSMENT.md` with: the command from Step 6, its captured output table, and a verdict per metric — **candidate must not degrade** MAE/Brier/CRPS and should keep coverage near 50/80. State the known caveat verbatim: the historical-forecast archive is near-analysis, so this is a same-basis regression check, not a true day-ahead proxy; the forward log (Task 7) is the day-ahead judge. Conclude PASS (safe to keep shadowing) or FLAG (investigate before leaving the shadow running).

- [ ] **Step 8: Commit**

```bash
git add sources/open_meteo_models.py backtest.py tests/test_backtest_det_models.py docs/benchmarks/2026-07-18-model-diversity/ASSESSMENT.md
git commit -m "feat: champion-vs-challenger backtest + do-no-harm assessment"
```

---

### Task 9: Full suite + wrap-up

Confirm nothing regressed and the branch is coherent.

- [ ] **Step 1: Run the whole collectable suite**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest -q`
Expected: PASS for all locally-collectable tests. (Per the repo's known local-env gaps, tests importing `streamlit`/`cryptography` may be skipped/uncollectable — note any that don't collect, but the new tests from Tasks 2-8 must all pass.)

- [ ] **Step 2: Sanity-check the production number is untouched**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_candidate_config.py::test_production_lists_unchanged tests/test_shadow_snapshot.py::test_default_snapshot_has_no_candidate_block tests/test_forecast_log_candidate.py::test_no_candidate_key_when_absent -v`
Expected: PASS — production model lists, default snapshot, and default log rows are all unchanged.

- [ ] **Step 3: Final commit if anything is uncommitted**

```bash
cd "/Users/jared/Desktop/Weather Model" && git status --short
# commit any stragglers with an appropriate message
```

---

## Promotion (post-validation, not part of this plan)

Once the shadow comparison + forward log convince you, promote by moving the winning models from `CANDIDATE_DETERMINISTIC_MODELS` / `CANDIDATE_ENSEMBLE_MODELS` into `DETERMINISTIC_MODELS` / `ENSEMBLE_MODELS` in `config.py` (one edit). Rollback is the reverse. No code changes required.
