# Austin — Plan 4: Austin Autonomous Trader + Trader Page Both-at-Once Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a second, fully independent autonomous trader for Austin alongside Dallas — own kill switch, mode, daily-loss cap, params, state, and position management — and make the Trader page a both-at-once safety summary plus a per-city edit toggle. Ships **DISABLED + shadow**, exactly like Dallas did.

**Architecture:** The trader is already `Deps`-injected, so per-station support means binding a station-scoped `Deps` in `_real_deps(station)` and looping `config.STATION_CODES` in `main()`. Each station's trade-state document (params + runtime + log) is a separate namespaced file on the `trade-data` branch, so params, kill switch, mode, and the daily-loss halt are independent. Position management is filtered by a config-driven ticker→station classifier so the Austin trader never touches Dallas positions and vice-versa. `run_once` itself stays station-agnostic (all IO flows through `Deps`), so the existing fake-Deps unit tests keep passing.

**Tech Stack:** Python 3.9, GitHub Actions (`trade.yml`), Kalshi trade API (read + write), the `trade-data` branch, ntfy.

## SAFETY — read first (money-touching)

- **Ships DISABLED + shadow, per station.** `trade_params.DEFAULT_PARAMS` is `kill_switch=True, mode="shadow"`. An absent Austin trade-state doc → `merge_params(None)` → those defaults. So Austin cannot place a real order until a human explicitly sets its kill switch off AND mode live on the Trader page. **No task changes DEFAULT_PARAMS.**
- **Position isolation is a safety invariant, not a nicety.** `kalshi_portfolio.positions()` returns the whole account. Each station's trader MUST filter to its own Kalshi series (Task 1's classifier) — otherwise the Austin trader could read a Dallas position as "held truth" and try to manage/exit it. Task 3 enforces and tests this.
- **Independence.** Kill switch, mode, `daily_loss_cap`, and the per-day loss halt are per-station (separate state/runtime docs). One city halting or going live must never affect the other.
- **Dallas byte-identical.** With Dallas the default station, every existing trader path (state paths, deps, run_once, cron) behaves exactly as today; the full trader test suite passes unchanged.
- **No live activation in this plan.** This plan wires and ships-safe. Turning Austin live is a later, deliberate human action after shadow validation (same as the Dallas rollout).

## Global Constraints

- Python 3.9; `from __future__ import annotations` where used.
- Per-station files on the `trade-data` branch: KDFW keeps the bare names (`trade_state.json`, `trade_runtime.json`, `trade_log.jsonl`); other stations suffix the stem (`trade_state.KAUS.json`, …). KDFW paths stay byte-identical.
- The trader loop and Trader page reuse Plan 2's station-aware `kalshi.series_for`/`fetch_contracts`/`implied_forecast` and Plan 3's `city_view`.

---

## File Structure

- `sources/kalshi.py` — **modify.** Add `station_of_ticker(ticker)` (+ reuse for `variable_of`).
- `sources/kalshi_portfolio.py` — **modify.** Generalize `variable_of` to both cities via config series.
- `trade_state.py` — **modify.** `station`-namespaced paths through load/save/runtime/append.
- `trader.py` — **modify.** `_real_deps(station)` (station-bound + position filter); `run_once(..., station=…)`; `main()` loops stations.
- `trade_view.py` — **modify.** Both-at-once safety summary + per-city edit toggle.
- `.github/workflows/trade.yml` — **modify.** Comment/window note (Texas hours; loops both cities).
- `tests/test_station_trader.py` — **create.**

---

### Task 1: Config-driven ticker classifiers (station + variable)

**Files:**
- Modify: `sources/kalshi.py`, `sources/kalshi_portfolio.py`
- Test: `tests/test_station_trader.py` (create)

**Interfaces:**
- Produces:
  - `kalshi.station_of_ticker(ticker: str) -> str | None` — the station code whose `kalshi_high_series`/`kalshi_low_series` prefixes `ticker`, else None.
  - `kalshi.variable_of_ticker(ticker: str) -> str | None` — "high"/"low" from the matching series, else None.
  - `kalshi_portfolio.variable_of` re-implemented to call `kalshi.variable_of_ticker` (handles both cities; KDFW results unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_trader.py
from sources import kalshi, kalshi_portfolio


def test_station_of_ticker():
    assert kalshi.station_of_ticker("KXHIGHAUS-26JUL27-T96") == "KAUS"
    assert kalshi.station_of_ticker("KXLOWTAUS-26JUL27-T77") == "KAUS"
    assert kalshi.station_of_ticker("KXHIGHTDAL-26JUL27-B99") == "KDFW"
    assert kalshi.station_of_ticker("KXLOWTDAL-26JUL27-B79") == "KDFW"
    assert kalshi.station_of_ticker("KXNADA-1") is None


def test_variable_of_ticker_both_cities():
    assert kalshi.variable_of_ticker("KXHIGHAUS-26JUL27-T96") == "high"
    assert kalshi.variable_of_ticker("KXLOWTAUS-26JUL27-T77") == "low"
    # kalshi_portfolio.variable_of now delegates and handles Austin too
    assert kalshi_portfolio.variable_of("KXLOWTAUS-26JUL27-T77") == "low"
    assert kalshi_portfolio.variable_of("KXHIGHTDAL-26JUL27-B99") == "high"  # unchanged
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_trader.py -q`
Expected: FAIL (`station_of_ticker` undefined; `variable_of` returns None for Austin).

- [ ] **Step 3: Implement**

In `sources/kalshi.py`:
```python
def station_of_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for code in config.STATION_CODES:
        s = config.station(code)
        if t.startswith(s.kalshi_high_series) or t.startswith(s.kalshi_low_series):
            return code
    return None


def variable_of_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for code in config.STATION_CODES:
        s = config.station(code)
        if t.startswith(s.kalshi_high_series):
            return "high"
        if t.startswith(s.kalshi_low_series):
            return "low"
    return None
```
In `sources/kalshi_portfolio.py`, replace the hardcoded `variable_of` body with `return kalshi.variable_of_ticker(ticker)` (import kalshi). **Watch the prefix-overlap order:** `KXHIGHAUS` is not a prefix of `KXHIGHTDAL` and vice-versa, so order is safe; the test locks it.

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_station_trader.py -q && python -m pytest -q`
Expected: PASS; unchanged existing count (KDFW classification identical).

- [ ] **Step 5: Commit**

```bash
git add sources/kalshi.py sources/kalshi_portfolio.py tests/test_station_trader.py
git commit -m "feat: config-driven ticker->station/variable classifiers (both cities)"
```

---

### Task 2: Per-station trade-state paths

**Files:**
- Modify: `trade_state.py`
- Test: `tests/test_station_trader.py` (extend)

**Interfaces:**
- Produces (append `station: str = config.DEFAULT_STATION`):
  - `load_state(transport=None, station=…)`, `save_state(params, transport=None, station=…)`, `load_runtime(...station)`, `save_runtime(...station)`, and a `station`-aware log path for `append_jsonl`.
  - A `_path(base: str, station: str) -> str` helper: KDFW → `base`; else insert `.<STATION>` before the extension (`trade_state.KAUS.json`, `trade_log.KAUS.jsonl`).

- [ ] **Step 1: Write the failing test**

```python
def test_trade_state_paths_by_station():
    import trade_state
    assert trade_state._path("trade_state.json", "KDFW") == "trade_state.json"
    assert trade_state._path("trade_state.json", "KAUS") == "trade_state.KAUS.json"
    assert trade_state._path("trade_log.jsonl", "KAUS") == "trade_log.KAUS.jsonl"


def test_load_state_defaults_ship_safe_for_absent_station():
    import trade_state

    class _T:  # transport that has no file for anyone
        def get(self, path): return None
        def put(self, path, text, sha): raise AssertionError("no write expected")

    p = trade_state.load_state(transport=_T(), station="KAUS")
    assert p["kill_switch"] is True and p["mode"] == "shadow"   # ships DISABLED
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_trader.py -k trade_state -q`
Expected: FAIL (`_path` undefined / `load_state` rejects `station`).

- [ ] **Step 3: Implement**

Add:
```python
import os
import config

def _path(base: str, station: str = config.DEFAULT_STATION) -> str:
    if station == config.DEFAULT_STATION:
        return base
    stem, ext = os.path.splitext(base)
    return f"{stem}.{station}{ext}"
```
Thread `station` through `load_state`/`save_state`/`load_runtime`/`save_runtime` (use `_path(STATE_PATH, station)` / `_path(RUNTIME_PATH, station)`), and give `append_jsonl` callers the station log path (`_path(LOG_PATH, station)`) — `append_jsonl(path, record, transport)` already takes an explicit path, so the station routing happens at the call site in `_real_deps` (Task 3).

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_station_trader.py -q && python -m pytest -q`
Expected: PASS. Existing `test_trade_state.py` calls (no station) resolve to KDFW bare paths — unchanged.

- [ ] **Step 5: Commit**

```bash
git add trade_state.py tests/test_station_trader.py
git commit -m "feat: per-station trade-state paths (KDFW bare, others suffixed)"
```

---

### Task 3: Per-station trader loop + position isolation

**Files:**
- Modify: `trader.py`
- Test: `tests/test_station_trader.py` (extend)

**Interfaces:**
- Consumes: Task 1 classifiers, Task 2 state paths, Plan 2 station-aware kalshi.
- Produces:
  - `run_once(now=None, *, deps, station=config.DEFAULT_STATION)` — unchanged except `settlement.climate_day_of(now, station)`. All other IO flows through `deps`, so behavior is identical for KDFW.
  - `_real_deps(station: str = config.DEFAULT_STATION)` — every callable is station-bound:
    - `snapshot=lambda: model.snapshot(calibration.get(refresh=True, station=station), station=station) or {}`
    - `fetch_contracts=lambda v, d: kalshi.fetch_contracts(v, d, station=station)`; same for `implied_forecast`.
    - `load_state=lambda: trade_state.load_state(station=station)`; `load_runtime`/`save_runtime` likewise.
    - `positions=lambda: [p for p in (kalshi_portfolio.positions() or []) if kalshi.station_of_ticker(p["ticker"]) == station]` — **the isolation filter.**
    - `append_log=lambda rec: trade_state.append_jsonl(trade_state._path(trade_state.LOG_PATH, station), rec)`
    - `balance`, `fetch_orderbook`, `place_order`, `notify` unchanged (account-wide / ticker-scoped).
  - `main()` loops `config.STATION_CODES`, isolating failures:
    ```python
    def main() -> None:
        from sources.common import TZ
        now = datetime.now(TZ)
        for code in config.STATION_CODES:
            try:
                out = run_once(now=now, deps=_real_deps(code), station=code)
                print(f"[{code}] trader run: {out}")
            except Exception as e:
                print(f"[{code}] trader run failed: {e}")
    ```

- [ ] **Step 1: Write the failing test**

```python
def test_real_deps_positions_are_station_isolated(monkeypatch):
    import trader
    from sources import kalshi_portfolio
    monkeypatch.setattr(kalshi_portfolio, "positions", lambda: [
        {"ticker": "KXHIGHTDAL-26JUL27-B99"}, {"ticker": "KXHIGHAUS-26JUL27-T96"}])
    aus = trader._real_deps("KAUS").positions()
    assert [p["ticker"] for p in aus] == ["KXHIGHAUS-26JUL27-T96"]
    dfw = trader._real_deps("KDFW").positions()
    assert [p["ticker"] for p in dfw] == ["KXHIGHTDAL-26JUL27-B99"]


def test_main_runs_every_station(monkeypatch):
    import trader
    seen = []
    monkeypatch.setattr(trader, "_real_deps", lambda code: code)
    monkeypatch.setattr(trader, "run_once",
                        lambda now=None, *, deps, station: seen.append(station) or {})
    trader.main()
    import config
    assert seen == config.STATION_CODES
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_station_trader.py -k "isolated or main_runs" -q`
Expected: FAIL (`_real_deps` rejects a station arg / `run_once` rejects `station`).

- [ ] **Step 3: Implement** the interfaces above. Keep `run_once`'s only station use to `settlement.climate_day_of(now, station)`.

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_station_trader.py tests/test_trader.py -q && python -m pytest -q`
Expected: PASS. `test_trader.py`'s fake-`Deps` calls (`run_once(now=NOON, deps=d)`) default `station="KDFW"` → identical behavior.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_station_trader.py
git commit -m "feat: per-station trader loop + position isolation (ships safe)"
```

---

### Task 4: Trader page — both-at-once safety summary + per-city editor

**Files:**
- Modify: `trade_view.py`
- Test: `tests/test_trade_view.py` (extend)

**Interfaces:**
- Consumes: `trade_state.load_state(station=…)`, Plan 3 `city_view.city_control`.
- Produces:
  - `trade_view.safety_rows() -> list[dict]` — one `{station, name, kill_switch, mode, halted}` per station (pure-ish; reads each station's state), for the top summary.
  - `trade_view.render()` — draws the combined safety summary first (both cities: kill switch, mode, and whether armed), then a `Dallas | Austin` edit toggle (`city_control("trader", 2)`) selecting which city's params/positions/log the editor below acts on. Every state read/write passes the selected `station`.

- [ ] **Step 1: Write the failing test**

```python
def test_safety_rows_reads_both_stations(monkeypatch):
    import sys
    from unittest.mock import MagicMock
    sys.modules.setdefault("streamlit", MagicMock())
    import trade_view, trade_state, config

    def fake_load(station=config.DEFAULT_STATION):
        return {"kill_switch": station == "KDFW", "mode": "shadow"}
    monkeypatch.setattr(trade_state, "load_state", fake_load)
    rows = trade_view.safety_rows()
    by = {r["station"]: r for r in rows}
    assert by["KDFW"]["kill_switch"] is True and by["KDFW"]["name"] == "Dallas"
    assert by["KAUS"]["kill_switch"] is False and by["KAUS"]["name"] == "Austin"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_trade_view.py::test_safety_rows_reads_both_stations -q`
Expected: FAIL (`safety_rows` undefined).

- [ ] **Step 3: Implement**

Add `safety_rows()`:
```python
def safety_rows():
    import config, trade_state
    out = []
    for code in config.STATION_CODES:
        p = trade_state.load_state(station=code)
        out.append({"station": code, "name": config.station(code).name,
                    "kill_switch": p["kill_switch"], "mode": p["mode"],
                    "halted": p["kill_switch"] or p["mode"] == "shadow"})
    return out
```
In `render()`, draw the summary (a small table/badges of `safety_rows()` — Title-Case, a red/green dot per city via `market_view.metric_card`'s `dot`), then `station = city_view.city_control("trader", arity=2)`, and thread `station` through the existing `load_state`/`save_state`/positions/log calls (`_render_positions`/`_render_log` gain a `station` arg; positions filter via `kalshi.station_of_ticker`). The "Save Settings" writes `trade_state.save_state(..., station=station)`.

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_trade_view.py -q && python -m pytest -q`
Expected: PASS. Update any single-station trade-view test to the new signature.

- [ ] **Step 5: Commit**

```bash
git add trade_view.py tests/test_trade_view.py
git commit -m "feat: Trader page both-at-once safety summary + per-city editor"
```

---

### Task 5: Cron note + trade-data branch readiness

**Files:**
- Modify: `.github/workflows/trade.yml`

- [ ] **Step 1: Update the workflow comment**

The run command (`python trader.py`) is unchanged — `main()` now loops both cities on the `trade-data` branch via the contents API (no checkout of per-station files needed). Update the header comment: the cron window covers **Texas** market hours (both KDFW and KAUS are Central), and the loop trades every configured station, each gated by its own kill switch/mode. Keep the ONE-TIME SETUP block; add a line: "Austin ships kill-switched + shadow; its state doc (`trade_state.KAUS.json`) is created on the first Save from the Trader page or the first shadow run."

- [ ] **Step 2: Validate YAML + commit**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/trade.yml')); print('YAML OK')"`
Expected: `YAML OK`.
```bash
git add .github/workflows/trade.yml
git commit -m "ci: trade loop covers both cities (comment/window note; run unchanged)"
```

---

### Task 6: Safety verification (ships-safe, isolated, page renders)

**Files:**
- Test: `tests/test_station_trader.py` (extend) + `verify` skill.

- [ ] **Step 1: Assert Austin cannot trade at ships-safe defaults**

```python
def test_kaus_run_once_no_ops_at_default_kill_switch():
    import trader, config
    from datetime import datetime
    from types import SimpleNamespace
    placed = []
    deps = SimpleNamespace(
        load_state=lambda: __import__("trade_params").DEFAULT_PARAMS.copy(),
        load_runtime=lambda: {}, save_runtime=lambda r: None,
        snapshot=lambda: {}, balance=lambda: 100.0, positions=lambda: [],
        fetch_contracts=lambda v, d: [], fetch_orderbook=lambda t: {},
        implied_forecast=lambda v, d: None,
        place_order=lambda **k: placed.append(k), append_log=lambda r: None,
        notify=lambda *a, **k: True)
    out = trader.run_once(now=datetime(2026, 7, 27, 12, 0), deps=deps, station="KAUS")
    assert out == {"halted": "kill_switch"} and placed == []
```

- [ ] **Step 2: Run the safety test + full trader suite**

Run: `python -m pytest tests/test_station_trader.py tests/test_trader.py tests/test_trade_state.py tests/test_trade_view.py -q && python -m pytest -q`
Expected: PASS. Record the final count.

- [ ] **Step 3: Visual-verify the Trader page (`verify` skill)**

Launch the dashboard headlessly and screenshot `/trader_page`: the both-at-once safety summary shows **both** cities (Dallas + Austin, both kill-switched/shadow), the `Dallas | Austin` edit toggle renders, and switching to Austin shows Austin's (default) params. Confirm Title-Case + mobile (390px, no h-scroll). Record under `docs/benchmarks/2026-07-26-austin-trader/`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_station_trader.py docs/benchmarks/2026-07-26-austin-trader/
git commit -m "test: Austin trader ships-safe + isolation + Trader-page verification"
```

---

## Self-Review

**Spec coverage (spec §Architecture-backend trader, §UI Trader):** Second independent per-station trader (own kill switch/mode/loss-cap/state) → Tasks 2–3. Position isolation → Task 3 (the `positions` filter, tested). Trader page combined safety summary + per-city edit toggle → Task 4. Ships DISABLED + shadow → guaranteed by unchanged `DEFAULT_PARAMS` + Task 2/6 tests. Cron → Task 5.

**Placeholder scan:** No TBD/TODO. Every task ships a tested, working path.

**Type consistency:** `station: str = config.DEFAULT_STATION` uniform across `trade_state`, `_real_deps`, `run_once`. `kalshi.station_of_ticker`/`variable_of_ticker` defined Task 1, consumed in Tasks 3–4 and `kalshi_portfolio`. `trade_state._path` defined Task 2, used in Tasks 2–3. `trade_view.safety_rows` defined + tested Task 4.

**Safety review:** DEFAULT_PARAMS untouched (ships kill-switched/shadow); absent-doc load returns those defaults (Task 2 test); a default-params `run_once` for KAUS halts on `kill_switch` and places nothing (Task 6 test); positions are station-filtered so no cross-city management (Task 3 test); per-station state/runtime/log docs keep kill switch, mode, and the loss halt independent. No task activates live trading.

## Follow-on

- **Post-merge, human, deliberate:** shadow-validate Austin for a stretch, watch its `trade_log.KAUS.jsonl`, then (only when satisfied) set Austin's kill switch off + mode live on the Trader page — mirroring the Dallas rollout. Not part of this plan.
- With Plan 4 merged, **every dashboard surface is two-city** (Forecast, Hourly, and — after Plan 3b — the analytics pages; Status; Trader).
