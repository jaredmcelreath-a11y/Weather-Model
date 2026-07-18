# Lab, Journal & Status Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three new mobile-friendly dashboard pages — Lab (scored forward-log experiments), Journal (per-day trading diary), Status (log-derived system health).

**Architecture:** One module per page (`lab_view.py`, `journal_view.py`, `status_view.py`), each with pure streamlit-free assembly functions plus a thin `render()`, mirroring `edge_view.py`. Cached loaders + nav entries in `app.py`. Grading logic is extracted from `recap.py` (not duplicated); bet-row fetching is extracted into `bet_history.fetch_rows` (not duplicated).

**Tech Stack:** Python 3.9-compatible, Streamlit, Altair (tap-to-pin chart pattern from `bet_view.equity_chart`), pandas, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-18-lab-journal-status-pages-design.md`.
- All user-visible labels/values in Title Case (project style).
- Mobile widgets by reuse only: `market_view.metric_card(label, value, help_text)` inside `st.container(key="metrics2_<page>")`; tables via `market_view._html_table(pd.DataFrame(...))`; charts use `alt.selection_point(on="click", nearest=True, empty=False, clear="dblclick")` with point marks + pinned-text readout.
- Every `render()` starts with `market_view._theme_controls()`.
- Sections are best-effort: wrap loader calls in try/except; a failed section shows an info/warning, never crashes the page.
- Test files that import streamlit-dependent modules start with the stub preamble (below), same as `tests/test_recap_render.py`.
- Local test command (this Mac): `python3 -m pytest <files> -q`. The 4 pre-existing failures in `tests/test_bet_view.py` (missing `cryptography`) and the 2 collection errors in `tests/test_kalshi_auth.py` / `tests/test_kalshi_portfolio.py` are known environment gaps — ignore them, introduce no new failures.

Streamlit stub preamble for test files:

```python
import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())
```

---

### Task 1: Extract `recap.day_scorecard`

**Files:**
- Modify: `recap.py` (the `yesterday_scorecard` function, lines ~94-117)
- Test: `tests/test_recap.py` (append)

**Interfaces:**
- Produces: `recap.day_scorecard(day: date, settled_map: dict, forecast_rows: list[dict], bet_rows: list[dict] | None = None) -> dict | None` — grades any settled day; same return shape as `yesterday_scorecard` (`{"date", "high"?, "low"?, "pnl"?}`).
- `yesterday_scorecard` keeps its exact signature and behavior (becomes a wrapper).

- [ ] **Step 1: Write the failing test** — append to `tests/test_recap.py`:

```python
def test_day_scorecard_grades_arbitrary_day():
    # Same grading as yesterday_scorecard, but for any settled day.
    from datetime import date
    import recap
    day = date(2026, 7, 10)
    settled = {day: (94.0, 77.0)}
    rows = [{"target_date": "2026-07-10", "variable": "high", "basis": "cli",
             "lead_bucket": 24, "consensus": 93.0},
            {"target_date": "2026-07-10", "variable": "low", "basis": "cli",
             "lead_bucket": 24, "consensus": 77.4}]
    out = recap.day_scorecard(day, settled, rows)
    assert out["date"] == "2026-07-10"
    assert out["high"]["settled"] == 94.0 and out["high"]["exact"] is False
    assert out["low"]["exact"] is True


def test_day_scorecard_none_when_unsettled():
    from datetime import date
    import recap
    assert recap.day_scorecard(date(2026, 7, 10), {}, []) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_recap.py -q`
Expected: the two new tests FAIL with `AttributeError: module 'recap' has no attribute 'day_scorecard'`; existing tests pass.

- [ ] **Step 3: Implement** — in `recap.py`, replace the body of `yesterday_scorecard` with the extraction. The existing function is:

```python
def yesterday_scorecard(today: date, settled_map: dict, forecast_rows: list[dict],
                        bet_rows: list[dict] | None = None) -> dict | None:
    """..."""
    yday = today - timedelta(days=1)
    hl = settled_map.get(yday)
    ...
```

Replace with:

```python
def day_scorecard(day: date, settled_map: dict, forecast_rows: list[dict],
                  bet_rows: list[dict] | None = None) -> dict | None:
    """Grade `day`'s high & low forecast against settlement. `settled_map` is
    {day: (high, low)} (e.g. settlements.as_map("cli")); `forecast_rows` is the
    forecast log; `bet_rows` (optional) attaches realized P&L. None until `day`
    is settled or if no forecast row exists. Shared by the Morning Recap
    (yesterday) and the Journal page (every settled day) so the two can never
    grade a day differently."""
    hl = settled_map.get(day)
    if not hl:
        return None
    day_iso = day.isoformat()
    out: dict = {"date": day_iso}
    for i, var in enumerate(("high", "low")):
        row = _pick_forecast(forecast_rows, day_iso, var)
        if row is not None:
            g = _grade(row, hl[i])
            if g:
                out[var] = g
    if "high" not in out and "low" not in out:
        return None
    pnl = yesterday_pnl(day_iso, bet_rows) if bet_rows else None
    if pnl:
        out["pnl"] = pnl
    return out


def yesterday_scorecard(today: date, settled_map: dict, forecast_rows: list[dict],
                        bet_rows: list[dict] | None = None) -> dict | None:
    """Grade yesterday (see day_scorecard)."""
    return day_scorecard(today - timedelta(days=1), settled_map, forecast_rows,
                         bet_rows)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_recap.py tests/test_recap_render.py -q`
Expected: all PASS (extraction preserved behavior).

- [ ] **Step 5: Commit**

```bash
git add recap.py tests/test_recap.py
git commit -m "refactor: extract recap.day_scorecard for the Journal page"
```

---

### Task 2: Extract `bet_history.fetch_rows`

**Files:**
- Modify: `bet_history.py` (append function), `app.py` (`load_recap` ~lines 116-143, `load_portfolio_value` ~lines 156-177)
- Test: `tests/test_bet_rows_fetch.py` (new)

**Interfaces:**
- Produces: `bet_history.fetch_rows(start: date) -> list[dict]` — live bet rows from the Kalshi portfolio API, each annotated with `target_date` (ISO **string**, the weather day). Raises on missing creds / network failure; callers stay best-effort.

- [ ] **Step 1: Write the failing test** — `tests/test_bet_rows_fetch.py`:

```python
"""bet_history.fetch_rows — the one shared 'live bet rows' builder (recap,
portfolio value, Journal). Lazy-imports the Kalshi client so this module stays
importable without cryptography."""
import sys
from datetime import date
from unittest.mock import MagicMock

import bet_history


def test_fetch_rows_builds_and_annotates(monkeypatch):
    stub = MagicMock()
    stub.fills.return_value = []
    stub.settlements.return_value = []
    fake_sources = MagicMock()
    fake_sources.kalshi_portfolio = stub
    monkeypatch.setitem(sys.modules, "sources", fake_sources)
    monkeypatch.setitem(sys.modules, "sources.kalshi_portfolio", stub)
    out = bet_history.fetch_rows(date(2026, 6, 22))
    assert out == []
    stub.fills.assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_bet_rows_fetch.py -q`
Expected: FAIL with `AttributeError: module 'bet_history' has no attribute 'fetch_rows'`.

- [ ] **Step 3: Implement** — append to `bet_history.py`:

```python
def fetch_rows(start: date) -> list[dict]:
    """Live bet rows straight from the Kalshi portfolio API since `start`,
    annotated with 'target_date' (the WEATHER day the ticker settles on) for
    per-day attribution. The single shared builder for the Morning Recap,
    the portfolio-value card and the Journal page. Lazy import: the Kalshi
    client needs `cryptography`, which local test envs lack. Raises on missing
    creds or network failure — callers decide best-effort."""
    from sources import kalshi_portfolio
    fills = kalshi_portfolio.fills(start)
    setts = kalshi_portfolio.settlements(start)
    meta = {t: kalshi_portfolio.market_meta(t) for t in {f["ticker"] for f in fills}}
    rows = build_rows(fills, setts, meta)
    for r in rows:
        r["target_date"] = _ticker_date(r["ticker"])
    return rows
```

Then in `app.py` replace the bet-row block inside `load_recap` (the `try:` that imports `bet_history`/`kalshi_portfolio` and builds `bet_rows`) with:

```python
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
    except Exception:
        bet_rows = None
```

And in `load_portfolio_value`, replace the four lines building `fills`/`setts`/`meta`/`rows` with:

```python
        rows = bet_history.fetch_rows(bet_history.BETS_START)
```

(keeping the surrounding `import bet_history`, `from sources import kalshi_portfolio` — still needed for `balance()`/`market_price()` — and the open-position loop unchanged).

**Note:** `_ticker_date` returns an ISO **string** (verified: it ends `.isoformat()`), which is exactly what `recap.yesterday_pnl` and the Journal compare against (`r.get("target_date") == day_iso`). `fetch_rows` stores it unchanged. `build_rows([], [], {})` returns `[]` (verified), so the empty-fills test is valid.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_bet_rows_fetch.py tests/test_recap.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bet_history.py app.py tests/test_bet_rows_fetch.py
git commit -m "refactor: shared bet_history.fetch_rows for recap/portfolio/journal"
```

---

### Task 3: `journal_view.assemble` (pure data)

**Files:**
- Create: `journal_view.py`
- Test: `tests/test_journal_view.py` (new)

**Interfaces:**
- Consumes: `recap.day_scorecard` (Task 1).
- Produces: `journal_view.assemble(today: date, settled_map: dict, forecast_rows: list[dict], bet_rows: list[dict] | None = None) -> dict` with keys `summary` (`{"high_hits7": [hit, n], "low_hits7": [hit, n], "pnl_total": float|None, "streak": int}`) and `days` (list of `day_scorecard` dicts + `"flags"`, newest first).

- [ ] **Step 1: Write the failing tests** — `tests/test_journal_view.py`:

```python
"""Journal page data layer: every settled day scored, newest first, with a
summary strip (7-day hit rates, total P&L, exact streak)."""
import sys
from datetime import date
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import journal_view

TODAY = date(2026, 7, 18)


def _rows(day_iso, high, low, flags=()):
    out = []
    for var, cons in (("high", high), ("low", low)):
        r = {"target_date": day_iso, "variable": var, "basis": "cli",
             "lead_bucket": 24, "consensus": cons}
        for f in flags:
            r[f] = True
        out.append(r)
    return out


def test_assemble_orders_newest_first_and_grades():
    settled = {date(2026, 7, 16): (93.0, 75.0), date(2026, 7, 17): (94.0, 77.0)}
    rows = _rows("2026-07-16", 93.2, 76.0) + _rows("2026-07-17", 94.0, 77.4)
    out = journal_view.assemble(TODAY, settled, rows)
    assert [d["date"] for d in out["days"]] == ["2026-07-17", "2026-07-16"]
    assert out["days"][0]["high"]["exact"] is True
    assert out["days"][1]["low"]["exact"] is False


def test_assemble_excludes_today_and_unforecast_days():
    settled = {TODAY: (95.0, 78.0), date(2026, 7, 1): (90.0, 74.0)}
    out = journal_view.assemble(TODAY, settled, [])   # no forecast rows at all
    assert out["days"] == []


def test_flags_collected_from_cli_rows():
    settled = {date(2026, 7, 16): (93.0, 75.0)}
    rows = _rows("2026-07-16", 93.0, 75.0, flags=("front_widened",))
    out = journal_view.assemble(TODAY, settled, rows)
    assert out["days"][0]["flags"] == ["front"]


def test_summary_hits_streak_and_pnl():
    settled = {date(2026, 7, 15): (92.0, 74.0),
               date(2026, 7, 16): (93.0, 75.0),
               date(2026, 7, 17): (94.0, 77.0)}
    rows = (_rows("2026-07-15", 92.0, 73.0)      # low miss -> breaks streak
            + _rows("2026-07-16", 93.0, 75.0)    # both exact
            + _rows("2026-07-17", 94.0, 77.0))   # both exact
    bets = [{"target_date": "2026-07-17", "status": "settled", "pnl": 10.0,
             "staked": 20.0},
            {"target_date": "2026-07-16", "status": "settled", "pnl": -4.0,
             "staked": 8.0}]
    out = journal_view.assemble(TODAY, settled, rows, bets)
    s = out["summary"]
    assert s["high_hits7"] == [3, 3]
    assert s["low_hits7"] == [2, 3]
    assert s["streak"] == 2                       # 7/17 and 7/16, broken 7/15
    assert s["pnl_total"] == 6.0
    assert out["days"][0]["pnl"]["net"] == 10.0


def test_assemble_empty_inputs():
    out = journal_view.assemble(TODAY, {}, [])
    assert out["days"] == [] and out["summary"]["streak"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_journal_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal_view'`.

- [ ] **Step 3: Implement** — create `journal_view.py`:

```python
"""Journal page — the trading diary: one scorecard per settled day.

Pure data assembly here (streamlit only inside render), mirroring edge_view.
Grading is recap.day_scorecard — the same function the Morning Recap uses, so
the two can never disagree about a day.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import market_view
import recap


def _day_flags(forecast_rows: list[dict], day_iso: str) -> list[str]:
    """Regime badges for the day, from the CLI forecast-log rows' latched
    flags: 'storm' (convective_widened) and/or 'front' (front_widened)."""
    flags = set()
    for r in forecast_rows:
        if r.get("target_date") != day_iso or r.get("basis", "hourly") != "cli":
            continue
        if r.get("convective_widened"):
            flags.add("storm")
        if r.get("front_widened"):
            flags.add("front")
    return sorted(flags)


def _summary(days: list[dict]) -> dict:
    """Headline strip over the (newest-first) day entries: last-7-settled-days
    exact-bin hit rates, total realized P&L, and the current both-exact streak
    (a day missing either grade breaks it)."""
    def hits(entries, var):
        graded = [e for e in entries if var in e]
        return [sum(1 for e in graded if e[var]["exact"]), len(graded)]

    last7 = days[:7]
    pnl = [e["pnl"]["net"] for e in days if e.get("pnl")]
    streak = 0
    for e in days:
        if e.get("high", {}).get("exact") and e.get("low", {}).get("exact"):
            streak += 1
        else:
            break
    return {"high_hits7": hits(last7, "high"), "low_hits7": hits(last7, "low"),
            "pnl_total": round(sum(pnl), 2) if pnl else None, "streak": streak}


def assemble(today: date, settled_map: dict, forecast_rows: list[dict],
             bet_rows: list[dict] | None = None) -> dict:
    """{summary, days}: one graded entry per settled day, newest first. Today
    is excluded even if a (preliminary) settlement row exists — the journal
    records finished days only. Days with no gradeable forecast are skipped."""
    days = []
    for day in sorted((d for d in settled_map if d < today), reverse=True):
        entry = recap.day_scorecard(day, settled_map, forecast_rows, bet_rows)
        if entry is None:
            continue
        entry["flags"] = _day_flags(forecast_rows, day.isoformat())
        days.append(entry)
    return {"summary": _summary(days), "days": days}
```

(`market_view` / `st` imports are used by Task 4's render; keeping them from the start avoids an import-shuffle commit.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_journal_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add journal_view.py tests/test_journal_view.py
git commit -m "feat: journal_view.assemble - per-settled-day scorecards + summary"
```

---

### Task 4: Journal render + nav entry

**Files:**
- Modify: `journal_view.py` (append), `app.py` (loader + page + nav)
- Test: `tests/test_journal_view.py` (append)

**Interfaces:**
- Consumes: `journal_view.assemble` (Task 3), `bet_history.fetch_rows` (Task 2), `market_view.metric_card` / `_html_table` / `_theme_controls`.
- Produces: `journal_view.day_card_html(entry) -> str`, `journal_view.render(journal_loader)`; `app.load_journal()`, `app.journal_page()`; nav gains "Journal" after "History".

- [ ] **Step 1: Write the failing tests** — append to `tests/test_journal_view.py`:

```python
def test_day_card_html_full_entry():
    entry = {"date": "2026-07-17",
             "high": {"settled": 94.0, "model": 94.0, "exact": True, "diff": 0.0,
                      "market": 93.5, "market_closer": False},
             "low": {"settled": 77.0, "model": 78.2, "exact": False, "diff": 1.2,
                     "market": None, "market_closer": None},
             "flags": ["front", "storm"],
             "pnl": {"net": 42.0, "pct": 18.0, "n": 3, "wins": 2, "losses": 1}}
    html = journal_view.day_card_html(entry)
    assert "Friday, Jul 17" in html
    assert "✓ Exact" in html
    assert "+1.2°F" in html
    assert "Model Closer" in html
    assert "⛈" in html and "🌪" in html
    assert "+$42.00" in html and "(+18%)" in html and "3 Settled Bets" in html


def test_day_card_html_minimal_entry():
    entry = {"date": "2026-07-16",
             "low": {"settled": 75.0, "model": 75.4, "exact": True, "diff": 0.4,
                     "market": None, "market_closer": None},
             "flags": []}
    html = journal_view.day_card_html(entry)
    assert "High: —" in html
    assert "P&amp;L" not in html and "P&L" not in html


def test_render_smoke_empty_and_full():
    journal_view.render(lambda: {"summary": {}, "days": []})
    journal_view.render(lambda: {
        "summary": {"high_hits7": [3, 7], "low_hits7": [5, 7],
                    "pnl_total": 12.5, "streak": 2},
        "days": [{"date": "2026-07-17",
                  "high": {"settled": 94.0, "model": 94.0, "exact": True,
                           "diff": 0.0, "market": None, "market_closer": None},
                  "flags": []}]})
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_journal_view.py -q`
Expected: new tests FAIL with `AttributeError: ... no attribute 'day_card_html'`.

- [ ] **Step 3: Implement** — append to `journal_view.py`:

```python
_EM = "—"

# Page-local CSS: full-width left-aligned day cards built on the themed
# .wxcard surface (so both themes apply), with compact line spacing.
_CSS = """<style>
.wxjday{width:100%;margin:0 0 10px 0;text-align:left;}
.wxjday .wxcard-l{margin-bottom:6px;}
.wxjl{font-size:0.95rem;line-height:1.55;color:var(--ink,inherit);}
</style>"""


def _var_line(label: str, g: dict | None) -> str:
    if not g:
        return f'<div class="wxjl">{label}: {_EM}</div>'
    mark = "✓ Exact" if g["exact"] else f'{g["diff"]:+.1f}°F'
    mkt = ""
    if g.get("market_closer") is True:
        mkt = " · Market Closer"
    elif g.get("market_closer") is False:
        mkt = " · Model Closer"
    return (f'<div class="wxjl">{label}: Settled {g["settled"]:g} · '
            f'Model {g["model"]:g} · {mark}{mkt}</div>')


def day_card_html(entry: dict) -> str:
    """One settled day as a full-width themed card: date + flag badges, a High
    line, a Low line, and a realized-P&L line when bets settled that day."""
    d = date.fromisoformat(entry["date"])
    badges = ""
    if "storm" in entry.get("flags", ()):
        badges += " ⛈"
    if "front" in entry.get("flags", ()):
        badges += " 🌪"
    pnl_line = ""
    p = entry.get("pnl")
    if p:
        sign = "+" if p["net"] >= 0 else "−"
        amt = f'{sign}${abs(p["net"]):,.2f}'
        pct = f' ({p["pct"]:+.0f}%)' if p.get("pct") is not None else ""
        n = p["n"]
        pnl_line = (f'<div class="wxjl">P&amp;L: {amt}{pct} on {n} Settled '
                    f'Bet{"s" if n != 1 else ""} ({p["wins"]}–{p["losses"]})</div>')
    return (f'<div class="wxcard wxjday">'
            f'<div class="wxcard-l">{d.strftime("%A, %b %-d")}{badges}</div>'
            f'{_var_line("High", entry.get("high"))}'
            f'{_var_line("Low", entry.get("low"))}'
            f'{pnl_line}</div>')


def render(journal_loader) -> None:
    market_view._theme_controls()   # sidebar Settings (theme picker) + theme CSS
    st.title("Journal")
    st.markdown(_CSS, unsafe_allow_html=True)
    try:
        data = journal_loader()
    except Exception:
        data = None
    if not data or not data.get("days"):
        st.info("Accumulating — the journal fills in as days settle "
                "(one card per finished day).")
        return
    s = data.get("summary") or {}

    def frac(pair):
        return f"{pair[0]}/{pair[1]}" if pair and pair[1] else _EM

    with st.container(key="metrics2_journal"):
        c = st.columns(4)
    c[0].markdown(market_view.metric_card(
        "High Hits (7d)", frac(s.get("high_hits7")),
        "Exact-bin hits on the daily high over the last 7 settled days."),
        unsafe_allow_html=True)
    c[1].markdown(market_view.metric_card(
        "Low Hits (7d)", frac(s.get("low_hits7")),
        "Exact-bin hits on the daily low over the last 7 settled days."),
        unsafe_allow_html=True)
    c[2].markdown(market_view.metric_card(
        "Exact Streak", f'{s.get("streak", 0)}d',
        "Consecutive most-recent days where BOTH the high and the low hit "
        "their exact bin."), unsafe_allow_html=True)
    pnl = s.get("pnl_total")
    c[3].markdown(market_view.metric_card(
        "Realized P&L", _EM if pnl is None else f"${pnl:+,.2f}",
        "Net realized Kalshi P&L across all journal days (needs the [kalshi] "
        "secret; blank locally)."), unsafe_allow_html=True)

    for entry in data["days"]:
        st.markdown(day_card_html(entry), unsafe_allow_html=True)
    st.caption("Graded with the same rules as the Morning Recap: the model's "
               "call is the fixed 9am capture when it exists, else the "
               "day-ahead forecast. ⛈ storm-flagged day · 🌪 front-guard day.")
```

Then in `app.py` add after `load_calibration_history`:

```python
@st.cache_data(ttl=3600, show_spinner=False)
def load_journal():
    """Every settled day scored for the Journal page. Changes ~daily; 1h TTL
    keeps same-day bet settlements reasonably fresh. Bet P&L is best-effort
    (cloud-only)."""
    from datetime import date
    import forecast_log
    import journal_view
    import settlements
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
    except Exception:
        bet_rows = None
    return journal_view.assemble(date.today(), settlements.as_map("cli"),
                                 forecast_log.load(), bet_rows)
```

Add `import journal_view` to the top-level imports, a page function next to `edge_page`:

```python
def journal_page():
    journal_view.render(load_journal)
```

and the nav entry after History:

```python
    st.Page(bet_view.render, title="History"),
    st.Page(journal_page, title="Journal"),
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_journal_view.py tests/test_recap.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add journal_view.py app.py tests/test_journal_view.py
git commit -m "feat: Journal page - per-day scorecard cards + summary strip"
```

---

### Task 5: `lab_view.head_to_head` (pure data)

**Files:**
- Create: `lab_view.py`
- Test: `tests/test_lab_view.py` (new)

**Interfaces:**
- Produces: `lab_view.head_to_head(rows, settled) -> dict` keyed `(variable, lead_bucket)` with `{n, prod_mae, cand_mae, prod_wins, cand_wins, ties, days: [{date, prod_err, cand_err}, ...]}` (days sorted ascending by date).

- [ ] **Step 1: Write the failing tests** — `tests/test_lab_view.py`:

```python
"""Lab page data layer: shadow-consensus head-to-head + per-model scoreboard,
both scored against CLI settlements."""
import sys
from datetime import date
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import lab_view

SETTLED = {date(2026, 7, 16): (93.0, 75.0), date(2026, 7, 17): (94.0, 77.0)}


def _row(day_iso, var, lead, cons, cand=None, **extra):
    r = {"target_date": day_iso, "variable": var, "basis": "cli",
         "lead_bucket": lead, "consensus": cons}
    if cand is not None:
        r["candidate_consensus"] = cand
    r.update(extra)
    return r


def test_head_to_head_scores_and_wins():
    rows = [_row("2026-07-16", "high", 24, 92.0, cand=93.5),   # prod 1.0 cand 0.5
            _row("2026-07-17", "high", 24, 94.0, cand=92.0),   # prod 0.0 cand 2.0
            _row("2026-07-17", "low", 24, 77.4, cand=77.4)]    # tie
    out = lab_view.head_to_head(rows, SETTLED)
    g = out[("high", 24)]
    assert g["n"] == 2
    assert g["prod_mae"] == 0.5 and g["cand_mae"] == 1.25
    assert g["prod_wins"] == 1 and g["cand_wins"] == 1
    assert out[("low", 24)]["ties"] == 1
    assert g["days"][0]["date"] == "2026-07-16"


def test_head_to_head_skips_cohort_unsettled_and_candidateless():
    rows = [_row("2026-07-17", "high", 0, 94.0, cand=93.0, capture_cohort="0900"),
            _row("2026-07-18", "high", 24, 95.0, cand=94.0),   # unsettled
            _row("2026-07-17", "high", 24, 94.0)]              # no candidate
    assert lab_view.head_to_head(rows, SETTLED) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lab_view'`.

- [ ] **Step 3: Implement** — create `lab_view.py`:

```python
"""Lab page — scored forward-log experiments.

Section A: shadow (candidate) consensus vs production, from forecast-log rows
that carry candidate_consensus. Section B: per-model scoreboard from the
per-source means, incl. MOS at matched lead. Pure data assembly here;
streamlit only in render — mirrors edge_view.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import market_view


def head_to_head(rows: list[dict], settled: dict) -> dict:
    """{(variable, lead_bucket): {n, prod_mae, cand_mae, prod_wins, cand_wins,
    ties, days}} for every settled rolling CLI row carrying a candidate
    consensus. Rolling rows only (the 0900 cohort would double-count the day).
    `days` = [{date, prod_err, cand_err}] ascending, for the error chart."""
    out: dict = {}
    for r in rows:
        if r.get("candidate_consensus") is None or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli" or r.get("consensus") is None:
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        key = (r["variable"], r.get("lead_bucket") or 0)
        g = out.setdefault(key, {"n": 0, "_pa": 0.0, "_ca": 0.0, "prod_wins": 0,
                                 "cand_wins": 0, "ties": 0, "days": []})
        pe = abs(r["consensus"] - actual)
        ce = abs(r["candidate_consensus"] - actual)
        g["n"] += 1
        g["_pa"] += pe
        g["_ca"] += ce
        if round(pe, 2) == round(ce, 2):
            g["ties"] += 1
        elif pe < ce:
            g["prod_wins"] += 1
        else:
            g["cand_wins"] += 1
        g["days"].append({"date": r["target_date"], "prod_err": round(pe, 2),
                          "cand_err": round(ce, 2)})
    for g in out.values():
        g["prod_mae"] = round(g.pop("_pa") / g["n"], 2)
        g["cand_mae"] = round(g.pop("_ca") / g["n"], 2)
        g["days"].sort(key=lambda d: d["date"])
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lab_view.py tests/test_lab_view.py
git commit -m "feat: lab_view.head_to_head - shadow vs production scoring"
```

---

### Task 6: `lab_view.per_model_scores`

**Files:**
- Modify: `lab_view.py` (append)
- Test: `tests/test_lab_view.py` (append)

**Interfaces:**
- Produces: `lab_view.per_model_scores(rows, settled) -> dict` keyed `(source, variable, lead_bucket)` with `{n, mae, bias}` (bias = mean of model − settled).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_lab_view.py`:

```python
def test_per_model_scores_mae_and_bias():
    rows = [_row("2026-07-16", "high", 24, 92.0,
                 sources={"nws": 92.0, "mos_nbs": 94.0}),
            _row("2026-07-17", "high", 24, 94.0,
                 sources={"nws": 95.0, "mos_nbs": 94.0})]
    out = lab_view.per_model_scores(rows, SETTLED)
    assert out[("nws", "high", 24)] == {"n": 2, "mae": 1.0, "bias": 0.0}
    assert out[("mos_nbs", "high", 24)] == {"n": 2, "mae": 0.5, "bias": 0.5}


def test_per_model_scores_excludes_prefix_mos_lav_same_day_low():
    # Same-day mos_lav lows logged before the 2026-07-19 covers_extreme fix
    # were the wrong-tail bug (14a2a3a) - they must not poison the scoreboard.
    rows = [_row("2026-07-17", "low", 0, 77.0, sources={"mos_lav": 84.0}),
            _row("2026-07-17", "low", 24, 77.0, sources={"mos_lav": 78.0})]
    out = lab_view.per_model_scores(rows, SETTLED)
    assert ("mos_lav", "low", 0) not in out
    assert out[("mos_lav", "low", 24)]["n"] == 1


def test_per_model_scores_skips_cohort_rows():
    rows = [_row("2026-07-17", "high", 0, 94.0, sources={"nws": 94.0},
                 capture_cohort="0900")]
    assert lab_view.per_model_scores(rows, SETTLED) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: new tests FAIL with `AttributeError: ... no attribute 'per_model_scores'`.

- [ ] **Step 3: Implement** — append to `lab_view.py`:

```python
# Same-day mos_lav lows logged before this date carry the wrong-tail
# covers_extreme bug (fixed 2026-07-18, commit 14a2a3a) — a now-forward LAMP
# feed reported the evening tail (~84) as the "low" on settled-77 nights.
_MOS_LAV_LOW_FIX = "2026-07-19"


def per_model_scores(rows: list[dict], settled: dict) -> dict:
    """{(source, variable, lead_bucket): {n, mae, bias}} from the per-source
    means the forecast log records (ensemble / deterministic / nws / mos_*).
    bias = mean(model − settled): positive means the source ran hot. Rolling
    rows only, matched lead by construction (the sources were captured at the
    same moment as the row's consensus)."""
    out: dict = {}
    for r in rows:
        src = r.get("sources")
        if not src or r.get("capture_cohort"):
            continue
        if r.get("basis", "hourly") != "cli":
            continue
        try:
            day = date.fromisoformat(r["target_date"])
        except (KeyError, ValueError):
            continue
        hl = settled.get(day)
        if not hl:
            continue
        actual = hl[0] if r.get("variable") == "high" else hl[1]
        if actual is None:
            continue
        lead = r.get("lead_bucket") or 0
        for name, val in src.items():
            if val is None:
                continue
            if (name == "mos_lav" and r.get("variable") == "low" and lead == 0
                    and r["target_date"] < _MOS_LAV_LOW_FIX):
                continue
            g = out.setdefault((name, r["variable"], lead),
                               {"n": 0, "_a": 0.0, "_s": 0.0})
            g["n"] += 1
            g["_a"] += abs(val - actual)
            g["_s"] += val - actual
    for g in out.values():
        g["mae"] = round(g.pop("_a") / g["n"], 2)
        g["bias"] = round(g.pop("_s") / g["n"], 2)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lab_view.py tests/test_lab_view.py
git commit -m "feat: lab_view.per_model_scores - matched-lead per-source MAE/bias"
```

---

### Task 7: Lab chart + render + nav entry

**Files:**
- Modify: `lab_view.py` (append), `app.py` (loader + page + nav)
- Test: `tests/test_lab_view.py` (append)

**Interfaces:**
- Consumes: `head_to_head` / `per_model_scores` (Tasks 5-6).
- Produces: `lab_view.chart_frame(h2h) -> list[dict]` (long-form `{date, variable, lead, series, abs_err}` records), `lab_view.render(lab_loader)`; `app.load_lab()` returning `(h2h, models)`; nav gains "Lab" before "Journal".

- [ ] **Step 1: Write the failing tests** — append to `tests/test_lab_view.py`:

```python
def test_chart_frame_long_form():
    h2h = {("high", 24): {"n": 1, "prod_mae": 1.0, "cand_mae": 0.5,
                          "prod_wins": 0, "cand_wins": 1, "ties": 0,
                          "days": [{"date": "2026-07-16", "prod_err": 1.0,
                                    "cand_err": 0.5}]}}
    recs = lab_view.chart_frame(h2h)
    assert {r["series"] for r in recs} == {"Production", "Candidate"}
    assert all(r["variable"] == "high" and r["lead"] == 24 for r in recs)
    assert recs[0]["date"] == "2026-07-16"


def test_render_smoke_empty_and_full():
    lab_view.render(lambda: ({}, {}))
    h2h = {("high", 24): {"n": 2, "prod_mae": 0.5, "cand_mae": 1.25,
                          "prod_wins": 1, "cand_wins": 1, "ties": 0,
                          "days": [{"date": "2026-07-16", "prod_err": 1.0,
                                    "cand_err": 0.5},
                                   {"date": "2026-07-17", "prod_err": 0.0,
                                    "cand_err": 2.0}]}}
    models = {("nws", "high", 24): {"n": 2, "mae": 1.0, "bias": 0.0},
              ("mos_nbs", "low", 0): {"n": 3, "mae": 0.4, "bias": -0.1}}
    lab_view.render(lambda: (h2h, models))
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: new tests FAIL with `AttributeError: ... no attribute 'chart_frame'`.

- [ ] **Step 3: Implement** — append to `lab_view.py`:

```python
_LEAD_LABEL = {0: "Same-Day", 24: "Day-Ahead"}


def chart_frame(h2h: dict) -> list[dict]:
    """Long-form records for the per-day error chart: one Production and one
    Candidate point per scored (day, variable, lead)."""
    recs = []
    for (variable, lead), g in sorted(h2h.items()):
        for d in g["days"]:
            for series, key in (("Production", "prod_err"),
                                ("Candidate", "cand_err")):
                recs.append({"date": d["date"], "variable": variable,
                             "lead": lead, "series": series,
                             "abs_err": d[key]})
    return recs


def _error_chart(recs: list[dict]):
    """Tap-to-pin absolute-error chart (same touch pattern as the History
    page's equity curve: click/tap a point to pin its readout)."""
    import altair as alt
    import pandas as pd
    df = pd.DataFrame(recs)
    enc = alt.Chart(df).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("abs_err:Q", title="Abs Error (°F)"),
        color=alt.Color("series:N", legend=alt.Legend(title=None, orient="top")))
    line = enc.mark_line(strokeWidth=2)
    pick = alt.selection_point(on="click", nearest=True,
                               fields=["date", "series"], empty=False,
                               clear="dblclick")
    dots = enc.mark_point(filled=True, opacity=1).encode(
        size=alt.condition(pick, alt.value(150), alt.value(60)),
        tooltip=[alt.Tooltip("date:T", title="date"),
                 alt.Tooltip("series:N", title="series"),
                 alt.Tooltip("abs_err:Q", title="abs error", format=".1f")],
    ).add_params(pick)
    labels = df.assign(label=df.apply(
        lambda r: (f"{r['series']} · "
                   f"{pd.to_datetime(r['date']).strftime('%b %-d')}\n"
                   f"{r['abs_err']:.1f}°F Off"), axis=1))
    pinned = alt.Chart(labels).mark_text(
        align="left", baseline="top", x=6, y=4, fontSize=13, fontWeight="bold",
        lineBreak="\n", lineHeight=15,
    ).encode(text="label:N", color="series:N").transform_filter(pick)
    return ((line + dots + pinned)
            .properties(height=240, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def render(lab_loader) -> None:
    import pandas as pd

    market_view._theme_controls()
    st.title("Lab")
    try:
        h2h, models = lab_loader()
    except Exception:
        h2h, models = {}, {}

    # --- Section A: shadow consensus vs production ---
    st.subheader("Shadow Consensus vs Production")
    st.caption("The candidate model set runs silently beside production; both "
               "are scored here once days settle. Promotion needs the "
               "candidate to win at true day-ahead lead.")
    if not h2h:
        st.info("Accumulating — no settled shadow days yet. This fills in "
                "daily as candidate-logged days settle.")
    else:
        n = sum(g["n"] for g in h2h.values())
        pw = sum(g["prod_wins"] for g in h2h.values())
        cw = sum(g["cand_wins"] for g in h2h.values())
        with st.container(key="metrics2_lab"):
            c = st.columns(3)
        c[0].markdown(market_view.metric_card(
            "Scored Rows", str(n),
            "Settled (day, variable, lead) rows where both consensuses were "
            "logged."), unsafe_allow_html=True)
        c[1].markdown(market_view.metric_card(
            "Production Wins", str(pw),
            "Rows where the live consensus was closer to settlement."),
            unsafe_allow_html=True)
        c[2].markdown(market_view.metric_card(
            "Candidate Wins", str(cw),
            "Rows where the shadow (expanded model set) consensus was closer."),
            unsafe_allow_html=True)
        table = [{"Variable": v.capitalize(),
                  "Lead": _LEAD_LABEL.get(lead, f"{lead}h"),
                  "Number": g["n"],
                  "Production MAE": g["prod_mae"], "Candidate MAE": g["cand_mae"],
                  "Ahead": ("Tie" if g["prod_mae"] == g["cand_mae"] else
                            "Production" if g["prod_mae"] < g["cand_mae"]
                            else "Candidate")}
                 for (v, lead), g in sorted(h2h.items())]
        market_view._html_table(pd.DataFrame(table))
        recs = chart_frame(h2h)
        for variable in ("high", "low"):
            sub = [r for r in recs if r["variable"] == variable]
            if sub:
                st.caption(f"{variable.capitalize()} — Absolute Error By Day "
                           "(Tap A Point To Pin Its Readout)")
                st.altair_chart(_error_chart(sub), use_container_width=True)

    # --- Section B: per-model scoreboard ---
    st.markdown("---")
    st.subheader("Per-Model Scoreboard")
    st.caption("Each source's own logged forecast vs settlement at matched "
               "lead — the evidence base for MOS weighting. Bias is model "
               "minus settled: positive ran hot, negative ran cold.")
    if not models:
        st.info("Accumulating — per-model source logging began 2026-07-17; "
                "rows appear as those days settle.")
        return
    for lead in (24, 0):
        rows = [{"Source": name, "Variable": v.capitalize(), "Number": g["n"],
                 "MAE": g["mae"], "Bias": f'{g["bias"]:+.2f}'}
                for (name, v, ld), g in sorted(models.items()) if ld == lead]
        if rows:
            st.markdown(f"**{_LEAD_LABEL.get(lead, f'{lead}h')}**")
            market_view._html_table(pd.DataFrame(rows))
```

Then in `app.py`: add `import lab_view` to the top-level imports, and after `load_journal`:

```python
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_lab():
    """Scored forward-log experiments for the Lab page. Changes ~daily."""
    import forecast_log
    import lab_view
    import settlements
    rows = forecast_log.load()
    settled = settlements.as_map("cli")
    return lab_view.head_to_head(rows, settled), lab_view.per_model_scores(rows, settled)


def lab_page():
    lab_view.render(load_lab)
```

and the nav entry between History and Journal:

```python
    st.Page(bet_view.render, title="History"),
    st.Page(lab_page, title="Lab"),
    st.Page(journal_page, title="Journal"),
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_lab_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lab_view.py app.py tests/test_lab_view.py
git commit -m "feat: Lab page - shadow head-to-head + per-model scoreboard"
```

---

### Task 8: `status_view.checks` (pure thresholds)

**Files:**
- Create: `status_view.py`
- Test: `tests/test_status_view.py` (new)

**Interfaces:**
- Produces: `status_view.checks(inputs: dict, now: datetime) -> list[dict]` — cards `{label, value, state, tip}` with `state` in `green|amber|red|unknown`; `status_view._DOT` maps state → 🟢🟡🔴⚪. `inputs` keys (all optional): `last_capture` (aware datetime), `obs_time` (aware datetime), `dropped_sources` (list[str]), `calib_computed` (aware datetime), `last_settled` (date), `betting_rows_today` (int).

- [ ] **Step 1: Write the failing tests** — `tests/test_status_view.py`:

```python
"""Status page threshold logic: every check is a pure function of plain
timestamps/counts, so green/amber/red boundaries are unit-testable."""
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import status_view

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)


def _card(cards, label):
    return next(c for c in cards if c["label"] == label)


def test_heartbeat_thresholds():
    for mins, state in ((10, "green"), (40, "amber"), (90, "red")):
        cards = status_view.checks(
            {"last_capture": NOW - timedelta(minutes=mins)}, NOW)
        assert _card(cards, "Action Heartbeat")["state"] == state


def test_obs_and_calibration_thresholds():
    cards = status_view.checks(
        {"obs_time": NOW - timedelta(minutes=100),
         "calib_computed": NOW - timedelta(hours=40)}, NOW)
    assert _card(cards, "Obs Reading")["state"] == "red"
    assert _card(cards, "Calibration")["state"] == "amber"


def test_feeds_states():
    assert _card(status_view.checks({"dropped_sources": []}, NOW),
                 "Forecast Feeds")["state"] == "green"
    assert _card(status_view.checks({"dropped_sources": ["nws"]}, NOW),
                 "Forecast Feeds")["state"] == "amber"
    assert _card(status_view.checks({"dropped_sources": ["nws", "gem"]}, NOW),
                 "Forecast Feeds")["state"] == "red"


def test_settlements_and_betting_log():
    cards = status_view.checks(
        {"last_settled": date(2026, 7, 17), "betting_rows_today": 6}, NOW)
    assert _card(cards, "Settlements")["state"] == "green"
    assert _card(cards, "Betting Log")["state"] == "green"
    cards = status_view.checks(
        {"last_settled": date(2026, 7, 14), "betting_rows_today": 0}, NOW)
    assert _card(cards, "Settlements")["state"] == "red"
    assert _card(cards, "Betting Log")["state"] == "red"


def test_missing_inputs_read_unknown():
    cards = status_view.checks({}, NOW)
    assert all(c["state"] == "unknown" for c in cards)
    assert all(c["value"] == "No Data" for c in cards)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_status_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'status_view'`.

- [ ] **Step 3: Implement** — create `status_view.py`:

```python
"""Status page — log-derived system health.

`checks` is a pure function of plain timestamps/counts (assembled by
app.load_status + the cached snapshot) so every green/amber/red threshold is
unit-testable; render is dumb. No new credentials: everything comes from data
the dashboard already reads.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

import market_view

GREEN, AMBER, RED, UNKNOWN = "green", "amber", "red", "unknown"
_DOT = {GREEN: "🟢", AMBER: "🟡", RED: "🔴", UNKNOWN: "⚪"}


def _fmt_age(age_min: float) -> str:
    if age_min < 90:
        return f"{age_min:.0f} Min Ago"
    if age_min < 48 * 60:
        return f"{age_min / 60:.1f} H Ago"
    return f"{age_min / 1440:.1f} D Ago"


def _age_card(label: str, age_min, green_lt: float, amber_lt: float,
              tip: str) -> dict:
    if age_min is None:
        return {"label": label, "value": "No Data", "state": UNKNOWN, "tip": tip}
    state = GREEN if age_min < green_lt else AMBER if age_min < amber_lt else RED
    return {"label": label, "value": _fmt_age(age_min), "state": state,
            "tip": tip}


def checks(inputs: dict, now: datetime) -> list[dict]:
    """Health cards from plain inputs; a missing input reads ⚪ unknown rather
    than guessing. Thresholds are the spec's table."""
    def age(dt):
        return None if dt is None else max(0.0, (now - dt).total_seconds() / 60)

    out = [
        _age_card("Action Heartbeat", age(inputs.get("last_capture")), 25, 60,
                  "Minutes since the scheduled Action's last consensus "
                  "capture. Green under 25 min (10-min cadence); red past an "
                  "hour means the Action or its trigger is down."),
        _age_card("Obs Reading", age(inputs.get("obs_time")), 45, 90,
                  "Age of the newest KDFW temperature reading. Red means at "
                  "least one full METAR cycle was missed (the IEM fallback "
                  "kicks in on NWS outages)."),
    ]
    dropped = inputs.get("dropped_sources")
    if dropped is None:
        out.append({"label": "Forecast Feeds", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Whether every forecast source answered on the "
                           "latest snapshot."})
    else:
        state = GREEN if not dropped else AMBER if len(dropped) == 1 else RED
        value = "All Live" if not dropped else f"{len(dropped)} Down"
        tip = ("Every forecast source answered on the latest snapshot."
               if not dropped else "Down: " + ", ".join(dropped) +
               ". The consensus runs on the remaining sources.")
        out.append({"label": "Forecast Feeds", "value": value, "state": state,
                    "tip": tip})
    out.append(_age_card(
        "Calibration", age(inputs.get("calib_computed")), 36 * 60, 72 * 60,
        "Age of the last calibration recompute (~1×/day when healthy). Red "
        "means the model is running on stale bias/sigma/weights."))
    last = inputs.get("last_settled")
    if last is None:
        out.append({"label": "Settlements", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Most recent day with a recorded CLI settlement."})
    else:
        behind = (now.date() - last).days
        state = GREEN if behind <= 1 else AMBER if behind == 2 else RED
        out.append({"label": "Settlements",
                    "value": f"Through {last.strftime('%b %-d')}",
                    "state": state,
                    "tip": "Most recent day with a recorded CLI settlement. "
                           "Green = settled through yesterday."})
    bt = inputs.get("betting_rows_today")
    if bt is None:
        out.append({"label": "Betting Log", "value": "No Data",
                    "state": UNKNOWN,
                    "tip": "Betting-time rows captured for today's slots."})
    else:
        out.append({"label": "Betting Log", "value": f"{bt} Rows Today",
                    "state": GREEN if bt > 0 else RED,
                    "tip": "Betting-time rows captured for today's slots "
                           "(morning low + afternoon high). Zero by midday "
                           "means slot capture is broken."})
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_status_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add status_view.py tests/test_status_view.py
git commit -m "feat: status_view.checks - log-derived health thresholds"
```

---

### Task 9: Status render + loader + nav entry

**Files:**
- Modify: `status_view.py` (append), `app.py` (loader + page + nav)
- Test: `tests/test_status_view.py` (append)

**Interfaces:**
- Consumes: `status_view.checks` (Task 8), `app.load_snapshot_kalshi` (existing; returns `(snap, calib)` where `snap["current"] = {"temp", "time"}` with tz-aware ISO `time`, `snap["dropped_sources"] = list`).
- Produces: `status_view.render(snap, inputs, counts)`; `app.load_status() -> (inputs, counts)`; nav gains "Status" last.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_status_view.py`:

```python
def test_render_smoke_with_and_without_snapshot():
    snap = {"current": {"temp": 93.0, "time": "2026-07-18T11:55-05:00"},
            "dropped_sources": []}
    inputs = {"last_capture": NOW - timedelta(minutes=8),
              "last_settled": date(2026, 7, 17), "betting_rows_today": 4}
    status_view.render(snap, inputs, {"Forecast Log": 170, "Settlements": 64})
    status_view.render(None, {}, {})


def test_snapshot_inputs_extraction():
    snap = {"current": {"temp": 93.0, "time": "2026-07-18T11:55-05:00"},
            "dropped_sources": ["gem"]}
    inputs = status_view.snapshot_inputs(snap)
    assert inputs["dropped_sources"] == ["gem"]
    assert inputs["obs_time"].tzinfo is not None
    assert status_view.snapshot_inputs(None) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_status_view.py -q`
Expected: new tests FAIL with `AttributeError: ... no attribute 'snapshot_inputs'` (or `render`).

- [ ] **Step 3: Implement** — append to `status_view.py`:

```python
def snapshot_inputs(snap: dict | None) -> dict:
    """The live-snapshot-derived check inputs (obs freshness + feed health).
    Pure and total: a missing/partial snapshot contributes nothing rather
    than crashing the page."""
    if not snap:
        return {}
    out: dict = {}
    t = (snap.get("current") or {}).get("time")
    if t:
        try:
            out["obs_time"] = datetime.fromisoformat(t)
        except ValueError:
            pass
    if "dropped_sources" in snap:
        out["dropped_sources"] = snap.get("dropped_sources") or []
    return out


def render(snap: dict | None, inputs: dict, counts: dict) -> None:
    import pandas as pd
    from zoneinfo import ZoneInfo

    from config import TIMEZONE

    market_view._theme_controls()
    st.title("Status")
    st.caption("Log-derived health: every check reads the same data the "
               "dashboard already loads — no extra credentials or probes.")
    now = datetime.now(ZoneInfo(TIMEZONE))
    merged = dict(inputs)
    merged.update(snapshot_inputs(snap))
    cards = checks(merged, now)
    with st.container(key="metrics2_status"):
        c = st.columns(3)
    for i, card in enumerate(cards):
        c[i % 3].markdown(market_view.metric_card(
            card["label"], f'{_DOT[card["state"]]} {card["value"]}',
            card["tip"]), unsafe_allow_html=True)
    if counts:
        st.subheader("Log Sizes")
        market_view._html_table(pd.DataFrame(
            [{"Log": k, "Rows": str(v)} for k, v in sorted(counts.items())]))
        st.caption("Row counts of the persisted data logs. Steady growth is "
                   "healthy; a frozen count means the Action stopped writing.")
```

Then in `app.py`: add `import status_view` to the top-level imports, and after `load_lab`:

```python
@st.cache_data(ttl=60, show_spinner=False)
def load_status():
    """Plain timestamps/counts for the Status page's checks. Each read is
    best-effort — a missing log yields an 'unknown' card, never a crash."""
    from datetime import date, datetime
    inputs: dict = {}
    counts: dict = {}

    def _dt(iso):
        # calibration's `computed` stamp is naive; the Action runner writes it
        # in UTC, so read naive stamps as UTC (±5h skew vs a local recompute
        # is immaterial against the 36h amber threshold).
        try:
            from datetime import timezone
            d = datetime.fromisoformat(iso)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    try:
        import consensus_log
        rows = consensus_log.load()
        counts["Consensus History"] = len(rows)
        cli = [r for r in rows if r.get("basis") == "cli"] or rows
        if cli:
            inputs["last_capture"] = _dt(cli[-1].get("captured_at"))
    except Exception:
        pass
    try:
        counts["Forecast Log"] = len(forecast_log.load())
    except Exception:
        pass
    try:
        import betting_log
        rows = betting_log.load()
        counts["Betting Log"] = len(rows)
        today = date.today().isoformat()
        inputs["betting_rows_today"] = sum(
            1 for r in rows if r.get("target_date") == today)
    except Exception:
        pass
    try:
        import settlements
        rows = settlements.load()
        counts["Settlements"] = len(rows)
        days = [date.fromisoformat(r["target_date"]) for r in rows
                if r.get("basis") == "cli" and r.get("target_date")]
        if days:
            inputs["last_settled"] = max(days)
    except Exception:
        pass
    try:
        import calibration_history
        counts["Calibration History"] = len(calibration_history.load())
    except Exception:
        pass
    try:
        calib = calibration.get(refresh=True) or {}
        inputs["calib_computed"] = _dt(calib.get("computed"))
    except Exception:
        pass
    return inputs, counts


def status_page():
    snap = None
    try:
        snap, _calib = load_snapshot_kalshi()
    except Exception:
        snap = None
    inputs, counts = load_status()
    status_view.render(snap, inputs, counts)
```

and the nav entry last:

```python
    st.Page(journal_page, title="Journal"),
    st.Page(status_page, title="Status"),
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_status_view.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add status_view.py app.py tests/test_status_view.py
git commit -m "feat: Status page - log-derived health cards + log sizes"
```

---

### Task 10: Full-suite verification + push

**Files:** none new.

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest -q --ignore=tests/test_kalshi_auth.py --ignore=tests/test_kalshi_portfolio.py`
Expected: everything passes except the 4 known `tests/test_bet_view.py` cryptography failures. No new failures.

- [ ] **Step 2: Python-compile the app entry (no local streamlit run possible)**

Run: `python3 -m py_compile app.py journal_view.py lab_view.py status_view.py`
Expected: silent success.

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Post-deploy eyeball note**

The three pages need a browser check on the deployed site (no local `streamlit run`): nav shows 8 pages; Journal cards render with themed surfaces; Lab charts tap-to-pin on mobile; Status cards show 🟢 states.
