# Trader Page P&L Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Trader page a readable position table, a de-noised decision log, and a truthful cumulative shadow-P&L curve — backed by log/runtime schema and settlement accounting that make the P&L correct rather than fabricated.

**Architecture:** Four parts in dependency order. (1) Entry records and `runtime["entries"]` gain bracket geometry + climate day; exit records gain price and pnl. (2) A settlement pass in `trader.run_once` closes past-day positions against the CLI settlement instead of letting the reversal path dump them at 1¢. (3) A new pure `trade_pnl.py` turns the audit log into closed trades, daily totals, and an equity curve from 0. (4) `trade_view` renders the columns, the split decision list, and the chart.

**Tech Stack:** Python 3.9-compatible typing (`from __future__ import annotations`), pytest, Streamlit, Altair, pandas.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-28-trader-page-pnl-design.md`.
- **No backfill.** Records predating the schema change are skipped by `trade_pnl`, never assumed zero.
- Pure modules stay pure: `trade_pnl.py` and `trade_view.position_rows` take plain data; no network, no Streamlit.
- Altair `:T` axes need `pd.to_datetime` first — bare date strings render a day early.
- The full suite must stay green: `python3 -m pytest -q` (810 passing at plan time).
- Local verification runs with `TRADE_GH_REPO=jaredmcelreath-a11y/Weather-Model` so the page reads the real trade-data branch.

---

### Task 1: Log + runtime schema

**Files:**
- Modify: `trader.py` (entry-record block ~line 200-210; exit-record block ~line 137-148)
- Test: `tests/test_trader.py`

**Interfaces:**
- Produces: entry records and `runtime["entries"][ticker]` carrying `variable`, `day` (ISO string), `floor`, `cap`, `label`; exit records carrying `exit_price` and `pnl`.

- [ ] **Step 1: Write failing tests** asserting an entry record and its runtime record both carry `day`/`floor`/`cap`/`label`/`variable`, and that a stop-loss exit record carries `exit_price` equal to the bid sold into plus `pnl == (exit_price - entry_ask) * count`.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_trader.py -q` — expect failures on missing keys.
- [ ] **Step 3: Implement.** In the entry block pass `variable=var, day=today_iso, floor=target.get("floor"), cap=target.get("cap"), label=target.get("label")` into `build_record` and store the same four on `runtime["entries"][ticker]`. In the exit block compute `price = cur_bid if cur_bid is not None else 0.0` and pass `exit_price=price, pnl=round((price - pos["entry_ask"]) * pos["count"], 4)` when `entry_ask` is known.
- [ ] **Step 4: Run** the suite — expect pass.
- [ ] **Step 5: Commit** `feat(trader): log bracket geometry, day, and exit price`.

---

### Task 2: Settlement close pass

**Files:**
- Modify: `trader.py` (`run_once`, new pass before the EXIT pass)
- Test: `tests/test_trader.py`

**Interfaces:**
- Consumes: Task 1's `day`/`floor`/`cap` on runtime entries.
- Produces: `trader.settle_positions(managed, today, settled_map, params) -> list[dict]` returning close decisions `{ticker, side, count, variable, exit_price, pnl, reason}`; pure, so it is unit-testable without network.

- [ ] **Step 1: Write failing tests** — a past-day YES position whose bracket contains the settled value closes at `exit_price` 1.0 with a positive `pnl` and reason `"settled won"`; one outside closes at 0.0 with reason `"settled lost"`; an unsettled day returns nothing (retry next run); a missing `day` is treated as past and closes with reason `"settled (unscored, pre-schema)"` and `pnl` `None`; today's position is untouched.
- [ ] **Step 2: Run** — expect `AttributeError: module 'trader' has no attribute 'settle_positions'`.
- [ ] **Step 3: Implement** `settle_positions`, then wire it into `run_once` before the exit pass: load `settlements.as_map("cli", station=station)`, call it, and for each decision append an exit log record, pop `runtime["entries"]`, and add the ticker to `exited` so the exit pass and `open_by_var` both skip it. Place no order.
- [ ] **Step 4: Run** the suite — expect pass.
- [ ] **Step 5: Commit** `feat(trader): close past-day positions against CLI settlement`.

---

### Task 3: `trade_pnl.py`

**Files:**
- Create: `trade_pnl.py`
- Test: `tests/test_trade_pnl.py`

**Interfaces:**
- Produces:
  - `closed_trades(records: list[dict]) -> list[dict]` → `{ticker, variable, day (date), entry_ts, exit_ts, entry_ask, exit_price, count, pnl, reason}`
  - `daily_pnl(trades: list[dict]) -> list[dict]` → `[{"date": date, "pnl": float}]`
  - `equity_curve(trades, today: date, open_marks: dict[str, float] | None) -> list[dict]` → `[{"date": date, "total": float}]`

- [ ] **Step 1: Write failing tests** covering: pairing one entry to one exit; an entry with no exit skipped; a pair whose exit lacks `exit_price` skipped; bucketing by the record's `day` not its wall-clock `ts`; cumulative totals; an anchor point at 0.0 dated the day before the first trade; a live point folded into an existing same-day point rather than duplicated; empty input returning `[]`.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_trade_pnl.py -q` — expect import failure.
- [ ] **Step 3: Implement** the three functions.
- [ ] **Step 4: Run** — expect pass.
- [ ] **Step 5: Commit** `feat: trade_pnl — closed trades, daily P&L, equity curve`.

---

### Task 4: Open Positions columns

**Files:**
- Modify: `trade_view.py` (`position_rows`, `_render_positions`)
- Test: `tests/test_trade_view.py`

**Interfaces:**
- Consumes: Task 1's runtime fields.
- Produces: `position_rows(mode, held_truth, runtime, marks=None, params=None) -> list[dict]` with keys `Date, Time, Variable, Contract, Side, Count, Entry, Current, P&L, Stop-out, Model %`. `marks` is `{ticker: {"bid": float|None, "ask": float|None, "model": float|None}}`.

- [ ] **Step 1: Write failing tests** — full column set present and ordered; `Current` reflects the mark's ask while `P&L` uses the bid × count; `Stop-out` equals `entry_ask - params["stop_loss"]`; a pre-schema row with no `day`/`label` falls back to the ticker suffix and renders `—` for absent numbers; no marks supplied renders `—` without raising.
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** `position_rows`; update `_render_positions` to build `marks` from `kalshi.fetch_orderbook` (bid/ask via `_best_bid`/`ask_ladder`) and `model.prob_for_strike`, tolerating fetch failure per ticker.
- [ ] **Step 4: Run** the suite — expect pass.
- [ ] **Step 5: Commit** `feat(trader page): position date/time/contract, current price, P&L, stop-out`.

---

### Task 5: Recent Decisions split

**Files:**
- Modify: `trade_view.py` (`_render_log`, `summarize_log`)
- Test: `tests/test_trade_view.py`

**Interfaces:**
- Produces: `partition_decisions(records) -> tuple[list[dict], list[dict]]` (actions = entry/exit/halt, skips) and `status_strip(records, variables) -> list[str]`.

- [ ] **Step 1: Write failing tests** — actions and skips separated, both newest-first; status strip reports the latest state per variable; empty input yields empty lists.
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** both helpers; render the status strip, an actions table via `market_view._html_table`, and skips inside `st.expander(f"Show skipped checks ({n})")`.
- [ ] **Step 4: Run** — expect pass.
- [ ] **Step 5: Commit** `feat(trader page): status strip, actions table, collapsed skips`.

---

### Task 6: Shadow P&L chart

**Files:**
- Modify: `trade_view.py` (new `_render_pnl`, called from `render`)
- Test: `tests/test_trade_view.py`

**Interfaces:**
- Consumes: `trade_pnl.equity_curve`, `city_view.city_control`, `market_view._chart_colors`.
- Produces: `pnl_frame(curves: dict[str, list[dict]]) -> pandas.DataFrame` with columns `date`, `total`, `city`.

- [ ] **Step 1: Write failing test** — `pnl_frame` emits one row per point per city with a `city` label column, and `date` is datetime64 (not a bare string).
- [ ] **Step 2: Run** — expect failure.
- [ ] **Step 3: Implement** `pnl_frame` and `_render_pnl`: `city_view.city_control("trader_pnl", arity=3)` defaulting to Both, per-city curves from the per-station logs, an Altair line per city coloured from `market_view._chart_colors()`, a zero rule, and a caption empty-state when no curve has points.
- [ ] **Step 4: Run** the suite — expect pass.
- [ ] **Step 5: Commit** `feat(trader page): cumulative shadow P&L chart with city toggle`.

---

### Task 7: Live verification

- [ ] **Step 1:** Launch with `FORECAST_LOG_GH_REPO=... TRADE_GH_REPO=jaredmcelreath-a11y/Weather-Model` per `.claude/skills/verify`.
- [ ] **Step 2:** Screenshot `/trader_page?city=Austin` and `?city=Both`; confirm the position table, decision split, and chart (or its empty state) render against real data.
- [ ] **Step 3:** Kill the server, run the full suite, commit any fixes.
