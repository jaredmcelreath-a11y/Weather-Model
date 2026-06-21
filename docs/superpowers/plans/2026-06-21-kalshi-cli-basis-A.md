# Kalshi CLI Settlement Basis — Part A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Kalshi page's high/low probabilities & consensus on the NWS CLI settlement basis via a calibrated per-variable offset, leaving Robinhood byte-for-byte identical.

**Architecture:** A `settlement_offset` ({high,low}) is calibrated as the mean `CLI_actual − hourly_actual` (CLI from the IEM daily summary) and stored in `calibration.json`. `model.predict_variable` gains an optional, default-off `settle_offset` that shifts the forecast `samples`/`fullday` (not the hard observed bound). The Kalshi page loads a snapshot with the offset; Robinhood passes nothing.

**Tech Stack:** Python 3.9, Streamlit, pytest. Spec: `docs/superpowers/specs/2026-06-21-kalshi-cli-basis-A-design.md`.

---

## File Structure

- **`sources/station_history.py`** (modify) — add `_parse_daily` + `fetch_actual_cli` (IEM daily summary → CLI truth). Existing hourly `fetch_actual` untouched.
- **`calibration.py`** (modify) — add `_settlement_offset` helper; `compute()` fetches CLI truth and stores `settlement_offset`.
- **`model.py`** (modify) — `predict_variable`/`_predict_from`/`predict`/`snapshot` gain optional `settle_offset=None`; shifts forecast samples only.
- **`markets.py`** (modify) — `MarketAdapter` gains `basis_note: str | None = None`; KALSHI sets it.
- **`market_view.py`** (modify) — render `adapter.basis_note` as a caption (Kalshi-only).
- **`app.py`** (modify) — add `load_snapshot_kalshi()`; `_page` takes a loader + a record flag; only Robinhood records the hourly forward log.
- **Tests:** `tests/test_cli_basis.py` (new) for station_history + calibration + model; extend `tests/test_markets.py` for `basis_note`.

Robinhood numbers are provably unchanged: every shared addition is optional/default-off and Robinhood's calls pass no offset.

---

## Task 1: CLI truth from the IEM daily summary

**Files:**
- Modify: `sources/station_history.py`
- Test: `tests/test_cli_basis.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_basis.py`:

```python
"""Tests for the Kalshi CLI settlement basis (Part A): CLI truth fetch parsing,
the calibrated settlement offset, and the model's settle_offset shift."""

from datetime import date

from sources.station_history import _parse_daily

SAMPLE_CSV = (
    "station,day,max_temp_f,min_temp_f,precip_in\n"
    "DFW,2026-06-08,95.0,78.0,0.0\n"
    "DFW,2026-06-09,None,77.0,0.0\n"      # missing max -> skipped
    "DFW,2026-06-10,94.0,M,0.0\n"          # missing min -> skipped
    "DFW,2026-06-11,93.0,79.0,0.0\n"
)


def test_parse_daily_maps_day_to_high_low():
    out = _parse_daily(SAMPLE_CSV)
    assert out[date(2026, 6, 8)] == (95.0, 78.0)
    assert out[date(2026, 6, 11)] == (93.0, 79.0)


def test_parse_daily_skips_missing_rows():
    out = _parse_daily(SAMPLE_CSV)
    assert date(2026, 6, 9) not in out   # None max
    assert date(2026, 6, 10) not in out  # M min
    assert len(out) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: FAIL with `ImportError: cannot import name '_parse_daily'`.

- [ ] **Step 3: Implement in `sources/station_history.py`**

Add after the existing `URL = ...` line:

```python
DAILY_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
```

Add these two functions at the end of the file:

```python
def _parse_daily(text: str) -> dict[date, tuple[float, float]]:
    """Parse the IEM daily-summary CSV into {day: (max_temp_f, min_temp_f)}.

    Rows with a missing/'None'/'M' max or min are skipped. This is the NWS-CLI
    settlement basis (continuous ASOS daily extremes) that Kalshi resolves on.
    """
    out: dict[date, tuple[float, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        hi, lo = row.get("max_temp_f"), row.get("min_temp_f")
        if hi in (None, "", "M", "None") or lo in (None, "", "M", "None"):
            continue
        try:
            out[date.fromisoformat(row["day"])] = (float(hi), float(lo))
        except (ValueError, KeyError):
            continue
    return out


def fetch_actual_cli(start: date, end: date) -> dict[date, tuple[float, float]]:
    """{day: (cli_high_f, cli_low_f)} from the IEM daily summary for [start, end].

    The CLI daily max/min come from continuous (1-minute) ASOS data, so they can
    exceed the hourly METAR extremes that `fetch_actual` returns — this is the
    basis Kalshi settles on (vs Robinhood's hourly basis)."""
    params = {
        "network": "TX_ASOS", "stations": "DFW", "format": "comma",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
    }
    return _parse_daily(get_text(DAILY_URL, params))
```

(`csv`, `io`, `date`, and `get_text` are already imported in this file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (2 passed). Ignore the LibreSSL `NotOpenSSLWarning`.

- [ ] **Step 5: Commit**

```bash
git add sources/station_history.py tests/test_cli_basis.py
git commit -m "Add IEM daily-summary CLI truth fetch for Kalshi basis

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Calibrate the settlement offset

**Files:**
- Modify: `calibration.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_basis.py`:

```python
from calibration import _settlement_offset


def test_settlement_offset_means_the_cli_minus_hourly_gap():
    cli = {date(2026, 6, 8): (95.0, 78.0), date(2026, 6, 9): (94.0, 77.0)}
    hourly = {date(2026, 6, 8): (94.0, 78.0), date(2026, 6, 9): (93.0, 79.0)}
    off = _settlement_offset(cli, hourly)
    assert off["high"] == 1.0    # (1 + 1) / 2
    assert off["low"] == -1.0    # (0 + -2) / 2
    assert off["n_days"] == 2


def test_settlement_offset_zero_when_no_overlap():
    off = _settlement_offset({date(2026, 6, 8): (95.0, 78.0)}, {})
    assert off == {"high": 0.0, "low": 0.0, "n_days": 0}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: FAIL with `ImportError: cannot import name '_settlement_offset'`.

- [ ] **Step 3: Implement in `calibration.py`**

Add this helper (e.g. just above `def compute()`):

```python
def _settlement_offset(cli: dict, hourly: dict) -> dict:
    """Mean (CLI − hourly) daily-extreme gap, per variable.

    The Kalshi page adds this to the hourly-basis forecast to reach the CLI
    settlement basis. Zero offset when there is no overlapping history (safe
    degrade to the current hourly behavior)."""
    dh, dl = [], []
    for day, (chi, clo) in cli.items():
        if day not in hourly:
            continue
        hhi, hlo = hourly[day]
        dh.append(chi - hhi)
        dl.append(clo - hlo)
    if not dh:
        return {"high": 0.0, "low": 0.0, "n_days": 0}
    return {
        "high": round(sum(dh) / len(dh), 2),
        "low": round(sum(dl) / len(dl), 2),
        "n_days": len(dh),
    }
```

In `compute()`, after the line `actual = station_history.fetch_actual(start, end)`, add:

```python
    try:
        cli_actual = station_history.fetch_actual_cli(start, end)
    except Exception:
        cli_actual = {}
```

Then in the returned dict (the `return { ... }` at the end of `compute()`), add a
`"settlement_offset"` entry alongside `"cooling": cooling,`:

```python
        "cooling": cooling,
        "settlement_offset": _settlement_offset(cli_actual, actual),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_cli_basis.py
git commit -m "Calibrate Kalshi CLI settlement offset from CLI vs hourly truth

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Apply the offset in the model (default-off)

**Files:**
- Modify: `model.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_basis.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    """A synthetic 24-hour member peaking at `peak` at 15:00 local."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]  # max=peak, min=peak-15
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


def test_settle_offset_shifts_consensus_and_distribution():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    plus = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0})
    assert base["consensus"] == 91.0
    assert plus["consensus"] == 92.0
    # Constant shift must not change the spread, only the location.
    assert plus["sigma_used"] == base["sigma_used"]
    assert (model.prob_at_least(plus["probabilities"], 92)
            > model.prob_at_least(base["probabilities"], 92))


def test_zero_offset_is_identical_to_none_robinhood_guard():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    zero = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 0.0, "low": 0.0})
    assert base == zero


def test_predict_from_threads_offset():
    day = date(2030, 7, 1)
    pf = model._predict_from(_series(day), {"obs": ([], [])}, day, None, None,
                             {"high": 1.0, "low": 0.0})
    assert pf["high"]["consensus"] == 92.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -k "offset or guard or threads" -q`
Expected: FAIL — `predict_variable()` / `_predict_from()` take too many positional args (offset param not yet added).

- [ ] **Step 3: Implement in `model.py`**

(a) Change the `predict_variable` signature:

```python
def predict_variable(series, obs_series, day, variable, now, calib,
                     settle_offset=None):
```

(b) Insert the offset shift immediately AFTER the radiational-cooling block and
BEFORE the line `calib_sigma = (calib or {}).get("sigma", {}).get(variable)`:

```python
    # Kalshi settlement basis: shift the forecast distribution to the CLI basis
    # by a calibrated per-variable offset. Applied to the forecast samples only,
    # NOT the hard observed bound (the offset is an average gap, not a floor) —
    # so consensus/bins move but still-possible bins are not zeroed. A constant
    # shift leaves sigma and locked_ratio unchanged. None => Robinhood, no shift.
    if settle_offset:
        off = settle_offset.get(variable, 0.0)
        if off:
            samples = [s + off for s in samples]
            fullday = [s + off for s in fullday]
```

(c) Change `_predict_from` to thread the param:

```python
def _predict_from(series, obs, day, now, calib, settle_offset=None):
    return {
        "day": day.isoformat(),
        "high": predict_variable(series, obs, day, "high", now, calib, settle_offset),
        "low": predict_variable(series, obs, day, "low", now, calib, settle_offset),
    }
```

(d) Change `predict` to accept and pass it:

```python
def predict(day: date, now: datetime | None = None, calib: dict | None = None,
            forecast_days: int = 2, settle_offset=None) -> dict:
    """Full prediction (high + low) for `day`. `now` enables the nowcast blend
    when `day` is today; pass None to force a pure forecast."""
    if now is None:
        now = datetime.now(TZ)
    series, obs = gather_series(forecast_days)
    return _predict_from(series, obs, day, now, calib, settle_offset)
```

(e) Change `snapshot` to accept it and pass it to both `_predict_from` calls:

```python
def snapshot(calib: dict | None = None, settle_offset=None) -> dict:
```
and within it:
```python
        "today": _predict_from(series, obs, today, now, calib, settle_offset),
        "tomorrow": _predict_from(series, obs, tomorrow, now, calib, settle_offset),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the FULL suite (Robinhood-unchanged regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (existing tests unaffected; the offset defaults off).

- [ ] **Step 6: Commit**

```bash
git add model.py tests/test_cli_basis.py
git commit -m "model: optional settle_offset shifts the forecast to CLI basis

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Wire the Kalshi page to the offset snapshot

**Files:**
- Modify: `markets.py`, `market_view.py`, `app.py`
- Test: `tests/test_markets.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_markets.py`:

```python
def test_basis_note_kalshi_set_robinhood_none():
    assert ROBINHOOD.basis_note is None
    assert KALSHI.basis_note and "CLI" in KALSHI.basis_note
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_markets.py::test_basis_note_kalshi_set_robinhood_none -q`
Expected: FAIL with `AttributeError: 'MarketAdapter' object has no attribute 'basis_note'`.

- [ ] **Step 3a: Add the field in `markets.py`**

In the `MarketAdapter` dataclass, add this as the LAST field (it has a default so it
must come after the existing no-default fields):

```python
    safe_hold_min: float            # safe-hold slider minimum (fraction)
    basis_note: str | None = None   # caption shown under the market heading
```

In the `KALSHI = MarketAdapter(...)` constructor, add this argument (e.g. after
`safe_hold_min=0.50,`):

```python
    basis_note=("Values on the NWS CLI settlement basis (continuous ASOS daily "
                "max/min) — what Kalshi resolves on, ~+0.9°F vs the hourly basis "
                "on highs."),
```

Leave the `ROBINHOOD = MarketAdapter(...)` constructor unchanged (basis_note
defaults to `None`).

- [ ] **Step 3b: Render it in `market_view.py`**

In `render_variable`, immediately AFTER the line `st.markdown(adapter.heading(variable))`
and BEFORE `contracts = adapter.fetch(variable, day_iso)`, add:

```python
        if adapter.basis_note:
            st.caption(adapter.basis_note)
```

- [ ] **Step 3c: Wire the loaders in `app.py`**

Add a Kalshi snapshot loader next to `load_snapshot()`:

```python
@st.cache_data(ttl=120, show_spinner="Fetching forecasts and observations…")
def load_snapshot_kalshi():
    """Snapshot shifted to the Kalshi/CLI settlement basis via the calibrated
    settlement_offset (absent offset -> behaves like the hourly snapshot)."""
    calib = calibration.get(refresh=True)
    snap = model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"))
    return snap, calib
```

Replace the existing `_page`, `robinhood_page`, and `kalshi_page` with:

```python
def _page(adapter, snapshot_loader, record_log):
    snap, calib = snapshot_loader()
    if record_log:
        try:
            forecast_log.record(snap)  # hourly forward log; upsert, idempotent
        except Exception:
            pass  # logging must never break the dashboard
    market_view.render_page(snap, calib, adapter, load_accuracy)


def robinhood_page():
    _page(ROBINHOOD, load_snapshot, record_log=True)


def kalshi_page():
    _page(KALSHI, load_snapshot_kalshi, record_log=False)
```

(The Kalshi page passes `record_log=False` so its offset-shifted snapshot never
overwrites the hourly forward log that self-scoring reads.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (including the new `basis_note` test).

- [ ] **Step 5: Compile-check app.py**

Run: `.venv/bin/python -m py_compile app.py && .venv/bin/python -c "import markets, market_view; print('ok')"`
Expected: prints `ok` (LibreSSL warning may precede it).

- [ ] **Step 6: Live smoke test of both pages (manual)**

Create `/tmp/cli_runner.py`:

```python
import os, sys
sys.path.insert(0, "/Users/jared/Desktop/Weather Model")
from streamlit.testing.v1 import AppTest

HARNESS = '''
import os, sys
sys.path.insert(0, "/Users/jared/Desktop/Weather Model")
import streamlit as st
import calibration, model, market_view
from markets import ROBINHOOD, KALSHI
adapter = KALSHI if os.environ.get("MARKET") == "kalshi" else ROBINHOOD
@st.cache_data(ttl=120, show_spinner=False)
def snap_rh():
    c = calibration.get(refresh=True); return model.snapshot(c), c
@st.cache_data(ttl=120, show_spinner=False)
def snap_kx():
    c = calibration.get(refresh=True)
    return model.snapshot(c, settle_offset=(c or {}).get("settlement_offset")), c
snap, calib = (snap_kx() if adapter is KALSHI else snap_rh())
market_view.render_page(snap, calib, adapter, lambda: (None, None))
'''
open("/tmp/cli_harness.py", "w").write(HARNESS)

def hi(market):
    os.environ["MARKET"] = market
    at = AppTest.from_file("/tmp/cli_harness.py", default_timeout=180).run()
    assert not at.exception, at.exception
    return [s.value for s in at.subheader], at

for m in ("robinhood", "kalshi"):
    subs, at = hi(m)
    notes = [c.value for c in at.caption if "CLI settlement" in c.value]
    print(m, "title:", [t.value for t in at.title][:1], "basis_note:", bool(notes))
print("calib offset:", calibration.get(refresh=True).get("settlement_offset"))
```

Run: `.venv/bin/python /tmp/cli_runner.py 2>&1 | grep -v Warning | grep -v warnings.warn`
Expected: both markets render with no exception; **robinhood basis_note: False**, **kalshi basis_note: True**; the printed `settlement_offset` shows a positive `high`. Then `rm /tmp/cli_runner.py /tmp/cli_harness.py`.

- [ ] **Step 7: Commit**

```bash
git add markets.py market_view.py app.py tests/test_markets.py
git commit -m "Kalshi page renders on the CLI settlement basis

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** `fetch_actual_cli`/`_parse_daily` (Task 1) ← spec §1; `_settlement_offset` + `compute()` wiring (Task 2) ← §2; `predict_variable` offset shift on samples-not-bound + threading (Task 3) ← §3 and the "subtle decision"; `load_snapshot_kalshi` + `_page` record guard (Task 4c) ← §4 and the logging error-handling note; `basis_note` adapter+render (Task 4a/4b) ← §5. Robinhood-unchanged guard = Task 3 `test_zero_offset_is_identical_to_none`. Tests = §Testing.
- **Type consistency:** `settle_offset` is a `{"high":float,"low":float,...}` dict (or `None`) everywhere; `predict_variable(..., settle_offset=None)`, `_predict_from(..., settle_offset=None)`, `predict(..., settle_offset=None)`, `snapshot(..., settle_offset=None)` consistent. `settlement_offset` is the calibration.json key feeding `settle_offset`. `MarketAdapter.basis_note` used consistently in markets.py and market_view.py.
- **Placeholder scan:** none — every code step is complete.
- **Offset-not-bound invariant:** Task 3 step 3b applies `off` only to `samples`/`fullday`; `observed`, `_apply_hard_bound`, and `observed_so_far` are untouched, per spec.
