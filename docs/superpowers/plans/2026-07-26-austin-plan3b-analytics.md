# Austin — Plan 3b: Analytics 3-Way "Both" + Status Both-at-Once Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the retrospective analytics pages — Journal, Lab, Edge, Accuracy, History — two-city aware with a 3-way `Dallas | Austin | Both` control (default Both), and make Status show both cities' pipeline health at once. Completes the two-city UI so every page respects the city selection.

**Architecture:** Reuses Plan 3's `city_view`. A shared chrome/loop pattern moves each analytics page's title + theme controls into its `app.py` page function (drawn once), so the view's `render` becomes **body-only** and takes a `station`. "Both" renders the two stations' bodies stacked under city subheaders — simple and uniform, honest for the current cold-start where Austin's retrospective data is still empty. History (a single portfolio table) instead filters rows by the city's Kalshi series. Status gathers both stations' log health and renders two labeled sections.

**Tech Stack:** Streamlit 1.50, Python 3.9, the dark-serif dashboard.

## Reality note (read before scheduling execution)

Austin began logging on 2026-07-26 (Plan 2). Until it accrues history it has **0 settled days, 0 calibration-history rows, 0 bets, 0 edge rows**, so every analytics page will show its existing "Accumulating — no data yet" state for Austin for ~1–2 weeks. This plan is therefore about **UI completeness** (the toggle works everywhere, Austin data appears as it lands), not new insight today. It is safe to build now or to defer until Austin has data — the code is identical either way.

## Global Constraints

- **Dallas byte-identical by default.** With Dallas selected, every analytics page renders exactly as today.
- **STANDING UI CONSTRAINT (user):** everything new is **Title-Cased, mobile-friendly, tooltip'd**, consistent with the dark-serif theme.
- **`_theme_controls()` MUST be called exactly once per page render** — it registers a sidebar expander with a fixed key, so calling it twice (e.g. once per station in a Both view) raises a duplicate-key error. Hence the chrome moves to the page function.
- **Cache correctness:** every station-aware `@st.cache_data` loader takes `station` so it keys on it.
- **Deferred to Plan 4 (NOT here):** the Trader page both-at-once — it needs the Austin autonomous trader, which Plan 4 builds. Trader stays Dallas-only this plan.

---

## File Structure

- `city_view.py` — **modify.** Add `city_sections(page_key, arity=3)` helper.
- `app.py` — **modify.** Parameterize `load_journal`/`load_lab`/`load_status`/`load_calibration_history` on station; the analytics page functions own chrome (theme + title + control) and loop stations.
- `journal_view.py`, `lab_view.py`, `edge_view.py`, `accuracy_view.py`, `status_view.py`, `bet_view.py` — **modify.** `render` becomes body-only + `station`-aware.
- `tests/test_city_view.py`, `tests/test_station_analytics.py` — **create/extend.**

---

### Task 1: `city_sections` helper + parameterize the remaining loaders

**Files:**
- Modify: `city_view.py`, `app.py`
- Test: `tests/test_city_view.py` (extend), `tests/test_station_analytics.py` (create)

**Interfaces:**
- Produces:
  - `city_view.city_sections(page_key: str, arity: int = 3) -> tuple[str, list[str]]` — renders the control and returns `(selection, codes)` where `codes = codes_for(selection)`. The page function loops `codes`, drawing `st.subheader(display_name(code))` only when `selection == "Both"`.
  - `load_journal(station=config.DEFAULT_STATION)`, `load_lab(station=...)`, `load_status(station=...)`, `load_calibration_history(station=...)` — thread station through `settlements.as_map("cli", station=...)`, `forecast_log.load(station=...)`, `consensus_log.load(station=...)`, `betting_log.load(station=...)`, `calibration_history` (see note), and `bet_history` (KDFW-only; leave account-wide).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_analytics.py
import sys
from unittest.mock import MagicMock
for m in ("streamlit", "streamlit.components", "streamlit.components.v1",
          "streamlit_autorefresh"):
    sys.modules.setdefault(m, MagicMock())

import city_view


def test_city_sections_returns_selection_and_codes(monkeypatch):
    monkeypatch.setattr(city_view, "city_control", lambda page_key, arity=3: "Both")
    sel, codes = city_view.city_sections("journal", 3)
    assert sel == "Both"
    assert codes == ["KDFW", "KAUS"]
    monkeypatch.setattr(city_view, "city_control", lambda page_key, arity=3: "Austin")
    sel, codes = city_view.city_sections("journal", 3)
    assert sel == "Austin" and codes == ["KAUS"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_analytics.py -q`
Expected: FAIL (`city_sections` undefined).

- [ ] **Step 3: Implement `city_sections`**

```python
def city_sections(page_key: str, arity: int = 3):
    sel = city_control(page_key, arity)
    if arity == 2:
        return sel, [sel]                 # sel is already a code for 2-way
    return sel, codes_for(sel)
```

- [ ] **Step 4: Parameterize the loaders in `app.py`**

Add `station: str = config.DEFAULT_STATION` to `load_journal`, `load_lab`, `load_status`, `load_calibration_history`; thread it through their data sources. Examples:
```python
@st.cache_data(ttl=3600, show_spinner=False)
def load_journal(station: str = config.DEFAULT_STATION):
    from datetime import date
    import settlements
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)   # account-wide
    except Exception:
        bet_rows = None
    return journal_view.assemble(date.today(), settlements.as_map("cli", station=station),
                                 forecast_log.load(station=station), bet_rows)

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_lab(station: str = config.DEFAULT_STATION):
    import settlements
    rows = forecast_log.load(station=station)
    settled = settlements.as_map("cli", station=station)
    return (lab_view.head_to_head(rows, settled), lab_view.per_model_scores(rows, settled))
```
`load_status(station)`: thread station into the `consensus_log.load`, `forecast_log.load`, `betting_log.load`, `settlements.load`, `calibration.get(refresh=True, station=station)` calls inside. **Note:** `calibration_history` is not yet per-station on disk; `load_calibration_history(station)` accepts the arg but keeps reading the shared file for now — add `# TODO: per-station calibration_history` (a KDFW-only drift sparkline is acceptable until Austin has recompute history).

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest tests/test_station_analytics.py tests/test_city_view.py -q && python -m pytest -q`
Expected: PASS; unchanged existing count.

- [ ] **Step 6: Commit**

```bash
git add city_view.py app.py tests/test_station_analytics.py tests/test_city_view.py
git commit -m "feat: city_sections helper + station-parameterize analytics loaders"
```

---

### Task 2: Journal — 3-way Both (body-only render, stacked city sections)

**Files:**
- Modify: `journal_view.py`, `app.py`
- Test: `tests/test_journal_view.py` (extend)

**Interfaces:**
- Produces: `journal_view.render(journal_loader, station=config.DEFAULT_STATION)` draws only the body (no `st.title`, no `_theme_controls`); the page function owns chrome.

- [ ] **Step 1: Write the failing test**

```python
# in tests/test_journal_view.py (streamlit mocked there)
def test_render_body_accepts_station():
    import journal_view
    journal_view.render(lambda: [], station="KAUS")   # empty journal, must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_journal_view.py::test_render_body_accepts_station -q`
Expected: FAIL (`render()` rejects `station`).

- [ ] **Step 3: Implement**

In `journal_view.render`, remove the `market_view._theme_controls()` and `st.title("Journal")` lines (moved to the page function) and add `station: str = config.DEFAULT_STATION` (used only if the body shows a per-city label). In `app.py`:
```python
def journal_page():
    market_view._theme_controls()
    st.title("Journal")
    sel, codes = city_view.city_sections("journal", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        journal_view.render(lambda code=code: load_journal(code), station=code)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_journal_view.py -q && python -m pytest -q`
Expected: PASS. Move any title/theme assertions in the journal test to the page-function level or drop them (chrome relocated).

- [ ] **Step 5: Commit**

```bash
git add journal_view.py app.py tests/test_journal_view.py
git commit -m "feat: Journal 3-way city sections (body-only render)"
```

---

### Task 3: Lab — 3-way Both

**Files:**
- Modify: `lab_view.py`, `app.py`
- Test: `tests/test_lab_view.py` (extend)

**Interfaces:**
- Produces: `lab_view.render(lab_loader, snap=None, station=config.DEFAULT_STATION)` — body-only.

- [ ] **Step 1: Write the failing test**

```python
def test_render_body_accepts_station():
    import lab_view
    lab_view.render(lambda: ({}, {}), snap=None, station="KAUS")   # empty, no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_lab_view.py::test_render_body_accepts_station -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Remove `_theme_controls()`/`st.title("Lab")` from `lab_view.render`; add `station`. In `app.py`:
```python
def lab_page():
    market_view._theme_controls()
    st.title("Lab")
    try:
        snap, _calib = load_snapshot_kalshi(config.DEFAULT_STATION)
    except Exception:
        snap = None
    sel, codes = city_view.city_sections("lab", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        s = None
        try:
            s, _ = load_snapshot_kalshi(code)
        except Exception:
            s = None
        lab_view.render(lambda code=code: load_lab(code), snap=s, station=code)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_lab_view.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lab_view.py app.py tests/test_lab_view.py
git commit -m "feat: Lab 3-way city sections (body-only render)"
```

---

### Task 4: Edge — 3-way Both (self-loading render)

**Files:**
- Modify: `edge_view.py`, `app.py`
- Test: `tests/test_edge_view.py` (extend)

**Interfaces:**
- Produces: `edge_view.render(station=config.DEFAULT_STATION)` — body-only; loads `betting_log.load(station=station)` and `settlements.as_map(..., station=station)`.

- [ ] **Step 1: Write the failing test**

```python
def test_render_accepts_station():
    import edge_view
    edge_view.render(station="KAUS")   # no settled Austin rows -> "Accumulating", no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_edge_view.py::test_render_accepts_station -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Remove `_theme_controls()`/`st.title("Edge")` from `edge_view.render`; add `station`; thread it into `betting_log.load(station=station)` and both `settlements.as_map(basis, station=station)` calls. In `app.py`:
```python
def edge_page():
    market_view._theme_controls()
    st.title("Edge")
    sel, codes = city_view.city_sections("edge", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        edge_view.render(station=code)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_edge_view.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add edge_view.py app.py tests/test_edge_view.py
git commit -m "feat: Edge 3-way city sections (station-threaded self-load)"
```

---

### Task 5: Accuracy — 3-way Both

**Files:**
- Modify: `accuracy_view.py`, `app.py`
- Test: `tests/test_accuracy_view.py` (extend)

**Interfaces:**
- Produces: `accuracy_view.render(load_accuracy, history_loader=None, station=config.DEFAULT_STATION)` — body-only.

- [ ] **Step 1: Write the failing test**

```python
def test_render_accepts_station():
    import accuracy_view
    accuracy_view.render(lambda: (None, None), history_loader=None, station="KAUS")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_accuracy_view.py::test_render_accepts_station -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Remove `_theme_controls()`/`st.title("Accuracy")` from `accuracy_view.render`; add `station`. In `app.py`:
```python
def accuracy_page():
    market_view._theme_controls()
    st.title("Accuracy")
    sel, codes = city_view.city_sections("accuracy", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        accuracy_view.render(lambda code=code: load_accuracy_kalshi(code),
                             lambda code=code: load_calibration_history(code), station=code)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_accuracy_view.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add accuracy_view.py app.py tests/test_accuracy_view.py
git commit -m "feat: Accuracy 3-way city sections (body-only render)"
```

---

### Task 6: History — 3-way city filter by Kalshi series

**Files:**
- Modify: `bet_view.py`, `app.py`
- Test: `tests/test_bet_view.py` (extend)

**Interfaces:**
- Produces: `bet_view.city_of_ticker(ticker: str) -> str | None` → the station code whose `kalshi_high_series`/`kalshi_low_series` prefixes `ticker`, else None; `bet_view.render(selection="Both")` — body-only; filters the portfolio rows to the selected city (or all for Both) and adds a City column when Both.

- [ ] **Step 1: Write the failing test**

```python
def test_city_of_ticker():
    import bet_view
    assert bet_view.city_of_ticker("KXHIGHAUS-26JUL27-T96") == "KAUS"
    assert bet_view.city_of_ticker("KXLOWTDAL-26JUL27-T79") == "KDFW"
    assert bet_view.city_of_ticker("KXNADA-99") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_bet_view.py::test_city_of_ticker -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `city_of_ticker` using `config.station(c).kalshi_high_series/.kalshi_low_series` for each `c in config.STATION_CODES` (match `ticker.startswith(series)`). Remove `_theme_controls()`/`st.title("History")` from `render`; add `selection="Both"`; after loading the portfolio rows, filter to `[r for r in rows if selection == "Both" or city_of_ticker(r["ticker"]) == city_view.codes_for(selection)[0]]` and, when Both, add a City column (`display_name(city_of_ticker(...))`) to the displayed table. In `app.py`:
```python
def history_page():
    market_view._theme_controls()
    st.title("History")
    sel, _codes = city_view.city_sections("history", arity=3)
    bet_view.render(selection=sel)
```
Register the page with `history_page` instead of `bet_view.render` in `st.navigation`.

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_bet_view.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bet_view.py app.py tests/test_bet_view.py
git commit -m "feat: History 3-way city filter by Kalshi series"
```

---

### Task 7: Status — both-at-once (two-column health)

**Files:**
- Modify: `status_view.py`, `app.py`
- Test: `tests/test_status_view.py` (extend)

**Interfaces:**
- Produces: `status_view.render(per_station: list[tuple[str, dict, dict]], snaps: dict)` — body-only; renders one labeled section per `(station_code, inputs, counts)`, so a stale Austin feed is never hidden. `checks(...)` stays pure and reused per station.

- [ ] **Step 1: Write the failing test**

```python
def test_render_both_sections(monkeypatch):
    import status_view
    per = [("KDFW", {}, {"Forecast Log": 10}), ("KAUS", {}, {"Forecast Log": 2})]
    status_view.render(per, snaps={})   # must not raise; both sections drawn
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_status_view.py::test_render_both_sections -q`
Expected: FAIL (signature mismatch).

- [ ] **Step 3: Implement**

Change `status_view.render` to take `per_station` + `snaps` (drop the single snap/inputs/counts triple; move `_theme_controls()`/`st.title` to the page function). Loop each station: `st.subheader(config.station(code).name)`, `merged = {**inputs, **snapshot_inputs(snaps.get(code))}`, `cards = checks(merged, now)`, render the card grid. In `app.py`:
```python
def status_page():
    market_view._theme_controls()
    st.title("Status")
    st.caption("Log-derived health for both cities …")
    per, snaps = [], {}
    for code in config.STATION_CODES:
        try:
            snaps[code], _ = load_snapshot_kalshi(code)
        except Exception:
            snaps[code] = None
        inputs, counts = load_status(code)
        per.append((code, inputs, counts))
    status_view.render(per, snaps)
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_status_view.py -q && python -m pytest -q`
Expected: PASS. Update the existing status-view render test to the new signature (the `checks()` threshold tests are unaffected — they call `checks` directly).

- [ ] **Step 5: Commit**

```bash
git add status_view.py app.py tests/test_status_view.py
git commit -m "feat: Status both-at-once (per-station health sections)"
```

---

### Task 8: Visual verification (both cities, all pages)

**Files:** none (verification).

- [ ] **Step 1: Verify via the `verify` skill**

Launch the local dashboard headlessly and screenshot, for each of Journal / Lab / Edge / Accuracy / History with `?city=Both` and `?city=Austin`, plus Status (both-at-once): the toggle renders, Dallas content is unchanged, Austin shows either its data or a clean "Accumulating" state, and Status shows both sections. Add a 390px mobile shot of one Both page (no horizontal scroll). Record under `docs/benchmarks/2026-07-26-austin-ui-analytics/`.

- [ ] **Step 2: Fix any layout/label issues; commit**

```bash
git add -A && git commit -m "fix: Plan 3b analytics UI verification adjustments"
```

---

## Self-Review

**Spec coverage (spec §UI per-page table):** History/Journal/Lab/Edge/Accuracy 3-way Both → Tasks 6/2/3/4/5. Status both-at-once → Task 7. `city_control` reuse + sticky → Plan 3 (done). Trader both-at-once → explicitly deferred to Plan 4 (needs the Austin trader).

**Placeholder scan:** The two `# TODO` markers (per-station `calibration_history`, station-aware `backtest`) are scoped, intentional deferrals of secondary drift/backtest visuals, not gaps in shipped paths — both degrade to a correct KDFW-only sparkline/estimate while the live per-station scoring is accurate. No other TBD/TODO.

**Type consistency:** `station: str = config.DEFAULT_STATION` uniform across loaders + view renders. `city_view.city_sections` returns `(selection, codes)` consumed identically in Tasks 2–7. `bet_view.city_of_ticker` defined + tested in Task 6. `status_view.render(per_station, snaps)` defined + tested in Task 7. Every page function calls `_theme_controls()` + `st.title` exactly once (the duplicate-key hazard the Global Constraints call out).

**Risk:** the chrome relocation (theme/title out of each view) is the one cross-cutting change; the mitigation is that each task moves exactly one page's chrome and re-runs the full suite. Visual acceptance is Task 8's `verify` screenshots.

## Follow-on

- **Plan 4 — Austin autonomous trader + the Trader-page both-at-once** (combined safety summary + per-city edit toggle). The only remaining two-city surface.
