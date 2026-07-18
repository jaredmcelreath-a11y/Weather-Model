# Market-EV Fairness for the Edge Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score the model and market with the same statistic on the Edge page (fixing a mean-vs-mode asymmetry), annotate market liquidity non-destructively, and prove the low side is surfaced end-to-end.

**Architecture:** A one-line correctness fix in `edge_report._subset_metrics` (market's bucket now comes from its EV, matching the model's consensus), plus a new forward-only `market_volume` field logged in `betting_log._row`, aggregated (median + thin flag) in `_subset_metrics`, and shown as a column + ⚠ marker in `edge_view._edge_rows`. All aggregation stays in pure, unit-tested functions.

**Tech Stack:** Python, Streamlit, pandas, pytest.

## Global Constraints

- Do not exclude any day from the tally — liquidity is annotate-only.
- `market_volume` is forward-only: rows logged before this ships have no volume and must render as "—" and never flag.
- The mean-vs-mean fix must work retroactively on existing rows (they already carry `market_ev`).
- `MARKET_LIQUIDITY_FLOOR = 20` (contracts) — a conservative first guess that only drives the ⚠ marker; documented as tunable.
- Streamlit and `cryptography` are absent locally; `edge_view`/`betting_log` pure-function tests must import cleanly (stub Streamlit in test modules that need it — see existing `tests/test_edge_view.py` header).
- Run tests with `python3 -m pytest` (no bare `python` in this env).

---

## File Structure

- `betting_log.py` (Modify) — `_row` logs `market_volume`.
- `config.py` (Modify) — add `MARKET_LIQUIDITY_FLOOR`.
- `edge_report.py` (Modify) — mean-vs-mean bucket; `market_volume` median + `thin` in `_subset_metrics`; add `market_volume` to CSV `_COLS`.
- `edge_view.py` (Modify) — `_edge_rows` adds a volume column + ⚠ prefix.
- `tests/test_betting_log.py` (Modify) — assert `market_volume` logged.
- `tests/test_edge_report.py` (Modify) — mean-vs-mean outcome + volume/thin.
- `tests/test_edge_view.py` (Modify) — volume column/⚠ + low-path verification.

---

## Task 1: Log `market_volume` in the betting-time row

**Files:**
- Modify: `betting_log.py` (the `_row` function's `if market_var:` block, ~line 130)
- Test: `tests/test_betting_log.py`

**Interfaces:**
- Consumes: `market_var` dict from `sources.kalshi.implied_forecast` — carries `ev`, `buckets`, and `volume`.
- Produces: betting-log rows gain `market_volume` (float) when a market block is present.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_betting_log.py`:

```python
def test_row_logs_market_volume():
    cli_var = {"consensus": 97.5, "probabilities": {"97": 0.6, "98": 0.4}}
    market_var = {"ev": 97.2, "buckets": [[97, 98, 1.0]], "volume": 42.0}
    rec = betting_log._row("2026-07-13", "high", "15:30", cli_var, {}, market_var,
                           0.89, "2026-07-13T15:30:00-05:00")
    assert rec["market_ev"] == 97.2
    assert rec["market_volume"] == 42.0


def test_row_without_market_has_no_volume_key():
    cli_var = {"consensus": 97.5, "probabilities": {"97": 0.6, "98": 0.4}}
    rec = betting_log._row("2026-07-13", "high", "15:30", cli_var, {}, None,
                           0.89, "2026-07-13T15:30:00-05:00")
    assert "market_volume" not in rec
```

Confirm `betting_log` is imported at the top of `tests/test_betting_log.py` (it is — existing tests call `betting_log._row`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_betting_log.py::test_row_logs_market_volume -q`
Expected: FAIL with `KeyError: 'market_volume'`.

- [ ] **Step 3: Write minimal implementation**

In `betting_log.py`, extend the `if market_var:` block in `_row`:

```python
    if market_var:
        rec["market_ev"] = market_var.get("ev")
        rec["market_buckets"] = market_var.get("buckets")
        rec["market_volume"] = market_var.get("volume")
    return rec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_betting_log.py -q`
Expected: PASS (all, including the two new).

- [ ] **Step 5: Commit**

```bash
git add betting_log.py tests/test_betting_log.py
git commit -m "feat: log market_volume in the betting-time row"
```

---

## Task 2: Mean-vs-mean bucket comparison in `edge_report`

**Files:**
- Modify: `edge_report.py` (`_subset_metrics`, the disagreement loop ~lines 58-70)
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: joined rows with `cli_consensus`, `market_ev`, `market_buckets`, `settled_cli`.
- Produces: `disagreements` / `model_bin_wins` / `market_bin_wins` now computed with the market's **EV bucket** (`settled_bucket(market_ev, buckets)`) instead of its modal bucket.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_edge_report.py` (the `_hi` helper already exists in this file):

```python
def test_metrics_market_bucket_uses_ev_not_mode():
    # Market's MODE is (95,96) (p=0.5), but its MEAN (ev 97.5) lands in (97,98),
    # which is the settled bucket. Under the fair mean-vs-mean rule the market
    # DISAGREES with the model (95,96) and WINS. The old mode rule would have put
    # market_b == model_b == (95,96) and counted no disagreement at all.
    joined = [
        _hi("15:30", 95.5, 97.5, [[95, 96, 0.5], [97, 98, 0.3], [99, 100, 0.2]],
            98.0, 97.0, 1.0),
    ]
    m = edge_report.metrics(joined)[("15:30", "high", "all")]
    assert m["disagreements"] == 1
    assert m["market_bin_wins"] == 1
    assert m["model_bin_wins"] == 0


def test_metrics_skips_row_with_no_market_ev():
    # A row with market_buckets but market_ev None must not blow up settled_bucket;
    # it is skipped from the disagreement tally (n still counts it).
    row = _hi("15:30", 95.5, None, [[95, 96, 1.0]], 95.0, 94.0, 1.0)
    m = edge_report.metrics([row])[("15:30", "high", "all")]
    assert m["n"] == 1
    assert m["disagreements"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_edge_report.py::test_metrics_market_bucket_uses_ev_not_mode -q`
Expected: FAIL — `disagreements == 0` under the current mode rule (assert wants 1).

- [ ] **Step 3: Write minimal implementation**

In `edge_report._subset_metrics`, change the disagreement loop guard and the market bucket line:

```python
    for r in rows:
        if not r.get("market_buckets") or r.get("market_ev") is None:
            continue
        model_b = settled_bucket(r["cli_consensus"], r["market_buckets"])
        market_b = settled_bucket(r["market_ev"], r["market_buckets"])
        actual_b = settled_bucket(r["settled_cli"], r["market_buckets"])
        if model_b != market_b:
            disagreements += 1
            if model_b == actual_b:
                model_bin_wins += 1
            elif market_b == actual_b:
                market_bin_wins += 1
```

(`top_bucket` remains defined and exported; the loop simply no longer calls it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_edge_report.py -q`
Expected: PASS (all — the existing metrics tests assert only MAE/RMSE/n, which are unchanged).

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py
git commit -m "fix: Edge page scores market by its EV bucket, matching the model (mean vs mean)"
```

---

## Task 3: Liquidity annotation in `config` + `edge_report`

**Files:**
- Modify: `config.py` (near `MARKET_MIN_BUCKET_PRICE`, line ~49)
- Modify: `edge_report.py` (imports; `_subset_metrics` entry dict; `_COLS`)
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: joined rows' optional `market_volume` (float or absent); `config.MARKET_LIQUIDITY_FLOOR`.
- Produces: each metrics entry gains `market_volume` (median float or `None`) and `thin` (bool).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_edge_report.py`:

```python
def test_metrics_market_volume_median_and_thin_flag():
    # Two liquid-ish days: median(5, 100) = 52.5, >= floor 20 -> not thin.
    a = _hi("15:30", 96.0, 96.0, [[95, 96, 1.0]], 96.0, 95.0, 1.0)
    b = _hi("15:30", 96.0, 96.0, [[95, 96, 1.0]], 96.0, 95.0, 1.0)
    a["market_volume"], b["market_volume"] = 5.0, 100.0
    m = edge_report.metrics([a, b])[("15:30", "high", "all")]
    assert m["market_volume"] == 52.5
    assert m["thin"] is False


def test_metrics_thin_when_median_below_floor():
    a = _hi("16:00", 96.0, 96.0, [[95, 96, 1.0]], 96.0, 95.0, 1.0)
    b = _hi("16:00", 96.0, 96.0, [[95, 96, 1.0]], 96.0, 95.0, 1.0)
    a["market_volume"], b["market_volume"] = 5.0, 10.0   # median 7.5 < 20
    m = edge_report.metrics([a, b])[("16:00", "high", "all")]
    assert m["market_volume"] == 7.5
    assert m["thin"] is True


def test_metrics_volume_absent_is_none_not_thin():
    # Historical rows (pre-volume) -> market_volume None, never flagged thin.
    row = _hi("16:30", 96.0, 96.0, [[95, 96, 1.0]], 96.0, 95.0, 1.0)
    m = edge_report.metrics([row])[("16:30", "high", "all")]
    assert m["market_volume"] is None
    assert m["thin"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_edge_report.py::test_metrics_market_volume_median_and_thin_flag -q`
Expected: FAIL with `KeyError: 'market_volume'` (entry has no such key yet).

- [ ] **Step 3: Add the config constant**

In `config.py`, after the `MARKET_MIN_BUCKET_PRICE` line:

```python
# ⚠-marker threshold on the Edge page: a (slot, variable) subset whose median
# traded market volume falls below this is flagged as a thin market, so a
# "market win/loss" that rode on almost no trading is visible. Annotation only —
# nothing is excluded from the tally. Conservative first guess; retune with data.
MARKET_LIQUIDITY_FLOOR = 20   # contracts
```

- [ ] **Step 4: Add median + thin to `_subset_metrics`**

In `edge_report.py`, add to the imports at the top:

```python
import statistics
```

and extend the config import:

```python
from config import BIN_HIGH, BIN_LOW, MARKET_LIQUIDITY_FLOOR
```

In `_subset_metrics`, after the `entry = {...}` dict is built (before the `if variable == "high":` block), insert:

```python
    vols = [r["market_volume"] for r in rows if r.get("market_volume") is not None]
    entry["market_volume"] = round(statistics.median(vols), 1) if vols else None
    entry["thin"] = (entry["market_volume"] is not None
                     and entry["market_volume"] < MARKET_LIQUIDITY_FLOOR)
```

Add `"market_volume"` to the CSV `_COLS` list (append it after `"flip_away"`) so the offline report carries it:

```python
_COLS = ["capture_slot", "variable", "subset", "n", "model_mae", "market_mae",
         "disagreements", "model_bin_wins", "market_bin_wins", "n_boundary",
         "flat_rmse", "live_rmse", "flip_toward", "flip_away", "market_volume"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_edge_report.py -q`
Expected: PASS (all, including the three new and the unchanged report/rounding tests).

- [ ] **Step 6: Commit**

```bash
git add config.py edge_report.py tests/test_edge_report.py
git commit -m "feat: annotate Edge-page subsets with median market volume + thin-market flag"
```

---

## Task 4: Volume column + ⚠ marker in the Edge page table

**Files:**
- Modify: `edge_view.py` (`_edge_rows`)
- Test: `tests/test_edge_view.py`

**Interfaces:**
- Consumes: metrics entries now carrying `market_volume` and `thin`.
- Produces: `_edge_rows` output dicts gain a `"volume"` column and a ⚠ prefix on the `"day type"` of thin subsets.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_edge_view.py`:

```python
def test_edge_rows_shows_volume_and_thin_marker():
    import edge_view
    metrics = {
        ("15:30", "high", "all"): {
            "n": 4, "model_mae": 1.0, "market_mae": 1.2, "disagreements": 2,
            "model_bin_wins": 1, "market_bin_wins": 1,
            "market_volume": 7.5, "thin": True},
        ("15:30", "high", "mid_bin"): {
            "n": 3, "model_mae": 1.0, "market_mae": 1.2, "disagreements": 1,
            "model_bin_wins": 1, "market_bin_wins": 0,
            "market_volume": None, "thin": False},
    }
    rows = edge_view._edge_rows(metrics)
    by_type = {r["day type"]: r for r in rows}
    assert "⚠ all" in by_type                     # thin subset flagged
    assert by_type["⚠ all"]["volume"] == "7.5"
    assert "mid-bin" in by_type                    # not thin, no marker
    assert by_type["mid-bin"]["volume"] == "—"     # unknown volume
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_edge_view.py::test_edge_rows_shows_volume_and_thin_marker -q`
Expected: FAIL — `KeyError: 'volume'` (or the ⚠ key is absent).

- [ ] **Step 3: Update `_edge_rows`**

In `edge_view.py`, replace the `rows.append({...})` body inside `_edge_rows` with:

```python
        vol = m.get("market_volume")
        rows.append({
            "slot": slot, "variable": variable,
            "day type": ("⚠ " if m.get("thin") else "") + subset.replace("_", "-"),
            "n": m["n"],
            "model MAE": m["model_mae"], "market MAE": m["market_mae"],
            "volume": "—" if vol is None else f"{vol:g}",
            "disagree": m["disagreements"],
            "model won": m["model_bin_wins"], "market won": m["market_bin_wins"],
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_edge_view.py -q`
Expected: PASS (all).

- [ ] **Step 5: Add a caption note about the ⚠ marker**

In `edge_view.render`, in the Part A `else:` branch, extend the caption that follows the `_html_table(...)` call so the marker is explained. Change:

```python
        st.caption("Lower **MAE** (mean absolute error, °F) is the sharper forecast. "
                   "When the two disagree on the bin, **model won / market won** is who "
                   "the settlement proved right.")
```

to append one sentence:

```python
        st.caption("Lower **MAE** (mean absolute error, °F) is the sharper forecast. "
                   "When the two disagree on the bin, **model won / market won** is who "
                   "the settlement proved right. Both sides are scored by where their "
                   "expected value lands. A ⚠ marks a thin-market subset (low traded "
                   "volume), where the market's 'opinion' is weak.")
```

- [ ] **Step 6: Commit**

```bash
git add edge_view.py tests/test_edge_view.py
git commit -m "feat: Edge page shows market volume + ⚠ thin-market marker"
```

---

## Task 5: Verify the low path reaches the Edge page

**Files:**
- Test: `tests/test_edge_view.py`

**Interfaces:**
- Consumes: `edge_view.assemble(betting_rows, cli_map, hourly_map)`.
- Produces: proof that a `variable == "low"` row yields a `(slot, "low", "all")` metrics entry and is counted in the headline.

- [ ] **Step 1: Write the test**

Append to `tests/test_edge_view.py`:

```python
def test_assemble_surfaces_low_slot():
    import edge_view
    rows = [
        {"target_date": "2026-07-02", "variable": "low", "capture_slot": "sr",
         "cli_consensus": 76.2, "flat_offset": -0.36, "live_gap": None,
         "market_ev": 76.0, "market_buckets": [[75, 76, 0.6], [77, 78, 0.4]]},
    ]
    cli_map = {date(2026, 7, 2): (95.0, 76.0)}       # (high, low); low settles 76
    hourly_map = {date(2026, 7, 2): (94.0, 75.0)}
    out = edge_view.assemble(rows, cli_map, hourly_map)
    assert ("sr", "low", "all") in out["metrics"]
    assert out["headline"]["n"] == 1
```

(`date` is already imported in `tests/test_edge_view.py`.)

- [ ] **Step 2: Run the test**

Run: `python3 -m pytest tests/test_edge_view.py::test_assemble_surfaces_low_slot -q`
Expected: PASS immediately (low slots already flow through `edge_report`; this is a guard, not a fix).

If it FAILS, stop — the low path is broken and needs investigation before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_edge_view.py
git commit -m "test: prove the low betting slot reaches a low Edge-page row"
```

---

## Task 6: Full regression + deploy verification + memory

**Files:** none (verification only) + memory.

- [ ] **Step 1: Run the affected suites**

Run: `python3 -m pytest tests/test_edge_view.py tests/test_edge_report.py tests/test_betting_log.py -q`
Expected: all PASS.

- [ ] **Step 2: Run the full local suite**

Run: `python3 -m pytest -q --ignore=tests/test_kalshi_auth.py --ignore=tests/test_kalshi_portfolio.py --ignore=tests/test_bet_view.py`
Expected: all PASS. (Those three modules can't collect locally — the pre-existing `cryptography` env gap, unrelated to this change. Confirm no *other* module newly errors.)

- [ ] **Step 3: Deploy verification checklist (manual, post-deploy)**

- Edge page "Forecast Edge vs. Market" table has a **volume** column.
- Any thin-market subset row shows a ⚠ in its day-type cell; unknown-volume (historical) rows show "—" and no ⚠.
- The disagreements / model-won / market-won counts reflect the mean-vs-mean rule (they will differ from the pre-fix numbers on skewed days).
- Once a low betting-time row settles, a `low` row appears in the table.

- [ ] **Step 4: Update memory**

Update `[[plan-c-edge-measurement]]` (and/or the Accuracy/Edge pages memory) to note: the Edge page now scores model and market by their EV bucket (mean-vs-mean, retroactive), logs `market_volume` and flags thin markets (⚠, floor `MARKET_LIQUIDITY_FLOOR = 20`, tunable), and that **low betting slots were already shipped** (correcting the stale `[[audit-roadmap-2026-07]]` note). Refresh the one-line pointer in `MEMORY.md` if wording changes.
