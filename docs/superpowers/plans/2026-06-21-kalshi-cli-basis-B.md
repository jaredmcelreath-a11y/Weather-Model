# Kalshi CLI Settlement Basis — Part B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Kalshi accuracy panel (backtest + live self-scoring) on the NWS CLI settlement basis via a per-basis forward log, leaving Robinhood's hourly accuracy unchanged.

**Architecture:** The forward log gains a `basis` tag ("hourly"/"cli"); the scheduled Action and the dashboard log both bases. `scoring.score(basis=...)` and `backtest.run(cli=, settle_offset=)` grade against the matching truth (`fetch_actual` vs `fetch_actual_cli`). The Kalshi page uses a CLI accuracy loader; everything defaults to hourly so Robinhood is unchanged.

**Tech Stack:** Python 3.9, Streamlit, pytest. Spec: `docs/superpowers/specs/2026-06-21-kalshi-cli-basis-B-design.md`. Part A (offset, `fetch_actual_cli`, `settle_offset`) is already on main.

---

## File Structure
- **`forecast_log.py`** (modify) — `record(..., basis="hourly")`; `_key` includes basis.
- **`scoring.py`** (modify) — `score(..., basis="hourly")`; `_actuals_for(records, basis)`; `per_lead_sigma` pins hourly.
- **`backtest.py`** (modify) — `run(days=60, cli=False, settle_offset=None)`.
- **`scheduled_log.py`** (modify) — log both bases.
- **`app.py`** (modify) — `load_accuracy_kalshi`; `_page(adapter, snapshot_loader, accuracy_loader, record_basis)`; page funcs.
- **`market_view.py`** (modify) — CLI caption in the accuracy expander (Kalshi only).
- **Tests:** extend `tests/test_cli_basis.py`.

Hourly defaults reproduce today's behavior exactly → Robinhood unchanged.

---

## Task 1: Forward-log basis dimension

**Files:**
- Modify: `forecast_log.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli_basis.py`:

```python
import json

import forecast_log


def _snap():
    return {
        "updated": "2026-06-20T15:00:00",
        "today": {
            "day": "2026-06-20",
            "high": {"consensus": 91, "probabilities": {"90": 0.5, "91": 0.5}},
            "low": {"consensus": 75, "probabilities": {"74": 0.5, "75": 0.5}},
        },
        "tomorrow": {
            "day": "2026-06-21",
            "high": {"consensus": 93, "probabilities": {"92": 0.5, "93": 0.5}},
            "low": {"consensus": 77, "probabilities": {"76": 0.5, "77": 0.5}},
        },
    }


def test_hourly_and_cli_records_coexist(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snap(), path=p, basis="hourly")
    forecast_log.record(_snap(), path=p, basis="cli")
    rows = forecast_log.load(p)
    assert {r["basis"] for r in rows} == {"hourly", "cli"}
    # today+tomorrow x high+low = 4 records per basis, kept separately
    assert len([r for r in rows if r["basis"] == "hourly"]) == 4
    assert len([r for r in rows if r["basis"] == "cli"]) == 4


def test_legacy_untagged_record_treated_as_hourly(tmp_path):
    p = str(tmp_path / "log.jsonl")
    legacy = {"target_date": "2026-06-20", "variable": "high", "lead_bucket": 0,
              "captured_at": "x", "consensus": 91, "probabilities": {"91": 1.0}}
    with open(p, "w") as fh:
        fh.write(json.dumps(legacy) + "\n")
    forecast_log.record(_snap(), path=p, basis="hourly")  # should UPSERT the legacy row
    rows = forecast_log.load(p)
    match = [r for r in rows
             if r["target_date"] == "2026-06-20" and r["variable"] == "high"
             and r["lead_bucket"] == 0 and r.get("basis", "hourly") == "hourly"]
    assert len(match) == 1  # legacy row upserted in place, not duplicated
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -k "coexist or legacy" -q`
Expected: FAIL — `record()` got an unexpected keyword argument `basis` (and/or records lack a `basis` key).

- [ ] **Step 3: Implement in `forecast_log.py`**

Change `_key`:
```python
def _key(rec: dict) -> tuple:
    return (rec["target_date"], rec["variable"], rec["lead_bucket"],
            rec.get("basis", "hourly"))
```

Change the `record` signature line `def record(snapshot: dict, path: str | None = None) -> None:` to:
```python
def record(snapshot: dict, path: str | None = None, basis: str = "hourly") -> None:
```

In `record`, add `"basis": basis,` to the dict appended to `new_recs` (alongside the existing fields like `"target_date"`, `"variable"`, `"lead_bucket"`, `"captured_at"`, `"consensus"`, `"probabilities"`):
```python
            new_recs.append({
                "target_date": pred["day"],
                "variable": variable,
                "lead_bucket": bucket,
                "basis": basis,
                "captured_at": captured,
                "consensus": d.get("consensus"),
                "probabilities": d["probabilities"],
            })
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add forecast_log.py tests/test_cli_basis.py
git commit -m "forecast_log: per-basis records (hourly/cli)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Basis-aware scoring

**Files:**
- Modify: `scoring.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli_basis.py`:

```python
import scoring
from sources import station_history


def test_score_filters_by_basis_and_uses_matching_truth(monkeypatch):
    recs = [
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "hourly", "consensus": 90, "probabilities": {"90": 1.0}},
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "cli", "consensus": 91, "probabilities": {"91": 1.0}},
    ]
    monkeypatch.setattr(scoring.forecast_log, "load", lambda path=None: recs)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 10): (90.0, 70.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {date(2026, 6, 10): (91.0, 70.0)})
    today = date(2026, 6, 11)
    h = scoring.score(today=today, basis="hourly")
    c = scoring.score(today=today, basis="cli")
    assert h["n_settled"] == 1 and c["n_settled"] == 1
    # hourly probs(90)=1 vs hourly truth 90 -> perfect; cli probs(91)=1 vs cli 91 -> perfect
    assert h["by_variable"]["high"]["brier"] == 0.0
    assert c["by_variable"]["high"]["brier"] == 0.0
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py::test_score_filters_by_basis_and_uses_matching_truth -q`
Expected: FAIL — `score()` got an unexpected keyword argument `basis`.

- [ ] **Step 3: Implement in `scoring.py`**

Change `_actuals_for`:
```python
def _actuals_for(records: list[dict], basis: str = "hourly") -> dict[date, tuple[float, float]]:
    if not records:
        return {}
    days = [date.fromisoformat(r["target_date"]) for r in records]
    fetch = (station_history.fetch_actual_cli if basis == "cli"
             else station_history.fetch_actual)
    return fetch(min(days), max(days))
```

Change the `score` signature `def score(today: date | None = None) -> dict:` to:
```python
def score(today: date | None = None, basis: str = "hourly") -> dict:
```
and change its first two body lines (`records = _settled_records(today)` and `actual = _actuals_for(records)`) to:
```python
    records = [r for r in _settled_records(today)
               if r.get("basis", "hourly") == basis]
```
(keep the `empty = {...}` / `if not records: return empty` lines as-is), and:
```python
    actual = _actuals_for(records, basis)
```

In `per_lead_sigma`, change the loop header `for bucket, vars_ in score(today).get("by_lead", {}).items():` to:
```python
    for bucket, vars_ in score(today, basis="hourly").get("by_lead", {}).items():
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scoring.py tests/test_cli_basis.py
git commit -m "scoring: grade per basis (hourly/cli) against matching truth

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: CLI-basis backtest

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli_basis.py`:

```python
import backtest
import calibration
from sources import open_meteo_models


def test_backtest_cli_uses_cli_truth_and_applies_offset(monkeypatch):
    day = date(2026, 6, 10)
    series = {"det_a": _member(day, 90.0)}  # daily high 90
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: series)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {day: (91.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    hourly = backtest.run()                                   # vs hourly truth 90
    cli_off = backtest.run(cli=True, settle_offset={"high": 1.0, "low": 0.0})
    cli_no = backtest.run(cli=True)                           # cli truth, no offset

    assert hourly["high"]["mae"] == 0.0          # consensus 90 vs 90
    assert cli_off["high"]["mae"] == 0.0         # consensus 90+1=91 vs cli 91
    assert cli_no["high"]["mae"] == 1.0          # consensus 90 vs cli 91 -> off by 1
```

(`_member` and `station_history` are already imported earlier in this test file.)

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py::test_backtest_cli_uses_cli_truth_and_applies_offset -q`
Expected: FAIL — `run()` got an unexpected keyword argument `cli`.

- [ ] **Step 3: Implement in `backtest.py`**

Change the `run` signature `def run(days: int = 60) -> dict:` to:
```python
def run(days: int = 60, cli: bool = False, settle_offset=None) -> dict:
```

Change the actuals line `actual = station_history.fetch_actual(start, end)` to:
```python
    actual = (station_history.fetch_actual_cli(start, end) if cli
              else station_history.fetch_actual(start, end))
```

Inside the `for var in ("high", "low"):` loop, immediately after `sigma = max(sigma_cfg.get(var) or 3.0, _MIN_SIGMA)`, add:
```python
        off = (settle_offset or {}).get(var, 0.0) if cli else 0.0
```

Change the corrected-samples line `corrected = [s - bias.get(var, 0.0) for s in samples]` to:
```python
            corrected = [s - bias.get(var, 0.0) + off for s in samples]
```

(Leave the baseline arm — `base = _bin_probabilities(samples, 3.0)` and `mu0` — unchanged.)

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (all).

- [ ] **Step 5: Run the FULL suite (hourly-unchanged regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (defaults `cli=False` reproduce the existing backtest).

- [ ] **Step 6: Commit**

```bash
git add backtest.py tests/test_cli_basis.py
git commit -m "backtest: optional CLI basis (cli truth + settle_offset shift)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Log both bases + wire the Kalshi accuracy panel

**Files:**
- Modify: `scheduled_log.py`, `app.py`, `market_view.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli_basis.py`:

```python
def test_scheduled_log_records_both_bases(monkeypatch):
    import scheduled_log
    import model

    monkeypatch.setattr(calibration, "get",
                        lambda refresh=True: {"settlement_offset": {"high": 1.0, "low": 0.0}})
    monkeypatch.setattr(model, "snapshot",
                        lambda calib, settle_offset=None: {"_off": settle_offset})
    calls = []
    monkeypatch.setattr(scheduled_log.forecast_log, "record",
                        lambda snap, basis="hourly": calls.append((snap.get("_off"), basis)))
    monkeypatch.setattr(scheduled_log.forecast_log, "load", lambda path=None: [])

    scheduled_log.main()

    assert (None, "hourly") in calls                       # hourly snapshot, no offset
    assert ({"high": 1.0, "low": 0.0}, "cli") in calls     # offset snapshot, cli basis
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py::test_scheduled_log_records_both_bases -q`
Expected: FAIL — only the hourly `record` call is made (no `cli` call yet).

- [ ] **Step 3a: Implement in `scheduled_log.py`**

Replace the body of `main()` (currently records one snapshot) with:
```python
def main() -> None:
    calib = calibration.get(refresh=True)
    forecast_log.record(model.snapshot(calib))                       # hourly basis
    off = (calib or {}).get("settlement_offset")
    forecast_log.record(model.snapshot(calib, settle_offset=off), basis="cli")
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged hourly+cli snapshots; log now holds {n} records")
```

- [ ] **Step 3b: Implement in `app.py`**

Add a Kalshi accuracy loader next to `load_accuracy()`:
```python
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_accuracy_kalshi():
    """Backtest + live self-scoring on the Kalshi/CLI settlement basis."""
    import backtest
    import scoring
    calib = calibration.get(refresh=True) or {}
    off = calib.get("settlement_offset")
    bt = live = None
    try:
        bt = backtest.run(cli=True, settle_offset=off)
    except Exception:
        pass
    try:
        live = scoring.score(basis="cli")
    except Exception:
        pass
    return bt, live
```

Replace the current `_page`, `robinhood_page`, `kalshi_page` (from Part A) with:
```python
def _page(adapter, snapshot_loader, accuracy_loader, record_basis):
    snap, calib = snapshot_loader()
    try:
        forecast_log.record(snap, basis=record_basis)  # upsert; per-basis key
    except Exception:
        pass  # logging must never break the dashboard
    market_view.render_page(snap, calib, adapter, accuracy_loader)


def robinhood_page():
    _page(ROBINHOOD, load_snapshot, load_accuracy, "hourly")


def kalshi_page():
    _page(KALSHI, load_snapshot_kalshi, load_accuracy_kalshi, "cli")
```

(Do not change the `st.navigation([...])` block, `load_snapshot`, `load_snapshot_kalshi`, `load_accuracy`, page_config, or the secrets block.)

- [ ] **Step 3c: Implement in `market_view.py`**

In `render_page`, change the accuracy expander block:
```python
    with st.expander("📊 Model accuracy"):
        _render_accuracy(load_accuracy)
```
to:
```python
    with st.expander("📊 Model accuracy"):
        if adapter.basis_note:  # Kalshi page -> CLI settlement basis
            st.caption("📐 Accuracy scored on the NWS CLI settlement basis "
                       "(what Kalshi resolves on).")
        _render_accuracy(load_accuracy)
```

- [ ] **Step 4: Run tests to verify PASS**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (including `test_scheduled_log_records_both_bases`).

- [ ] **Step 5: Compile + import check**

Run: `.venv/bin/python -m py_compile app.py scheduled_log.py && .venv/bin/python -c "import markets, market_view; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Live smoke test (manual)**

Create `/tmp/accB_runner.py`:
```python
import os, sys
sys.path.insert(0, "/Users/jared/Desktop/Weather Model")
from streamlit.testing.v1 import AppTest

HARNESS = '''
import os, sys
sys.path.insert(0, "/Users/jared/Desktop/Weather Model")
import streamlit as st
import calibration, model, market_view, backtest, scoring
from markets import ROBINHOOD, KALSHI
adapter = KALSHI if os.environ.get("MARKET") == "kalshi" else ROBINHOOD
@st.cache_data(ttl=120, show_spinner=False)
def snap():
    c = calibration.get(refresh=True)
    off = (c or {}).get("settlement_offset") if adapter is KALSHI else None
    return model.snapshot(c, settle_offset=off), c
def acc_rh():
    try: bt = backtest.run()
    except Exception: bt = None
    try: lv = scoring.score()
    except Exception: lv = None
    return bt, lv
def acc_kx():
    c = calibration.get(refresh=True) or {}
    off = c.get("settlement_offset")
    try: bt = backtest.run(cli=True, settle_offset=off)
    except Exception: bt = None
    try: lv = scoring.score(basis="cli")
    except Exception: lv = None
    return bt, lv
s, calib = snap()
market_view.render_page(s, calib, adapter, acc_kx if adapter is KALSHI else acc_rh)
'''
open("/tmp/accB_harness.py", "w").write(HARNESS)

def run(market):
    os.environ["MARKET"] = market
    at = AppTest.from_file("/tmp/accB_harness.py", default_timeout=240).run()
    assert not at.exception, at.exception
    caps = [c.value for c in at.caption]
    cli_note = any("CLI settlement basis" in c for c in caps)
    print(market, "cli_accuracy_caption:", cli_note)

for m in ("robinhood", "kalshi"):
    run(m)
```
Run: `.venv/bin/python /tmp/accB_runner.py 2>&1 | grep -v Warning | grep -v warnings.warn`
Expected: both render with NO exception; `robinhood cli_accuracy_caption: False`; `kalshi cli_accuracy_caption: True`. Then `rm /tmp/accB_runner.py /tmp/accB_harness.py`. (If the live data fetch fails in this sandbox, report it but treat it as environmental, not a code failure.)

- [ ] **Step 7: Commit**

```bash
git add scheduled_log.py app.py market_view.py tests/test_cli_basis.py
git commit -m "Kalshi accuracy panel on CLI basis; log both bases

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** forecast_log basis (Task 1) ← spec §1; scoring basis + per_lead hourly (Task 2) ← §2; backtest cli/offset (Task 3) ← §3; scheduled_log both bases (Task 4 §3a) ← §4; app load_accuracy_kalshi + `_page` record_basis + page funcs (Task 4 §3b) ← §5; market_view CLI caption (Task 4 §3c) ← §6. Backward-compat (legacy untagged → hourly) = Task 1 test. Robinhood-unchanged = hourly defaults across Tasks 2/3 + full-suite regression in Task 3 Step 5.
- **Type consistency:** `basis` is a str ("hourly"/"cli") in `record`, `_key`, `score`, `_actuals_for`. `settle_offset`/`cli` in `backtest.run` match `model`/Part A conventions (`{"high","low"}` dict). `load_accuracy_kalshi` returns `(bt, live)` like `load_accuracy`. `_page(adapter, snapshot_loader, accuracy_loader, record_basis)` call sites consistent.
- **Placeholder scan:** none — every code step is complete.
- **Robinhood invariant:** `record()`/`score()`/`backtest.run()` defaults are hourly/no-offset; `robinhood_page` passes `load_snapshot, load_accuracy, "hourly"`; the CLI accuracy caption is gated on `adapter.basis_note` (None for Robinhood).
