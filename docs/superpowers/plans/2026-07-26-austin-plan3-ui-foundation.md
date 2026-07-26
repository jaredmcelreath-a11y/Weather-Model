# Austin — Plan 3: UI City-Control Foundation + Live Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the in-page city control and make the two primary daily-use pages — **Forecast** and **Hourly** — switch between Dallas and Austin, backed by the Plan 1/2 station-aware pipeline and the now-flowing `data/KAUS/*`. Ship a complete, usable two-city experience on the pages that matter most; the analytics/ops pages follow in Plan 3b.

**Architecture:** A small `city_view.py` owns the control and its pure selection logic (unit-testable; Streamlit mocked in tests). Each page function in `app.py` calls `city_control(...)`, gets a station code, and passes it to the now-station-parameterized cached loaders. The rendered pages derive their display name from the snapshot's `station` tag rather than hardcoded "Dallas", so `market_view`/`hourly_view` internals barely change. Sticky city lives in `st.session_state` so the pick follows you between pages.

**Tech Stack:** Streamlit 1.50 (`st.segmented_control`), Python 3.9, the existing dark-serif dashboard theme.

## Global Constraints

- **Dallas byte-identical by default.** With Dallas selected (the sticky default on first load), every page renders exactly as today — same title, same data, same cache behavior. Verified by the full suite + a Dallas screenshot diff.
- **STANDING UI CONSTRAINT (user):** everything new is **Title-Cased**, **mobile-friendly**, and uses **tooltips** — consistent with the dark-serif dashboard. The city control uses Title-Case labels ("Dallas"/"Austin"), a `help=` tooltip, and `st.segmented_control` (responsive/touch-friendly). No raw lowercase labels, no fixed-width layouts.
- **Cache correctness:** every `@st.cache_data` loader that becomes station-aware MUST take `station` as an argument so the cache keys on it — otherwise Austin and Dallas would collide in one cache entry.
- **Python 3.9**; `from __future__ import annotations` in new modules.
- **Deferred to Plan 3b (do NOT build here):** History/Journal/Lab/Edge/Accuracy 3-way "Both", and Status/Trader both-at-once. Those pages stay Dallas-only this plan (unchanged).

---

## File Structure

- `city_view.py` — **create.** The city control + pure helpers (`codes_for`, `resolve_selection`, `display_name`, `city_control`).
- `app.py` — **modify.** Station-parameterize the live loaders (`load_snapshot_kalshi`, `load_accuracy_kalshi`, `load_recap`, `load_cli_report`, `load_portfolio_value`, `load_hourly`); the `_page`/`kalshi_page`/`hourly_page` functions call `city_control` and thread the station; `set_page_config` title → generic.
- `market_view.py` — **modify.** Title + the ~3 "Dallas" literals derive from `snap["station"]`.
- `hourly_view.py` — **modify.** Title + the Dallas/DFW literals + the PWS note derive from the active station.
- `tests/test_city_view.py` — **create.**

---

### Task 1: `city_view` module (control + pure selection logic)

**Files:**
- Create: `city_view.py`
- Test: `tests/test_city_view.py`

**Interfaces:**
- Produces:
  - `SELECTIONS_2 = ["Dallas", "Austin"]`, `SELECTIONS_3 = ["Dallas", "Austin", "Both"]` (Title-Case labels).
  - `display_name(code: str) -> str` → `config.station(code).name`.
  - `codes_for(selection: str) -> list[str]` → `"Dallas"→["KDFW"]`, `"Austin"→["KAUS"]`, `"Both"→config.STATION_CODES`.
  - `resolve_selection(state: dict, page_key: str, arity: int) -> str` — pure sticky logic: 2-way pages default to the sticky single-city (`state["city"]`, itself defaulting to "Dallas"); 3-way pages default to "Both" but remember their own last pick in `state[f"city_{page_key}"]`.
  - `city_control(page_key: str, arity: int = 2) -> str` — renders `st.segmented_control` with the right options, seeds/reads `st.session_state`, updates the sticky single-city on a Dallas/Austin pick, and returns the selected **station code** for a 2-way page (`"KDFW"`/`"KAUS"`) or the raw selection for a 3-way page. Wrapper around `resolve_selection` + Streamlit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_city_view.py
import sys
from unittest.mock import MagicMock
try:
    import streamlit  # noqa: F401
except ImportError:
    sys.modules.setdefault("streamlit", MagicMock())

import city_view


def test_codes_for():
    assert city_view.codes_for("Dallas") == ["KDFW"]
    assert city_view.codes_for("Austin") == ["KAUS"]
    assert city_view.codes_for("Both") == ["KDFW", "KAUS"]


def test_display_name():
    assert city_view.display_name("KDFW") == "Dallas"
    assert city_view.display_name("KAUS") == "Austin"


def test_resolve_2way_defaults_to_sticky_dallas():
    state = {}
    assert city_view.resolve_selection(state, "forecast", 2) == "Dallas"
    # a prior Austin pick sticks across pages
    state["city"] = "Austin"
    assert city_view.resolve_selection(state, "hourly", 2) == "Austin"


def test_resolve_3way_defaults_both_but_remembers_own_pick():
    state = {"city": "Austin"}          # sticky single-city doesn't force 3-way
    assert city_view.resolve_selection(state, "edge", 3) == "Both"
    state["city_edge"] = "Dallas"       # the page's own remembered pick wins
    assert city_view.resolve_selection(state, "edge", 3) == "Dallas"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_city_view.py -q`
Expected: FAIL (`No module named 'city_view'`).

- [ ] **Step 3: Implement `city_view.py`**

```python
"""In-page city control for the two-city dashboard. Pure selection logic is
separated from the Streamlit widget so it unit-tests without a running app."""
from __future__ import annotations

import streamlit as st

import config

SELECTIONS_2 = ["Dallas", "Austin"]
SELECTIONS_3 = ["Dallas", "Austin", "Both"]
_LABEL_TO_CODE = {config.station(c).name: c for c in config.STATION_CODES}


def display_name(code: str) -> str:
    return config.station(code).name


def codes_for(selection: str) -> list[str]:
    if selection == "Both":
        return list(config.STATION_CODES)
    return [_LABEL_TO_CODE[selection]]


def resolve_selection(state: dict, page_key: str, arity: int) -> str:
    if arity == 3:
        return state.get(f"city_{page_key}", "Both")
    return state.get("city", "Dallas")


def city_control(page_key: str, arity: int = 2) -> str:
    """Render the toggle; return a station code (2-way) or the raw selection
    (3-way, one of Dallas/Austin/Both). Sticky single-city follows the user."""
    options = SELECTIONS_3 if arity == 3 else SELECTIONS_2
    default = resolve_selection(st.session_state, page_key, arity)
    choice = st.segmented_control(
        "City", options, default=default, key=f"citysel_{page_key}",
        help="Switch which city this page shows. Your pick follows you between pages.")
    if choice is None:
        choice = default
    if choice in ("Dallas", "Austin"):
        st.session_state["city"] = choice           # update the sticky single-city
    if arity == 3:
        st.session_state[f"city_{page_key}"] = choice
        return choice
    return codes_for(choice)[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_city_view.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add city_view.py tests/test_city_view.py
git commit -m "feat: city_view control + pure selection logic (sticky city)"
```

---

### Task 2: Station-parameterize the live cached loaders

**Files:**
- Modify: `app.py`
- Test: (covered by the full suite import + Task 5 visual verify; these are thin cache wrappers)

**Interfaces:**
- Consumes: Plan 1/2 `station=`-aware `model.snapshot`, `calibration.get`, `scoring`, `settlements`, `kalshi`, `bet_history`, `wunderground.hourly`.
- Produces (each gains a leading `station: str = config.DEFAULT_STATION` arg so `@st.cache_data` keys on it):
  - `load_snapshot_kalshi(station)` → `calibration.get(refresh=True, station=station)` + `model.snapshot(..., station=station)`.
  - `load_accuracy_kalshi(station)` → `backtest.run(cli=True, ...)` (Dallas-only backtest stays; see note), `scoring.score(basis="cli", station=station)`, `scoring.market_accuracy(station=station)`.
  - `load_recap(station)`, `load_cli_report(station)`, `load_portfolio_value(station)`, `load_hourly(station)` — thread station through their bodies.

- [ ] **Step 1: Add `import config` + `import city_view` to app.py; add the `station` arg to each loader**

Change each `@st.cache_data`-decorated loader signature to take `station: str = config.DEFAULT_STATION` (first arg) and thread it through every call inside per the Interfaces block. Example:

```python
@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot_kalshi(station: str = config.DEFAULT_STATION):
    calib = calibration.get(refresh=True, station=station)
    snap = model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"),
                          continuous_obs=True, include_candidate=True, station=station)
    return snap, calib
```

For `load_cli_report(station)`: `nws_cli.fetch_latest_cli(ttl=300, station=station)` and `settlement.climate_day_of(now, station)`. For `load_hourly(station)`: `wunderground.hourly(station=station), wunderground.pws_current(station=station)`.

**Note:** `backtest.run(...)` is not yet station-aware (out of scope — backtest is the immediate-history estimate; Austin's live scoring comes from `scoring.score` which IS threaded). Leave the `backtest.run` call KDFW for now and add a `# TODO(plan3b): station-aware backtest` comment; the live self-scoring half already reflects the station.

- [ ] **Step 2: Verify the app imports and the suite is green**

Run: `python -c "import app" 2>&1 | tail -5 || true` (Streamlit may warn about bare-mode `run()`; an ImportError is the failure signal). Then `python -m pytest -q`.
Expected: no ImportError; full suite unchanged count.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: station-parameterize live cached loaders (cache keys on station)"
```

---

### Task 3: Forecast page — 2-way city toggle + dynamic title

**Files:**
- Modify: `app.py` (`kalshi_page`, `_page`), `market_view.py`
- Test: `tests/test_city_view.py` (extend — market_view title helper)

**Interfaces:**
- Consumes: `city_view.city_control`, `city_view.display_name`, the station-aware `load_snapshot_kalshi`.
- Produces: `market_view.page_title(snap) -> str` = `f"{config.station(snap.get('station','KDFW')).name} Daily High & Low"`; `render_page` uses it in place of the literal title.

- [ ] **Step 1: Write the failing test**

```python
def test_market_view_title_from_snapshot():
    import sys
    from unittest.mock import MagicMock
    for m in ("streamlit", "streamlit.components", "streamlit.components.v1",
              "streamlit_autorefresh"):
        sys.modules.setdefault(m, MagicMock())
    import market_view
    assert market_view.page_title({"station": "KAUS"}) == "Austin Daily High & Low"
    assert market_view.page_title({"station": "KDFW"}) == "Dallas Daily High & Low"
    assert market_view.page_title({}) == "Dallas Daily High & Low"   # default
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_city_view.py::test_market_view_title_from_snapshot -q`
Expected: FAIL (`page_title` undefined).

- [ ] **Step 3: Implement**

In `market_view.py`, add:
```python
import config

def page_title(snap: dict) -> str:
    return f"{config.station(snap.get('station', config.DEFAULT_STATION)).name} Daily High & Low"
```
Replace `st.title("Dallas Daily High & Low")` (line ~1848) with `st.title(page_title(snap))`. Grep the other ~2 "Dallas" literals in `market_view.py`; replace user-facing ones with `config.station(snap.get("station", config.DEFAULT_STATION)).name`.

In `app.py`, make `kalshi_page` pick the city and thread it:
```python
def kalshi_page():
    station = city_view.city_control("forecast", arity=2)
    _page(KALSHI, lambda: load_snapshot_kalshi(station),
          lambda: load_accuracy_kalshi(station), "cli", station)
```
Update `_page(adapter, snapshot_loader, accuracy_loader, record_basis, station=config.DEFAULT_STATION)`: attach the market with `kalshi.implied_block(..., station=station)`; `forecast_log.record`/`consensus_log.record` already auto-route by the snapshot's station tag (leave as-is); pass `station` to `load_portfolio_value(station)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_city_view.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `python -m pytest -q`
Expected: PASS — unchanged count (Dallas default preserves the exact title).

- [ ] **Step 6: Commit**

```bash
git add app.py market_view.py tests/test_city_view.py
git commit -m "feat: Forecast page city toggle + snapshot-derived title"
```

---

### Task 4: Hourly page — 2-way city toggle + station threading

**Files:**
- Modify: `app.py` (`hourly_page`), `hourly_view.py`
- Test: `tests/test_hourly_view.py` (extend if a title/name helper is added)

**Interfaces:**
- Consumes: `city_view.city_control`, station-aware `load_hourly`, `wunderground` (Plan 1: `hourly(station)`, `pws_current(station)` returns None for non-KDFW).
- Produces: `hourly_view.render(load_hourly, cli_report=None, station=config.DEFAULT_STATION)` — title `f"{config.station(station).name} Hourly"`; the Dallas/DFW display literals derive from the station name; when `pws_current` is None (Austin), the "live PWS" line is omitted with a short note instead of showing Euless data.

- [ ] **Step 1: Write the failing test**

```python
# in tests/test_hourly_view.py (streamlit already mocked there)
def test_hourly_render_accepts_station(monkeypatch):
    import hourly_view
    # render must accept a station and not raise with empty data
    hourly_view.render(lambda: ([], None), cli_report=None, station="KAUS")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_hourly_view.py::test_hourly_render_accepts_station -q`
Expected: FAIL (`render() got an unexpected keyword argument 'station'`).

- [ ] **Step 3: Implement**

Add `station: str = config.DEFAULT_STATION` to `hourly_view.render`; replace the `st.title("Hourly")`/"Dallas"/"DFW" display literals with the station name (keep the TWC/Wunderground attribution generic). Guard the PWS block: `if pws is None: <note "No live PWS for this station yet">`. In `app.py`:
```python
def hourly_page():
    station = city_view.city_control("hourly", arity=2)
    hourly_view.render(lambda: load_hourly(station),
                       cli_report=load_cli_report(station), station=station)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_hourly_view.py -q && python -m pytest -q`
Expected: PASS. Widen any hourly-view test mocks that now pass/expect `station` (behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add app.py hourly_view.py tests/test_hourly_view.py
git commit -m "feat: Hourly page city toggle + station-derived title/PWS guard"
```

---

### Task 5: Generic tab title + visual verification

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Generic browser-tab title**

Change `st.set_page_config(page_title="Dallas Daily High & Low", layout="wide")` to `page_title="Texas Daily High & Low"` (the per-page `st.title` still shows the active city). Commit:
```bash
git add app.py && git commit -m "polish: generic browser-tab title for the two-city app"
```

- [ ] **Step 2: Verify the running app for BOTH cities (REQUIRED SUB-SKILL: `verify`)**

Use the `verify` skill to launch the local dashboard headlessly and screenshot:
1. Forecast page, Dallas selected — confirm it matches today (title "Dallas Daily High & Low", data present).
2. Forecast page, Austin selected — title "Austin Daily High & Low", Austin probabilities render, no crash.
3. Hourly page, Austin selected — Austin hourly renders, PWS line gracefully omitted.
4. Mobile viewport (narrow) — the `st.segmented_control` and top-metric boxes remain usable (no horizontal page scroll).

Record the screenshots under `docs/benchmarks/2026-07-26-austin-ui/`. Fix any layout/label issues found (Title-Case, tooltips present, responsive) before finishing.

- [ ] **Step 3: Commit any verification fixes**

```bash
git add -A && git commit -m "fix: Austin UI verification adjustments (Forecast/Hourly, mobile)"
```

---

## Self-Review

**Spec coverage (spec §Architecture-UI, live pages):** Per-page in-page toggle + sticky session state → Task 1 (`city_control`/`resolve_selection`). Forecast 2-way → Task 3. Hourly 2-way → Task 4. Generic tab title → Task 5. Title-Case/mobile/tooltip constraint → Global Constraints + Task 5 verification. **Deferred (spec, Plan 3b):** History/Journal/Lab/Edge/Accuracy 3-way Both; Status/Trader both-at-once — explicitly out of scope here and unchanged.

**Placeholder scan:** The one `# TODO(plan3b): station-aware backtest` is a scoped, intentional deferral (backtest is the immediate-history estimate; live scoring is already station-aware), not a gap in a shipped path. No other TBD/TODO.

**Type consistency:** `station: str = config.DEFAULT_STATION` is the uniform loader/param signature. `city_view.codes_for`/`display_name`/`resolve_selection`/`city_control` defined in Task 1 and consumed in Tasks 3–4. `market_view.page_title(snap)` defined + tested in Task 3. `hourly_view.render(..., station=...)` defined + tested in Task 4.

**Risk:** Streamlit rendering isn't unit-testable, so Task 5's `verify`-skill screenshots are the real acceptance gate for the visual/mobile/Title-Case requirements — not optional.

## Follow-on

- **Plan 3b — analytics Both + ops both-at-once:** History/Journal/Lab/Edge/Accuracy get the 3-way `Dallas|Austin|Both` control (city-tagged combined rows, default Both) and their loaders parameterized; Status renders both pipelines side by side; Trader gets the combined safety summary + per-city edit toggle. Same Title-Case/mobile/tooltip constraint.
- **Plan 4 — Austin autonomous trader.**
