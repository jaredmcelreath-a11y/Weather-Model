# Accuracy Scorecard & Edge Tracker pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two standalone Streamlit pages — an Accuracy Scorecard (forecast skill) and an Edge Tracker (model-vs-market forecast edge + realized-bet P&L attribution) — surfacing analytics the model already computes.

**Architecture:** Each page is a new module exposing a thin `render()`, mirroring `bet_view.py`, wired into `st.navigation` in `app.py`. New aggregation lives in pure, unit-tested functions; `render()` stays thin. The Accuracy page reuses the existing `market_view._render_accuracy` body (removed from the Forecast page) plus new headline tiles and live reliability charts. The Edge page renders `edge_report.metrics()` live and a new `pnl_attribution()` over `bet_history` rows.

**Tech Stack:** Python, Streamlit, Altair, pandas, pytest.

## Global Constraints

- Kalshi/CLI settlement basis only — no Robinhood/hourly-basis variants (the live site is Kalshi-only).
- No new data capture — consume only fields already in `forecast_log.jsonl`, `betting_log.jsonl`, `settlements.jsonl`, and the Kalshi portfolio API.
- Tables render via `market_view._html_table` / `market_view._html_df` (canvas `st.dataframe` cannot center — established project constraint); charts via Altair matching `market_view._chart_colors()`.
- Metric cards: `market_view.metric_card(label, value, help_text=None)`, rendered with `col.markdown(card, unsafe_allow_html=True)`.
- Streamlit is not installed in the local dev env. Test modules that import a Streamlit-importing module MUST stub it first (see the stub block in Task 1, Step 1). Pure-function tests run locally; `render()` is verified on deploy.
- Every page must be empty-safe and never crash the dashboard: degrade to an "accumulating" caption when data is sparse; isolate credential/network failures.

---

## File Structure

- `edge_view.py` (Create) — Edge Tracker page: pure `pnl_attribution`, `assemble`, and `render`.
- `accuracy_view.py` (Create) — Accuracy Scorecard page: pure `headline_tiles` and `render`.
- `market_view.py` (Modify) — remove the "Model Accuracy" expander from `render_page`; add live-reliability charts to `_render_accuracy`.
- `app.py` (Modify) — add `accuracy_page` / `edge_page` and two `st.Page` entries.
- `tests/test_edge_view.py` (Create) — `pnl_attribution` + `assemble` + import smoke.
- `tests/test_accuracy_view.py` (Create) — `headline_tiles` + import smoke.

---

## Task 1: Edge Tracker — `pnl_attribution` (pure)

**Files:**
- Create: `edge_view.py`
- Test: `tests/test_edge_view.py`

**Interfaces:**
- Consumes: `bet_history` row dicts — each has `status` (`"settled"`/`"closed"`/`"open"`), `entry` (float price you paid for your side, 0–1, may be `None`), `pnl` (float, realized).
- Produces: `edge_view.pnl_attribution(bet_rows: list[dict]) -> dict` returning
  `{"with_market": {"n","wins","losses","net_pnl"}, "against_market": {"n","wins","losses","net_pnl"}}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_edge_view.py`:

```python
"""Edge Tracker page — pure aggregation tests + import smoke. edge_view imports
streamlit, absent in this dev env, so stub it before importing (see test_recap_render)."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())


def test_pnl_attribution_splits_by_entry_price():
    import edge_view
    rows = [
        {"status": "settled", "entry": 0.70, "pnl": 3.0},    # with-market win
        {"status": "settled", "entry": 0.55, "pnl": -5.5},   # with-market loss
        {"status": "closed",  "entry": 0.30, "pnl": 7.0},    # against-market win
        {"status": "open",    "entry": 0.40, "pnl": 1.0},    # skipped: not realized
        {"status": "settled", "entry": None, "pnl": 2.0},    # skipped: no entry price
    ]
    out = edge_view.pnl_attribution(rows)
    assert out["with_market"] == {"n": 2, "wins": 1, "losses": 1, "net_pnl": -2.5}
    assert out["against_market"] == {"n": 1, "wins": 1, "losses": 0, "net_pnl": 7.0}


def test_pnl_attribution_entry_exactly_half_is_with_market():
    import edge_view
    out = edge_view.pnl_attribution([{"status": "settled", "entry": 0.50, "pnl": 1.0}])
    assert out["with_market"]["n"] == 1
    assert out["against_market"]["n"] == 0


def test_pnl_attribution_empty():
    import edge_view
    out = edge_view.pnl_attribution([])
    assert out["with_market"] == {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
    assert out["against_market"] == {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'edge_view'`.

- [ ] **Step 3: Write minimal implementation**

Create `edge_view.py`:

```python
"""Edge Tracker page — forecast edge vs. the Kalshi market (from the betting-time
log) plus realized-edge P&L attribution (from your actual bets).

Two independent sections so one failing does not blank the other:
  A. Forecast edge — model vs. market at each betting slot, scored once settled.
  B. Realized edge — your bets split into with-market (bought the favorite) vs.
     against-market (bought the underdog), each with net P&L.
"""
from __future__ import annotations


def pnl_attribution(bet_rows: list[dict]) -> dict:
    """Split realized bets by entry price: with-market (entry >= 0.50, you bought
    the market favorite) vs against-market (entry < 0.50, you bought the underdog).
    Realized = settled or closed; open bets and rows without an entry are skipped.
    Returns {bucket: {n, wins, losses, net_pnl}} with net_pnl rounded to cents."""
    buckets = {
        "with_market": {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
        "against_market": {"n": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
    }
    for r in bet_rows:
        if r.get("status") not in ("settled", "closed"):
            continue
        entry = r.get("entry")
        if entry is None:
            continue
        b = buckets["with_market" if entry >= 0.50 else "against_market"]
        b["n"] += 1
        pnl = r.get("pnl") or 0.0
        b["wins" if pnl > 0 else "losses"] += 1
        b["net_pnl"] += pnl
    for b in buckets.values():
        b["net_pnl"] = round(b["net_pnl"], 2)
    return buckets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_edge_view.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add edge_view.py tests/test_edge_view.py
git commit -m "feat: edge_view.pnl_attribution — with/against-market P&L split"
```

---

## Task 2: Edge Tracker — `assemble` (pure)

**Files:**
- Modify: `edge_view.py`
- Test: `tests/test_edge_view.py`

**Interfaces:**
- Consumes: `edge_report.join(betting_rows, cli_map, hourly_map)` and `edge_report.metrics(joined)` (existing). `betting_rows` are `betting_log` dicts (`target_date`, `variable`, `capture_slot`, `cli_consensus`, `market_ev`, `market_buckets`, `flat_offset`, `live_gap`). `cli_map`/`hourly_map` are `{date: (high, low)}` from `settlements.as_map(basis)`.
- Produces: `edge_view.assemble(betting_rows, cli_map, hourly_map) -> {"metrics": dict, "headline": {"n","disagreements","model_wins","market_wins"}}`. `metrics` is `edge_report.metrics`'s `{(slot, variable, subset): stats}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_edge_view.py`:

```python
from datetime import date


def test_assemble_headline_rolls_up_all_subset():
    import edge_view
    rows = [
        {"target_date": "2026-07-01", "variable": "high", "capture_slot": "15:30",
         "cli_consensus": 97.9, "flat_offset": 0.89, "live_gap": 1.2,
         "market_ev": 96.0, "market_buckets": [[None, 96, 0.6], [97, 98, 0.4]]},
    ]
    cli_map = {date(2026, 7, 1): (98.0, 79.0)}       # actual high 98 -> bucket (97,98)
    hourly_map = {date(2026, 7, 1): (97.0, 79.0)}
    out = edge_view.assemble(rows, cli_map, hourly_map)
    h = out["headline"]
    # model 97.9 -> (97,98) == actual; market top bucket (None,96) != actual -> model wins
    assert h == {"n": 1, "disagreements": 1, "model_wins": 1, "market_wins": 0}
    assert ("15:30", "high", "all") in out["metrics"]


def test_assemble_empty_is_zeroed():
    import edge_view
    out = edge_view.assemble([], {}, {})
    assert out["headline"] == {"n": 0, "disagreements": 0, "model_wins": 0, "market_wins": 0}
    assert out["metrics"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_view.py::test_assemble_headline_rolls_up_all_subset -q`
Expected: FAIL with `AttributeError: module 'edge_view' has no attribute 'assemble'`.

- [ ] **Step 3: Write minimal implementation**

Add to `edge_view.py` — the import at the top of the module and the function:

```python
import edge_report
```

```python
def assemble(betting_rows: list[dict], cli_map: dict, hourly_map: dict) -> dict:
    """Join betting-log rows to settlements and compute the forecast-edge metrics
    (edge_report.metrics), plus a headline roll-up summed across the 'all' subset
    of every (slot, variable) group. Empty/unsettled input -> zeroed headline,
    empty metrics."""
    joined = edge_report.join(betting_rows, cli_map, hourly_map)
    metrics = edge_report.metrics(joined)
    head = {"n": 0, "disagreements": 0, "model_wins": 0, "market_wins": 0}
    for (_slot, _var, subset), m in metrics.items():
        if subset != "all":
            continue
        head["n"] += m["n"]
        head["disagreements"] += m["disagreements"]
        head["model_wins"] += m["model_bin_wins"]
        head["market_wins"] += m["market_bin_wins"]
    return {"metrics": metrics, "headline": head}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_edge_view.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add edge_view.py tests/test_edge_view.py
git commit -m "feat: edge_view.assemble — join betting log to settlements + headline"
```

---

## Task 3: Edge Tracker — `render()` and nav wiring

**Files:**
- Modify: `edge_view.py`
- Modify: `app.py:216-227` (the page functions + `st.navigation` list)
- Test: `tests/test_edge_view.py`

**Interfaces:**
- Consumes: `betting_log.load()`, `settlements.as_map("cli")`, `settlements.as_map("hourly")`, `edge_view.assemble`, `edge_view.pnl_attribution`, `bet_view._load_bets()` (returns `(rows, summary, curve, balance)`; raises `kalshi_auth.KalshiCredentialsError` when creds absent), `market_view.metric_card`, `market_view._html_table`, `market_view._inject_theme`, `market_view._seed_theme`.
- Produces: `edge_view.render()` (no args, draws the page). `app.py` `edge_page()`.

- [ ] **Step 1: Write the failing tests (offset verdict + import smoke)**

Append to `tests/test_edge_view.py`:

```python
def test_offset_verdict_high_all_subset_only():
    import edge_view
    metrics = {
        ("15:30", "high", "all"): {
            "flat_rmse": 0.90, "live_rmse": 0.60, "flip_toward": 3, "flip_away": 1,
            "n": 5, "model_mae": 1.0, "market_mae": 1.2,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
        ("15:30", "high", "boundary"): {  # ignored: not the 'all' subset
            "flat_rmse": 0.5, "live_rmse": 0.5, "flip_toward": 0, "flip_away": 0,
            "n": 1, "model_mae": 1.0, "market_mae": 1.2,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
        ("09:00", "low", "all"): {  # ignored: low has no offset predictor
            "flat_rmse": None, "live_rmse": None, "flip_toward": None, "flip_away": None,
            "n": 5, "model_mae": 1.0, "market_mae": 1.1,
            "disagreements": 0, "model_bin_wins": 0, "market_bin_wins": 0},
    }
    lines = edge_view._offset_verdict(metrics)
    assert len(lines) == 1
    assert "15:30" in lines[0] and "live gap beats flat" in lines[0]


def test_edge_view_exposes_render():
    import edge_view
    assert hasattr(edge_view, "render")
    assert callable(edge_view.render)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_view.py::test_offset_verdict_high_all_subset_only tests/test_edge_view.py::test_edge_view_exposes_render -q`
Expected: FAIL (`AttributeError` — no `_offset_verdict`/`render` yet).

- [ ] **Step 3: Write the implementation**

Add to `edge_view.py`. Put the Streamlit imports at the top of the module (after the existing `import edge_report`):

```python
import streamlit as st

import betting_log
import market_view
import settlements
```

Then add the helpers and `render`:

```python
def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _offset_verdict(metrics: dict) -> list[str]:
    """One line per slot for the HIGH variable's 'all' subset, comparing the flat
    +0.9 settlement offset against the live-gap predictor (RMSE, lower better) —
    the one real edge lever. Low has no offset predictor, so it is skipped."""
    out = []
    for (slot, variable, subset), m in sorted(metrics.items()):
        if variable != "high" or subset != "all":
            continue
        if m.get("live_rmse") is None or m.get("flat_rmse") is None:
            continue
        verdict = ("live gap beats flat" if m["flat_rmse"] - m["live_rmse"] >= 0.15
                   else "no clear offset edge")
        out.append(f"{slot}: flat RMSE {m['flat_rmse']} vs live RMSE {m['live_rmse']} "
                   f"({verdict}); flips toward {m['flip_toward']} / away {m['flip_away']}")
    return out


def _edge_rows(metrics: dict) -> list[dict]:
    """Flatten metrics {(slot, variable, subset): stats} into display rows,
    boundary-first within each (slot, variable)."""
    order = {"boundary": 0, "all": 1, "mid_bin": 2}
    rows = []
    for (slot, variable, subset), m in sorted(
            metrics.items(), key=lambda kv: (kv[0][0], kv[0][1], order.get(kv[0][2], 9))):
        rows.append({
            "slot": slot, "variable": variable, "day type": subset.replace("_", "-"),
            "n": m["n"],
            "model MAE": m["model_mae"], "market MAE": m["market_mae"],
            "disagree": m["disagreements"],
            "model won": m["model_bin_wins"], "market won": m["market_bin_wins"],
        })
    return rows


def render():
    import pandas as pd

    market_view._inject_theme(market_view._seed_theme())
    st.title("Edge")

    # --- Part A: forecast edge vs. market (needs no credentials) ---
    st.subheader("Forecast edge vs. market")
    st.caption(
        "At each betting slot the model's consensus and the live Kalshi price are "
        "logged; once the day settles we score which was closer. The rows that "
        "matter are **boundary** days — consensus near a Kalshi bin edge — where a "
        "small error flips the bet.")
    try:
        rows = betting_log.load()
        data = assemble(rows, settlements.as_map("cli"), settlements.as_map("hourly"))
    except Exception:
        data = {"headline": {"n": 0}, "metrics": {}}
    head = data["headline"]
    if not head.get("n"):
        st.info("Accumulating — no settled betting-time rows yet. This fills in as "
                "days settle (one day's lead after each slot).")
    else:
        c = st.columns(4)
        c[0].markdown(market_view.metric_card("Settled slots", str(head["n"])),
                      unsafe_allow_html=True)
        c[1].markdown(market_view.metric_card(
            "Disagreements", str(head["disagreements"]),
            "Days the model and market pointed at different bins."),
            unsafe_allow_html=True)
        c[2].markdown(market_view.metric_card(
            "Model won", f"{head['model_wins']} ({_pct(head['model_wins'], head['disagreements'])})",
            "Of the disagreements, how often the model's bin was the settled one."),
            unsafe_allow_html=True)
        c[3].markdown(market_view.metric_card(
            "Market won", f"{head['market_wins']} ({_pct(head['market_wins'], head['disagreements'])})"),
            unsafe_allow_html=True)
        market_view._html_table(pd.DataFrame(_edge_rows(data["metrics"])))
        st.caption("Lower **MAE** (mean absolute error, °F) is the sharper forecast. "
                   "When the two disagree on the bin, **model won / market won** is who "
                   "the settlement proved right.")
        for line in _offset_verdict(data["metrics"]):
            st.caption("Settlement offset — " + line)

    # --- Part B: realized edge / P&L attribution (needs the [kalshi] secret) ---
    st.markdown("---")
    st.subheader("My realized edge")
    st.caption("Your settled bets split by the price you paid: **with-market** means "
               "you bought the favorite (entry ≥ 50¢); **against-market** means you "
               "bought the underdog. Against-market profit is edge the market didn't see.")
    import kalshi_auth
    try:
        bet_rows, _summ, _curve, _bal = bet_view._load_bets()
    except kalshi_auth.KalshiCredentialsError:
        st.info("Add your Kalshi API key to the app secrets (`[kalshi]`) to see "
                "realized-edge attribution.")
        return
    except Exception:
        st.warning("Couldn't load your Kalshi bets right now; the forecast-edge "
                   "section above is unaffected.")
        return

    attr = pnl_attribution(bet_rows)
    wm, am = attr["with_market"], attr["against_market"]
    c = st.columns(2)
    c[0].markdown(market_view.metric_card(
        "Against-market P&L", f"${am['net_pnl']:+.2f}",
        f"{am['wins']}–{am['losses']} on underdog bets — your true edge."),
        unsafe_allow_html=True)
    c[1].markdown(market_view.metric_card(
        "With-market P&L", f"${wm['net_pnl']:+.2f}",
        f"{wm['wins']}–{wm['losses']} riding the favorite."),
        unsafe_allow_html=True)
    market_view._html_table(pd.DataFrame([
        {"bet type": "against-market (underdog)", "n": am["n"],
         "wins": am["wins"], "losses": am["losses"], "net P&L": f"${am['net_pnl']:+.2f}"},
        {"bet type": "with-market (favorite)", "n": wm["n"],
         "wins": wm["wins"], "losses": wm["losses"], "net P&L": f"${wm['net_pnl']:+.2f}"},
    ]))
```

Add `import bet_view` to the top-of-module imports (alongside `import market_view`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_edge_view.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Wire the page into `app.py`**

In `app.py`, add an import near the other page-module imports (`import bet_view`):

```python
import edge_view
```

Add a page function next to `kalshi_page` (after it, before the `st.navigation` call):

```python
def edge_page():
    edge_view.render()
```

Replace the `st.navigation([...])` list so it reads:

```python
st.navigation([
    st.Page(kalshi_page, title="Forecast", default=True),
    st.Page(edge_page, title="Edge"),
    st.Page(bet_view.render, title="History"),
]).run()
```

(The `Accuracy` page is inserted in Task 5; leaving it out here keeps this task independently shippable.)

- [ ] **Step 6: Verify app imports cleanly**

Run: `python -c "import sys; from unittest.mock import MagicMock; [sys.modules.setdefault(m, MagicMock()) for m in ('streamlit','streamlit.components','streamlit.components.v1','streamlit_autorefresh')]; import edge_view; print('edge_view ok')"`
Expected: prints `edge_view ok` with no traceback.

- [ ] **Step 7: Commit**

```bash
git add edge_view.py app.py tests/test_edge_view.py
git commit -m "feat: Edge Tracker page — forecast edge vs market + realized P&L attribution"
```

---

## Task 4: Accuracy Scorecard — `headline_tiles` (pure)

**Files:**
- Create: `accuracy_view.py`
- Test: `tests/test_accuracy_view.py`

**Interfaces:**
- Consumes: the `live` dict from `scoring.score(basis="cli")` — `{"n_settled": int, "by_variable": {var: {"n","brier","exact_peak","within1", ...}}}` (values may be `None`).
- Produces: `accuracy_view.headline_tiles(live: dict) -> list[dict]` where each dict is `{"label": str, "value": str}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_accuracy_view.py`:

```python
"""Accuracy Scorecard — pure tile builder + import smoke. accuracy_view imports
streamlit, absent in this dev env, so stub it before importing."""
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())


def test_headline_tiles_formats_values():
    import accuracy_view
    live = {"n_settled": 22, "by_variable": {
        "high": {"n": 22, "brier": 0.12, "exact_peak": 82.0, "within1": 95.0},
        "low": {"n": 22, "brier": 0.15, "exact_peak": 74.0, "within1": 90.0},
    }}
    tiles = accuracy_view.headline_tiles(live)
    by = {t["label"]: t["value"] for t in tiles}
    assert by["Settled days"] == "22"
    assert by["High exact-bin"] == "82%"
    assert by["Low exact-bin"] == "74%"
    assert by["High within ±1"] == "95%"
    assert by["High Brier"] == "0.12"


def test_headline_tiles_handles_missing_and_none():
    import accuracy_view
    # No settled data at all -> just the count tile, no crash.
    tiles = accuracy_view.headline_tiles({"n_settled": 0, "by_variable": {}})
    assert tiles == [{"label": "Settled days", "value": "0"}]
    # None metric renders as an em dash, not a crash.
    tiles = accuracy_view.headline_tiles(
        {"n_settled": 3, "by_variable": {"high": {"n": 3, "brier": None,
                                                  "exact_peak": None, "within1": None}}})
    by = {t["label"]: t["value"] for t in tiles}
    assert by["High exact-bin"] == "—"
    assert by["High Brier"] == "—"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_accuracy_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'accuracy_view'`.

- [ ] **Step 3: Write minimal implementation**

Create `accuracy_view.py`:

```python
"""Accuracy Scorecard page — how good the forecast itself is, the complement to
the betting-P&L History page. Reuses market_view._render_accuracy for the detailed
body and adds glanceable headline tiles on top."""
from __future__ import annotations


def _pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def _num(v) -> str:
    return f"{v:.2f}" if v is not None else "—"


def headline_tiles(live: dict) -> list[dict]:
    """Glanceable accuracy tiles from scoring.score()'s live dict: settled-day
    count plus each variable's exact-bin %, within-±1 %, and Brier. Missing
    variables are skipped; None metrics render as an em dash."""
    tiles = [{"label": "Settled days", "value": str(live.get("n_settled", 0) or 0)}]
    by_var = live.get("by_variable") or {}
    for var in ("high", "low"):
        m = by_var.get(var)
        if not m:
            continue
        cap = var.capitalize()
        tiles.append({"label": f"{cap} exact-bin", "value": _pct(m.get("exact_peak"))})
        tiles.append({"label": f"{cap} within ±1", "value": _pct(m.get("within1"))})
        tiles.append({"label": f"{cap} Brier", "value": _num(m.get("brier"))})
    return tiles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_accuracy_view.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add accuracy_view.py tests/test_accuracy_view.py
git commit -m "feat: accuracy_view.headline_tiles — glanceable accuracy summary"
```

---

## Task 5: Accuracy Scorecard — `render()`, live reliability, remove Forecast panel, nav

**Files:**
- Modify: `accuracy_view.py`
- Modify: `market_view.py` — `render_page` (remove the `with st.expander("Model Accuracy"):` block) and `_render_accuracy` (add live-reliability charts).
- Modify: `app.py` — add `accuracy_page` and its `st.Page`.
- Test: `tests/test_accuracy_view.py`

**Interfaces:**
- Consumes: `accuracy_view.headline_tiles`, `market_view._render_accuracy(load_accuracy, calib, history_loader=...)`, `market_view.metric_card`, `market_view._inject_theme`, `market_view._seed_theme`, `calibration.get()`, `markets.KALSHI` (for `.accuracy_note`). From `app.py`: `load_accuracy_kalshi` (cached `() -> (bt, live)`), `load_calibration_history` (cached `() -> history rows`).
- Produces: `accuracy_view.render(load_accuracy, history_loader)`; `app.py` `accuracy_page()`.

- [ ] **Step 1: Write the failing test (import smoke)**

Append to `tests/test_accuracy_view.py`:

```python
def test_accuracy_view_exposes_render():
    import accuracy_view
    assert hasattr(accuracy_view, "render")
    assert callable(accuracy_view.render)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_accuracy_view.py::test_accuracy_view_exposes_render -q`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Add live-reliability charts to `market_view._render_accuracy`**

In `market_view.py`, inside `_render_accuracy`, in the `if live and live.get("n_settled"):` block, immediately AFTER the `if lrows:` `_html_df(...)` line (the per-variable live table) and BEFORE the "Per-lead breakout" comment, insert:

```python
        rc = st.columns(2)
        for i, var in enumerate(("high", "low")):
            rdf = reliability_df(live.get("by_variable", {}).get(var, {}).get("reliability"))
            if rdf is not None:
                rc[i].caption(f"{var.title()} reliability (live) — predicted vs observed")
                rc[i].altair_chart(_reliability_chart(rdf), use_container_width=True)
```

- [ ] **Step 4: Remove the accuracy expander from the Forecast page**

In `market_view.py`, in `render_page`, delete this block:

```python
    with st.expander("Model Accuracy"):
        if adapter.accuracy_note:
            st.caption(adapter.accuracy_note)
        _render_accuracy(load_accuracy, calib, history_loader=history_loader)
```

Leave `_render_accuracy` itself defined (the Accuracy page calls it). `render_page` keeps its `load_accuracy` / `history_loader` parameters unchanged (still passed by `app.py`), so no call-site edits are needed here.

- [ ] **Step 5: Write `accuracy_view.render`**

Add to `accuracy_view.py`. Put imports at the top of the module:

```python
import streamlit as st

import calibration
import market_view
from markets import KALSHI
```

Then the function:

```python
def render(load_accuracy, history_loader=None):
    """Draw the Accuracy Scorecard: headline tiles + the full self-scoring /
    reliability / calibration-drift body (market_view._render_accuracy).
    `load_accuracy` is the cached () -> (bt, live) callable; `history_loader`
    the cached () -> calibration-history rows."""
    market_view._inject_theme(market_view._seed_theme())
    st.title("Accuracy")

    try:
        _bt, live = load_accuracy()
    except Exception:
        live = None
    if live and live.get("n_settled"):
        tiles = headline_tiles(live)
        cols = st.columns(len(tiles))
        for col, t in zip(cols, tiles):
            col.markdown(market_view.metric_card(t["label"], t["value"]),
                         unsafe_allow_html=True)

    if KALSHI.accuracy_note:
        st.caption(KALSHI.accuracy_note)

    calib = None
    try:
        calib = calibration.get()
    except Exception:
        pass
    market_view._render_accuracy(load_accuracy, calib, history_loader=history_loader)
```

- [ ] **Step 6: Wire the page into `app.py`**

In `app.py`, add a page function next to `edge_page`:

```python
def accuracy_page():
    accuracy_view.render(load_accuracy_kalshi, load_calibration_history)
```

Add `import accuracy_view` near the other page-module imports. Update the `st.navigation` list to the final order:

```python
st.navigation([
    st.Page(kalshi_page, title="Forecast", default=True),
    st.Page(accuracy_page, title="Accuracy"),
    st.Page(edge_page, title="Edge"),
    st.Page(bet_view.render, title="History"),
]).run()
```

- [ ] **Step 7: Run tests + import checks**

Run: `python -m pytest tests/test_accuracy_view.py tests/test_edge_view.py -q`
Expected: PASS (all).

Run: `python -c "import sys; from unittest.mock import MagicMock; [sys.modules.setdefault(m, MagicMock()) for m in ('streamlit','streamlit.components','streamlit.components.v1','streamlit_autorefresh')]; import accuracy_view, market_view; print('imports ok')"`
Expected: prints `imports ok` with no traceback.

- [ ] **Step 8: Guard against the old panel lingering**

Run: `grep -n 'st.expander("Model Accuracy")' market_view.py`
Expected: no output (the Forecast-page panel is gone).

- [ ] **Step 9: Commit**

```bash
git add accuracy_view.py market_view.py app.py tests/test_accuracy_view.py
git commit -m "feat: Accuracy Scorecard page; remove accuracy panel from Forecast"
```

---

## Task 6: Full regression + deploy verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full local suite**

Run: `python -m pytest -q`
Expected: the new `test_edge_view.py` and `test_accuracy_view.py` pass; no previously-passing test regresses. (Tests that import Streamlit-dependent modules without a stub may be skipped/uncollected locally per the env constraint — confirm none newly ERROR.)

- [ ] **Step 2: Deploy verification checklist (manual, on Streamlit Cloud)**

Confirm after deploy:
- Sidebar shows four pages in order: Forecast · Accuracy · Edge · History.
- Forecast page no longer shows the "Model Accuracy" expander.
- Accuracy page shows headline tiles, the self-scoring tables, both backtest AND live reliability charts, model-vs-market MAE, and the calibration-drift section.
- Edge page shows the forecast-edge section (or the "accumulating" note if unsettled) and the realized-edge attribution (or the `[kalshi]`-secret prompt).

- [ ] **Step 3: Update memory**

Add a short project memory noting the two new pages shipped (nav is now four pages; accuracy moved off Forecast into `accuracy_view`; edge in `edge_view`), linking `[[kalshi-bet-history-page]]` and `[[plan-c-edge-measurement]]`. Add its one-line pointer to `MEMORY.md`.
