# Kalshi Autonomous Trading Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully autonomous, parameter-driven loop that buys and (on stop-loss/reversal) sells Kalshi KDFW temperature contracts, shipping in shadow mode first.

**Architecture:** A dedicated market-hours GitHub Actions cron runs `trader.py`, which reconciles truth from the Kalshi account, consults the existing model/kelly decision engine, then places marketable-limit orders through a single isolated write client. All parameters and audit logs live on a dedicated `trade-data` GitHub branch, edited live from a new Streamlit control page. Pure decision logic is isolated from all IO so it is unit-testable without a network.

**Tech Stack:** Python 3.11, Streamlit, `requests`, `cryptography` (RSA-PSS signing, must stay ≤38.x), pytest. Reuses `model.py`, `kelly.py`, `sources/kalshi.py`, `sources/kalshi_portfolio.py`, `notify.py`.

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-07-24-kalshi-auto-trade-design.md` — the authority; this plan implements it.
- **Ships in `shadow` mode; `kill_switch` defaults engaged (True = trading disabled).** No real order may be placed until a human sets `kill_switch=False` AND `mode="live"`.
- **`sources/kalshi_auth.py` stays read-only and untouched.** All order placement lives only in `sources/kalshi_orders.py`.
- **Private key material is never logged, printed, or placed in an exception message** (same discipline as `kalshi_auth.py`).
- **`cryptography` must stay ≤38.x** (local env gap; see memory `local-test-env-gaps`).
- **Source of truth for holdings is always the reconciled Kalshi account, never the local log.**
- **Entry is agreement-based, not edge-based:** trade only when model consensus and market-implied center agree within `agreement_tol` °F.
- **Stop-loss is measured on the ASK referenced to entry ask; the exit fills at the bid** (trigger-on-ask, fill-at-bid).
- **No take-profit** — winners hold to Kalshi settlement.
- Station tz constants: `config.TIMEZONE = "America/Chicago"`; climate day via `settlement.climate_day_of(now)`.
- Run the full suite with `python -m pytest -q` from the repo root; it must stay green (662+ tests today).

---

### Task 1: Trading parameters & market window (pure)

**Files:**
- Create: `trade_params.py`
- Test: `tests/test_trade_params.py`

**Interfaces:**
- Consumes: `config.TIMEZONE`.
- Produces:
  - `DEFAULT_PARAMS: dict` — canonical schema + safe defaults.
  - `merge_params(stored: dict | None) -> dict` — defaults overlaid with a stored dict, unknown keys dropped, types coerced.
  - `within_market_window(now: datetime, params: dict) -> bool` — is `now` (tz-aware, any tz) inside `[market_open, market_close]` in `config.TIMEZONE`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_params.py
from datetime import datetime
from zoneinfo import ZoneInfo

import trade_params as tp


def test_defaults_are_safe():
    d = tp.DEFAULT_PARAMS
    assert d["kill_switch"] is True          # engaged = trading disabled
    assert d["mode"] == "shadow"
    assert d["max_price"] == 0.94
    assert d["min_resolved"] == 0.70
    assert d["agreement_tol"] == 1.0
    assert d["per_market_cap"] == 0.50
    assert d["max_open_per_variable"] == 1


def test_merge_fills_defaults_and_drops_unknown():
    merged = tp.merge_params({"max_price": 0.80, "bogus": 1})
    assert merged["max_price"] == 0.80       # override kept
    assert merged["min_resolved"] == 0.70    # default filled
    assert "bogus" not in merged             # unknown dropped


def test_merge_coerces_types():
    merged = tp.merge_params({"kill_switch": "false", "per_market_cap": "0.5"})
    assert merged["kill_switch"] is False
    assert merged["per_market_cap"] == 0.5


def test_merge_none_is_all_defaults():
    assert tp.merge_params(None) == tp.DEFAULT_PARAMS


def test_market_window_true_inside_false_outside():
    p = tp.merge_params({"market_open": "06:00", "market_close": "20:00"})
    ct = ZoneInfo("America/Chicago")
    assert tp.within_market_window(datetime(2026, 7, 24, 12, 0, tzinfo=ct), p) is True
    assert tp.within_market_window(datetime(2026, 7, 24, 5, 0, tzinfo=ct), p) is False
    assert tp.within_market_window(datetime(2026, 7, 24, 21, 0, tzinfo=ct), p) is False


def test_market_window_converts_utc():
    p = tp.merge_params({})
    utc = ZoneInfo("UTC")
    # 12:00 UTC == 07:00 CDT (inside default 06:00-20:00)
    assert tp.within_market_window(datetime(2026, 7, 24, 12, 0, tzinfo=utc), p) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_params.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trade_params'`.

- [ ] **Step 3: Write minimal implementation**

```python
# trade_params.py
"""Trading parameter schema, defaults, coercion, and the market-hours window.

Pure — no IO, no network. `DEFAULT_PARAMS` is the single source of truth for
what a trade-state document may contain; `merge_params` normalizes a stored
document against it. Ships SAFE: kill switch engaged, shadow mode.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import config

# kill_switch True == ENGAGED == trading disabled. Ships engaged.
DEFAULT_PARAMS: dict = {
    "kill_switch": True,
    "mode": "shadow",                     # "shadow" | "live"
    "enabled_variables": ["high", "low"],
    "min_resolved": 0.70,                 # fraction 0-1
    "agreement_tol": 1.0,                 # °F
    "max_price": 0.94,                    # skip asks >= this
    "min_price": 0.10,                    # skip asks < this
    "kelly_fraction": 0.25,
    "per_market_cap": 0.50,               # dollars per bracket
    "max_open_per_variable": 1,
    "daily_loss_cap": -5.00,              # dollars; None disables
    "stop_loss": 0.20,                    # ask drop from entry_ask that exits
    "slippage_cap": 0.02,                 # dollars through the ask a buy may pay
    "market_open": "06:00",               # HH:MM local (config.TIMEZONE)
    "market_close": "20:00",
}

_BOOL = {"kill_switch"}
_FLOAT = {"min_resolved", "agreement_tol", "max_price", "min_price",
          "kelly_fraction", "per_market_cap", "stop_loss", "slippage_cap"}
_INT = {"max_open_per_variable"}


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def merge_params(stored: dict | None) -> dict:
    """DEFAULT_PARAMS overlaid with `stored`; unknown keys dropped, types coerced."""
    out = dict(DEFAULT_PARAMS)
    for k, v in (stored or {}).items():
        if k not in DEFAULT_PARAMS:
            continue
        if k in _BOOL:
            out[k] = _coerce_bool(v)
        elif k in _FLOAT:
            out[k] = None if (k == "daily_loss_cap" and v is None) else float(v)
        elif k in _INT:
            out[k] = int(v)
        elif k == "daily_loss_cap":
            out[k] = None if v is None else float(v)
        else:
            out[k] = v
    return out


def _hhmm(s: str) -> time:
    hh, mm = (int(x) for x in str(s).split(":", 1))
    return time(hh, mm)


def within_market_window(now: datetime, params: dict) -> bool:
    """True when `now` (tz-aware) falls inside [market_open, market_close] in
    config.TIMEZONE."""
    local = now.astimezone(ZoneInfo(config.TIMEZONE))
    return _hhmm(params["market_open"]) <= local.time() <= _hhmm(params["market_close"])
```

Note: `merge_params` handles `daily_loss_cap` (nullable float) before the generic `_FLOAT` branch by keeping it out of `_FLOAT`; it is resolved in the explicit `elif k == "daily_loss_cap"` branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trade_params.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add trade_params.py tests/test_trade_params.py
git commit -m "feat: trading parameter schema + market-hours window"
```

---

### Task 2: GitHub-backed trade state store

**Files:**
- Create: `trade_state.py`
- Test: `tests/test_trade_state.py`

**Interfaces:**
- Consumes: `trade_params.merge_params`.
- Produces (all accept an injectable `transport` for tests; default hits the GitHub contents API):
  - `load_state(transport=None) -> dict` — merged params from `trade_state.json` (defaults if the file is absent).
  - `save_state(params: dict, transport=None) -> None` — PUT `trade_state.json` (create-or-update via sha).
  - `load_runtime(transport=None) -> dict` — `trade_runtime.json` (`{}` if absent). Keys: `halt_day` (ISO date the daily-loss halt engaged), `entries` (`{ticker: {"entry_ask": float, "side": str, "ts": iso}}`).
  - `save_runtime(runtime: dict, transport=None) -> None`.
  - `append_jsonl(path: str, record: dict, transport=None) -> None` — append one line to a `.jsonl` file on the branch.
  - `GitHubTransport` — default transport class wrapping GET/PUT of the contents API.

**Storage model:** dedicated branch `trade-data` (env `TRADE_GH_BRANCH`, default `trade-data`), repo `TRADE_GH_REPO`, token `TRADE_GH_TOKEN`. Distinct from the `data` branch so `log.yml`'s force-push can never clobber it. `trade_state.json` is written only by the control page; the cron reads it. `trade_runtime.json` and `trade_log.jsonl` are written only by the cron.

- [ ] **Step 1: Write the failing test** (fake in-memory transport)

```python
# tests/test_trade_state.py
import json

import trade_state as ts


class FakeTransport:
    """In-memory stand-in for the GitHub contents API, keyed by path."""
    def __init__(self):
        self.files = {}       # path -> (text, sha)
        self._n = 0

    def get(self, path):
        return self.files.get(path)          # (text, sha) or None

    def put(self, path, text, sha):
        if path in self.files and self.files[path][1] != sha:
            raise ts.ConflictError(path)
        self._n += 1
        self.files[path] = (text, f"sha{self._n}")


def test_load_state_absent_returns_defaults():
    t = FakeTransport()
    st = ts.load_state(transport=t)
    assert st["kill_switch"] is True
    assert st["mode"] == "shadow"


def test_save_then_load_roundtrip():
    t = FakeTransport()
    ts.save_state({"kill_switch": False, "mode": "live", "max_price": 0.9}, transport=t)
    st = ts.load_state(transport=t)
    assert st["kill_switch"] is False
    assert st["mode"] == "live"
    assert st["max_price"] == 0.9
    assert st["min_resolved"] == 0.70      # default filled by merge_params


def test_runtime_roundtrip():
    t = FakeTransport()
    assert ts.load_runtime(transport=t) == {}
    ts.save_runtime({"halt_day": "2026-07-24", "entries": {}}, transport=t)
    assert ts.load_runtime(transport=t)["halt_day"] == "2026-07-24"


def test_append_jsonl_accumulates():
    t = FakeTransport()
    ts.append_jsonl("trade_log.jsonl", {"a": 1}, transport=t)
    ts.append_jsonl("trade_log.jsonl", {"a": 2}, transport=t)
    text, _sha = t.get("trade_log.jsonl")
    lines = [json.loads(x) for x in text.splitlines() if x]
    assert [r["a"] for r in lines] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trade_state'`.

- [ ] **Step 3: Write minimal implementation**

```python
# trade_state.py
"""GitHub-branch-backed store for trading parameters, runtime, and the audit log.

All trade documents live on a dedicated branch (default `trade-data`), read and
written through the GitHub contents API so both the Streamlit control page and
the headless cron share one source without any git operations. Kept off the
`data` branch so log.yml's force-push cannot clobber it.

`trade_state.json`  — params; written only by the control page, read by the cron.
`trade_runtime.json`— cron-owned mutable runtime (halt day, per-position entry_ask).
`trade_log.jsonl`   — append-only audit.
"""
from __future__ import annotations

import base64
import json
import os

import requests

import trade_params

STATE_PATH = "trade_state.json"
RUNTIME_PATH = "trade_runtime.json"
LOG_PATH = "trade_log.jsonl"


class ConflictError(RuntimeError):
    """A PUT lost the optimistic-concurrency race (stale sha)."""


class GitHubTransport:
    """GET/PUT a file on the trade-data branch via the contents API."""

    def __init__(self):
        self.repo = os.environ.get("TRADE_GH_REPO", "")
        self.branch = os.environ.get("TRADE_GH_BRANCH", "trade-data")
        self.token = os.environ.get("TRADE_GH_TOKEN", "")

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _url(self, path):
        return f"https://api.github.com/repos/{self.repo}/contents/{path}"

    def get(self, path):
        """Return (text, sha) or None if the file does not exist."""
        r = requests.get(self._url(path), params={"ref": self.branch},
                         headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        j = r.json()
        text = base64.b64decode(j["content"]).decode("utf-8")
        return text, j["sha"]

    def put(self, path, text, sha):
        body = {
            "message": f"trade: update {path}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        r = requests.put(self._url(path), json=body, headers=self._headers(),
                         timeout=15)
        if r.status_code == 409:
            raise ConflictError(path)
        r.raise_for_status()


def _t(transport):
    return transport or GitHubTransport()


def _read_json(path, transport):
    got = _t(transport).get(path)
    if not got:
        return None, None
    text, sha = got
    return (json.loads(text) if text.strip() else None), sha


def load_state(transport=None) -> dict:
    data, _sha = _read_json(STATE_PATH, transport)
    return trade_params.merge_params(data)


def save_state(params: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(STATE_PATH)
    sha = got[1] if got else None
    t.put(STATE_PATH, json.dumps(params, indent=2, sort_keys=True), sha)


def load_runtime(transport=None) -> dict:
    data, _sha = _read_json(RUNTIME_PATH, transport)
    return data or {}


def save_runtime(runtime: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(RUNTIME_PATH)
    sha = got[1] if got else None
    t.put(RUNTIME_PATH, json.dumps(runtime, indent=2, sort_keys=True), sha)


def append_jsonl(path: str, record: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(path)
    text, sha = (got[0], got[1]) if got else ("", None)
    text = (text + json.dumps(record) + "\n") if text else json.dumps(record) + "\n"
    t.put(path, text, sha)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trade_state.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add trade_state.py tests/test_trade_state.py
git commit -m "feat: GitHub-branch-backed trade state/runtime/log store"
```

---

### Task 3: Decision logic — gates, agreement, bracket selection (pure)

**Files:**
- Create: `trade_logic.py`
- Test: `tests/test_trade_logic_entry.py`

**Interfaces:**
- Consumes: `model.prob_for_strike`, `model.displayed_resolved`.
- Produces:
  - `market_center(implied: dict | None) -> float | None` — the market-implied temperature (`implied["ev"]`).
  - `gates_clear(var_snap: dict) -> tuple[bool, str]` — False (+reason) when `low_forming`, unpeaked-high, `front_widened`, or `convective_widened` block entry.
  - `entry_allowed(var_snap, implied, params, variable) -> tuple[bool, str]` — resolved ≥ floor, gates clear, agreement within tol.
  - `select_bracket(contracts: list[dict], agreed_temp: float, variable: str, tie_margin: float = 0.5) -> dict | None` — the target `between` contract, applying the direction tie-break.

**Contract dict shape** (from `kalshi.fetch_contracts`): `ticker, label, strike_type, floor, cap, yes_bid, yes_ask, no_bid, no_ask`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_logic_entry.py
import trade_logic as tl


def _c(floor, cap, ticker):
    return {"ticker": ticker, "strike_type": "between", "floor": floor, "cap": cap,
            "yes_ask": 0.5, "yes_bid": 0.45, "no_ask": 0.55, "no_bid": 0.5}


def test_market_center_reads_ev():
    assert tl.market_center({"ev": 99.4}) == 99.4
    assert tl.market_center(None) is None


def test_entry_blocked_below_resolved_floor():
    snap = {"resolved": 0.5, "consensus": 99.0, "low_forming": False,
            "peak_locked": True}
    ok, reason = tl.entry_allowed(snap, {"ev": 99.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is False and "resolved" in reason.lower()


def test_entry_blocked_when_disagree():
    snap = {"resolved": 0.9, "consensus": 99.0, "low_forming": False,
            "peak_locked": True}
    ok, reason = tl.entry_allowed(snap, {"ev": 101.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is False and "agree" in reason.lower()


def test_entry_blocked_by_low_forming_gate():
    snap = {"resolved": 0.9, "consensus": 75.0, "low_forming": True,
            "peak_locked": False}
    ok, reason = tl.entry_allowed(snap, {"ev": 75.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "low")
    assert ok is False and "forming" in reason.lower()


def test_entry_allowed_when_all_clear():
    snap = {"resolved": 0.9, "consensus": 99.0, "low_forming": False,
            "peak_locked": True, "front_widened": False, "convective_widened": False}
    ok, _ = tl.entry_allowed(snap, {"ev": 99.4},
                             {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is True


def test_bracket_direct_hit():
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 98.4, "high")
    assert got["ticker"] == "A"


def test_bracket_tie_high_picks_upper():
    # 99.5 sits between 98-99 and 100-101; a HIGH still climbing buys upper.
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 99.5, "high")
    assert got["ticker"] == "B"


def test_bracket_tie_low_picks_lower():
    # mirror: a forming LOW that can still fall buys the lower bracket.
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 99.5, "low")
    assert got["ticker"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_logic_entry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trade_logic'`.

- [ ] **Step 3: Write minimal implementation**

```python
# trade_logic.py
"""Pure trading decisions: entry gating, agreement, bracket selection, sizing,
and exit rules. No network, no Streamlit — every function takes plain data so it
is unit-testable. IO and orchestration live in trader.py.
"""
from __future__ import annotations

import model


def market_center(implied: dict | None) -> float | None:
    """The market's own implied temperature, or None when no market is priced."""
    return None if not implied else implied.get("ev")


def gates_clear(var_snap: dict) -> tuple[bool, str]:
    """Model safety gates that block a NEW entry. Mirrors the dashboard's own
    'wait' states so the trader never buys into a forming/unsettled extreme."""
    if var_snap.get("low_forming"):
        return False, "low still forming"
    if var_snap.get("front_widened"):
        return False, "front risk widened"
    if var_snap.get("convective_widened"):
        return False, "convective risk widened"
    return True, ""


def entry_allowed(var_snap: dict, implied: dict | None, params: dict,
                  variable: str) -> tuple[bool, str]:
    """Resolved floor + safety gates + model/market agreement. Returns (ok, reason)."""
    resolved = var_snap.get("resolved", 0.0)
    if resolved < params["min_resolved"]:
        return False, f"resolved {resolved:.0%} < {params['min_resolved']:.0%}"
    ok, reason = gates_clear(var_snap)
    if not ok:
        return False, reason
    mkt = market_center(implied)
    if mkt is None:
        return False, "no market center"
    model_c = var_snap.get("consensus")
    if model_c is None or abs(model_c - mkt) > params["agreement_tol"]:
        return False, f"model {model_c} vs market {mkt} disagree > {params['agreement_tol']}°F"
    return True, ""


def _between(contracts):
    return [c for c in contracts if c.get("strike_type") == "between"
            and c.get("floor") is not None and c.get("cap") is not None]


def select_bracket(contracts: list[dict], agreed_temp: float, variable: str,
                   tie_margin: float = 0.5) -> dict | None:
    """The target 'between' bracket for `agreed_temp`.

    Direct hit → the bracket whose [floor, cap] contains the temp. Near-tie
    (the temp sits in a gap between two brackets, or within `tie_margin` of a
    bracket edge that faces a neighbor) → buy in the direction the variable can
    still move: HIGH → the upper bracket, LOW → the lower. That keeps an adverse
    move heading toward the position (sellable via stop-loss) instead of settling
    it to $0 instantly."""
    br = sorted(_between(contracts), key=lambda c: c["floor"])
    if not br:
        return None

    # Candidate brackets near the agreed temp (contains it, or an edge within margin).
    def near(c):
        return (c["floor"] - tie_margin) <= agreed_temp <= (c["cap"] + tie_margin)

    cands = [c for c in br if near(c)]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    # Ambiguous straddle: direction tie-break.
    cands.sort(key=lambda c: c["floor"])
    return cands[-1] if variable == "high" else cands[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trade_logic_entry.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add trade_logic.py tests/test_trade_logic_entry.py
git commit -m "feat: entry gating, agreement, and bracket-selection tie-break"
```

---

### Task 4: Decision logic — sizing & exits (pure)

**Files:**
- Modify: `trade_logic.py`
- Test: `tests/test_trade_logic_exit.py`

**Interfaces:**
- Consumes: `kelly.best_side`, `kelly.optimal_size`, `kelly.cost_to_buy`, `model.prob_for_strike`.
- Produces:
  - `size_bracket(contract, var_snap, orderbook, bankroll, params) -> dict` — `{"side","contracts","avg_price","stake","note"}`; `contracts=0` when un-sizable (price bounds, no edge, cap = 0 contracts).
  - `stop_loss_hit(entry_ask: float, current_ask: float, stop_loss: float) -> bool` — `current_ask <= entry_ask - stop_loss`.
  - `should_exit(position, current_ask, target_ticker, gates_ok, params) -> tuple[bool, str]` — stop-loss OR reversal (held ticker no longer the target, or a gate fired).
  - `reentry_allowed(stopped_ticker: str | None, target_ticker: str) -> bool` — only into a *different* bracket than the one just stopped.

**`position` shape** (built by trader from reconciliation + runtime): `{"ticker","side","count","entry_ask"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_logic_exit.py
import trade_logic as tl


def test_stop_loss_trigger_on_ask():
    # entry ask 0.60, stop 0.20 -> exit when ask <= 0.40
    assert tl.stop_loss_hit(0.60, 0.40, 0.20) is True
    assert tl.stop_loss_hit(0.60, 0.41, 0.20) is False


def test_stop_loss_ignores_spread_at_entry():
    # ask still 0.60 right after fill -> not underwater despite a low bid
    assert tl.stop_loss_hit(0.60, 0.60, 0.20) is False


def test_should_exit_on_stop_loss():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.39, target_ticker="A",
                                 gates_ok=True, params={"stop_loss": 0.20})
    assert out is True and "stop" in reason.lower()


def test_should_exit_on_reversal_new_target():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.60, target_ticker="B",
                                 gates_ok=True, params={"stop_loss": 0.20})
    assert out is True and "revers" in reason.lower()


def test_should_exit_on_gate_fire():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.60, target_ticker="A",
                                 gates_ok=False, params={"stop_loss": 0.20})
    assert out is True and "gate" in reason.lower()


def test_hold_when_nothing_triggers():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, _ = tl.should_exit(pos, current_ask=0.60, target_ticker="A",
                            gates_ok=True, params={"stop_loss": 0.20})
    assert out is False


def test_reentry_only_into_different_bracket():
    assert tl.reentry_allowed("A", "B") is True
    assert tl.reentry_allowed("A", "A") is False
    assert tl.reentry_allowed(None, "A") is True


def test_size_bracket_respects_price_ceiling():
    c = {"ticker": "A", "strike_type": "between", "floor": 98, "cap": 99,
         "yes_ask": 0.96, "no_ask": 0.06}
    snap = {"probabilities": {"98": 0.5, "99": 0.5}}
    out = tl.size_bracket(c, snap, {"yes": [], "no": []}, bankroll=10.0,
                          params={"max_price": 0.94, "min_price": 0.10,
                                  "kelly_fraction": 0.25, "per_market_cap": 0.50})
    assert out["contracts"] == 0
    assert "price" in out["note"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_logic_exit.py -q`
Expected: FAIL — `AttributeError: module 'trade_logic' has no attribute 'stop_loss_hit'`.

- [ ] **Step 3: Add the implementation to `trade_logic.py`**

Append to `trade_logic.py`:

```python
import kelly


def stop_loss_hit(entry_ask: float, current_ask: float, stop_loss: float) -> bool:
    """Ask-referenced stop: exit when the current ask has fallen `stop_loss`
    below the entry ask. Never trips on the bid/spread at fill time."""
    return current_ask <= entry_ask - stop_loss


def should_exit(position: dict, current_ask, target_ticker, gates_ok: bool,
                params: dict) -> tuple[bool, str]:
    """Exit a held position on stop-loss OR signal reversal (a gate fired, or the
    target bracket is no longer this one). No take-profit — winners hold."""
    if not gates_ok:
        return True, "reversal: safety gate fired"
    if target_ticker is not None and target_ticker != position["ticker"]:
        return True, f"reversal: target moved to {target_ticker}"
    if current_ask is not None and stop_loss_hit(position["entry_ask"], current_ask,
                                                 params["stop_loss"]):
        return True, f"stop-loss: ask {current_ask:.2f} <= entry {position['entry_ask']:.2f} - {params['stop_loss']:.2f}"
    return False, ""


def reentry_allowed(stopped_ticker: str | None, target_ticker: str) -> bool:
    """After a stop-loss, only re-enter when the signal has genuinely flipped to a
    DIFFERENT bracket (prevents churning back into the just-stopped one)."""
    return stopped_ticker != target_ticker


def size_bracket(contract: dict, var_snap: dict, orderbook: dict, bankroll: float,
                 params: dict) -> dict:
    """Fractional-Kelly size for `contract`, clamped by per_market_cap and the
    price bounds. Returns {"side","contracts","avg_price","stake","note"}."""
    probs = var_snap.get("probabilities") or {}
    p = model.prob_for_strike(probs, contract["strike_type"],
                              contract.get("floor"), contract.get("cap"))
    if p is None:
        return {"side": "", "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": "model abstains (open tail)"}
    pick = kelly.best_side(p, contract.get("yes_ask"), contract.get("no_ask"))
    if pick is None:
        return {"side": "", "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": "no edge on either side"}
    side, win, ask = pick
    if ask >= params["max_price"] or ask < params["min_price"]:
        return {"side": side, "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": f"price {ask:.2f} outside [{params['min_price']},{params['max_price']})"}
    from sources import kalshi as _k
    ladder = _k.ask_ladder(orderbook, side)   # `orderbook` is the normalized book
    sizing = kelly.optimal_size(ladder, win, bankroll, params["kelly_fraction"], side=side)
    n = sizing.contracts
    # Clamp to the per-market dollar cap.
    while n > 0 and (kelly.cost_to_buy(ladder, n) or 1e9) > params["per_market_cap"]:
        n -= 1
    if n <= 0:
        return {"side": side, "contracts": 0, "avg_price": None, "stake": 0.0,
                "note": "cap or book leaves 0 contracts"}
    stake = kelly.cost_to_buy(ladder, n)
    return {"side": side, "contracts": n, "avg_price": stake / n, "stake": stake,
            "note": sizing.note}
```

Note: the trader (Task 6) passes the normalized order book from `sources.kalshi.fetch_orderbook`; `size_bracket` calls `sources.kalshi.ask_ladder(orderbook, side)` to build the ascending ask ladder before sizing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trade_logic_exit.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add trade_logic.py tests/test_trade_logic_exit.py
git commit -m "feat: fractional-Kelly sizing + ask-referenced stop-loss/reversal exits"
```

---

### Task 5: Order write client + position reads

**Files:**
- Create: `sources/kalshi_orders.py`
- Modify: `sources/kalshi_portfolio.py` (add `positions`, `resting_orders`)
- Test: `tests/test_kalshi_orders.py`

**Interfaces:**
- Consumes: `sources.kalshi_auth` (signing internals), `trade_params`.
- Produces:
  - `signed_request(method, path, body=None, timeout=10) -> dict` — RSA-PSS-signed POST/DELETE against the trade API. **The only write-capable request path in the codebase.**
  - `place_order(*, ticker, side, action, count, price, client_order_id, mode, transport=None) -> dict` — in `mode="shadow"` logs + returns a synthetic ack with no network call; in `mode="live"` POSTs `/portfolio/orders`.
  - `cancel_order(order_id, mode, transport=None) -> dict`.
  - `sources.kalshi_portfolio.positions(fetch=None) -> list[dict]` — normalized `{ticker, side, count, variable}` for the Dallas temp series (read via `signed_get /portfolio/positions`).
  - `sources.kalshi_portfolio.resting_orders(fetch=None) -> list[dict]` — normalized `{ticker, order_id, side, action, count}`.

**Order-body schema note:** the exact Kalshi `POST /portfolio/orders` field names (`ticker`, `client_order_id`, `side`, `action`, `count`, `type`, `yes_price`/`no_price` in **whole cents 1-99**) MUST be confirmed against the live Kalshi Trade API v2 docs during this task before the live path is exercised. The test pins the shape this plan assumes; if the real API differs, update both the test and `_order_body` together.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kalshi_orders.py
import pytest

from sources import kalshi_orders as ko


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, timeout=10):
        self.calls.append((method, path, body))
        return {"order": {"order_id": "OID1", "status": "resting"}}


def test_shadow_places_no_network_call():
    t = FakeTransport()
    ack = ko.place_order(ticker="KXHIGHTDAL-26JUL24-B99", side="yes", action="buy",
                         count=1, price=0.60, client_order_id="cid-1",
                         mode="shadow", transport=t)
    assert t.calls == []                      # no network in shadow
    assert ack["shadow"] is True
    assert ack["client_order_id"] == "cid-1"


def test_live_posts_order_with_cents_price():
    t = FakeTransport()
    ack = ko.place_order(ticker="KXHIGHTDAL-26JUL24-B99", side="yes", action="buy",
                         count=2, price=0.60, client_order_id="cid-2",
                         mode="live", transport=t)
    method, path, body = t.calls[0]
    assert method == "POST" and path == "/portfolio/orders"
    assert body["count"] == 2
    assert body["yes_price"] == 60           # dollars -> whole cents
    assert body["client_order_id"] == "cid-2"
    assert ack["order"]["order_id"] == "OID1"


def test_no_price_used_for_no_side():
    t = FakeTransport()
    ko.place_order(ticker="X", side="no", action="buy", count=1, price=0.30,
                   client_order_id="c", mode="live", transport=t)
    _m, _p, body = t.calls[0]
    assert body["no_price"] == 30 and "yes_price" not in body


def test_positions_normalizes_series_only():
    from sources import kalshi_portfolio as kp

    def fake_get(path, params=None):
        return {"market_positions": [
            {"ticker": "KXHIGHTDAL-26JUL24-B99", "position": 2},
            {"ticker": "KXOTHER-1", "position": 5},
        ]}

    out = kp.positions(fetch=fake_get)
    assert len(out) == 1
    assert out[0]["ticker"].startswith("KXHIGHTDAL")
    assert out[0]["count"] == 2
    assert out[0]["variable"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kalshi_orders.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.kalshi_orders'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sources/kalshi_orders.py
"""WRITE-capable Kalshi client — the ONLY module that can place or cancel orders.

Deliberately separate from sources/kalshi_auth.py (which stays read-only) so the
dangerous capability lives in one auditable place. Signs with the same RSA-PSS
scheme. In shadow mode place_order/cancel_order make NO network call — they log
and return a synthetic ack — so the whole pipeline can run against live signals
without moving money. Private key material is never logged or put in an exception.
"""
from __future__ import annotations

import time
import uuid

import requests

from sources import kalshi_auth


def signed_request(method: str, path: str, body: dict | None = None,
                   timeout: int = 10) -> dict:
    """RSA-PSS-signed request to the trade API. `path` is the sub-path after the
    API prefix, e.g. '/portfolio/orders'. Returns parsed JSON; raises for HTTP
    errors. This is the sole write path in the codebase."""
    key_id, private_key = kalshi_auth.load_credentials()
    full_path = kalshi_auth.API_PREFIX + path
    headers = kalshi_auth.auth_headers(method, full_path, key_id, private_key)
    headers["Content-Type"] = "application/json"
    resp = requests.request(method, kalshi_auth.HOST + full_path, json=body,
                            headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _order_body(ticker, side, action, count, price, client_order_id) -> dict:
    """Kalshi order body. Price is dollars 0-1 -> whole cents 1-99.
    NOTE: field shape must be confirmed against the live Trade API v2 docs."""
    cents = int(round(price * 100))
    body = {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": side,               # "yes" | "no"
        "action": action,           # "buy" | "sell"
        "count": count,
        "type": "limit",
    }
    body["yes_price" if side == "yes" else "no_price"] = cents
    return body


def place_order(*, ticker, side, action, count, price, client_order_id, mode,
                transport=None) -> dict:
    """Place one marketable-limit order. Shadow mode → log + synthetic ack, no
    network. `transport` is injectable for tests (defaults to signed_request)."""
    body = _order_body(ticker, side, action, count, price, client_order_id)
    if mode != "live":
        print(f"[SHADOW] would {action} {count} {side} {ticker} @ {price:.2f} "
              f"(cid={client_order_id})")
        return {"shadow": True, "client_order_id": client_order_id, "body": body}
    call = transport or signed_request
    return call("POST", "/portfolio/orders", body)


def cancel_order(order_id: str, mode: str, transport=None) -> dict:
    if mode != "live":
        print(f"[SHADOW] would cancel {order_id}")
        return {"shadow": True, "order_id": order_id}
    call = transport or signed_request
    return call("DELETE", f"/portfolio/orders/{order_id}")


def new_client_order_id(ticker: str, day_iso: str, intent: str, bucket: str) -> str:
    """Deterministic idempotency key for one (ticker, day, intent, run-bucket).
    A retried/overlapping run yields the same id, so Kalshi rejects the dup."""
    return f"{ticker}:{day_iso}:{intent}:{bucket}"
```

Add to `sources/kalshi_portfolio.py`:

```python
def positions(fetch=None) -> list[dict]:
    """Open positions for the Dallas temp series, normalized to
    {ticker, side, count, variable}. Read-only GET /portfolio/positions."""
    fetch = fetch or kalshi_auth.signed_get
    try:
        data = fetch("/portfolio/positions", None) or {}
    except Exception:
        return []
    out = []
    for mp in data.get("market_positions") or []:
        ticker = mp.get("ticker", "")
        var = variable_of(ticker)
        if var is None:
            continue
        count = mp.get("position") or 0
        if not count:
            continue
        out.append({"ticker": ticker, "side": "yes" if count > 0 else "no",
                    "count": abs(int(count)), "variable": var})
    return out


def resting_orders(fetch=None) -> list[dict]:
    """Open (resting) orders for the Dallas temp series, normalized to
    {ticker, order_id, side, action, count}. Read-only GET /portfolio/orders."""
    fetch = fetch or kalshi_auth.signed_get
    try:
        data = fetch("/portfolio/orders", {"status": "resting"}) or {}
    except Exception:
        return []
    out = []
    for o in data.get("orders") or []:
        if variable_of(o.get("ticker", "")) is None:
            continue
        out.append({"ticker": o.get("ticker"), "order_id": o.get("order_id"),
                    "side": o.get("side"), "action": o.get("action"),
                    "count": o.get("remaining_count") or o.get("count")})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kalshi_orders.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify the read client is still untouched**

Run: `git diff --stat sources/kalshi_auth.py`
Expected: no output (file unchanged).

- [ ] **Step 6: Commit**

```bash
git add sources/kalshi_orders.py sources/kalshi_portfolio.py tests/test_kalshi_orders.py
git commit -m "feat: isolated Kalshi order write client + position/order reads"
```

---

### Task 6: The trader orchestrator

**Files:**
- Create: `trader.py`
- Create: `trade_log.py`
- Test: `tests/test_trader.py`

**Interfaces:**
- Consumes: everything above, plus `model.snapshot`, `calibration.get`, `sources.kalshi.fetch_contracts`, `sources.kalshi.fetch_orderbook`, `sources.kalshi.implied_forecast`, `sources.kalshi_portfolio.balance/positions`, `settlement.climate_day_of`, `notify.send_ntfy`.
- Produces:
  - `trade_log.build_record(kind, **fields) -> dict` — audit record with `ts`, `kind` (`entry`/`exit`/`skip`/`halt`), and fields.
  - `trader.run_once(now=None, *, deps=None) -> dict` — one pass; returns a summary dict. `deps` is an injectable bundle (dataclass) of every external callable so the whole loop is testable with fakes.
  - `trader.main()` — production entry: builds real `deps`, calls `run_once`.

**Run-once algorithm (must match the spec's run loop):**
1. Load params via `deps.load_state()`. If `kill_switch` → return `{"halted": "kill_switch"}` (no orders).
2. If not `within_market_window(now, params)` → return `{"halted": "closed"}`.
3. Load runtime; if `runtime["halt_day"] == today` → return `{"halted": "daily_loss"}`.
4. Reconcile: `balance()`, `positions()`. If balance is None (read failed) → return `{"halted": "reconcile_failed"}` (never trade blind).
5. Compute snapshot via `deps.snapshot()`; per enabled variable get `var_snap`, `implied`, `contracts`.
6. Daily-loss check: sum realized+unrealized vs `daily_loss_cap`; if breached, set `runtime["halt_day"]=today`, save, notify, return.
7. **Exit pass** (before entry): for each held position, compute current ask + target bracket + gates; `should_exit` → `place_order(action="sell", side, price=current_bid)`, clear its runtime entry, log.
8. **Entry pass:** per enabled variable, if open-count-for-variable < `max_open_per_variable` and `entry_allowed`, pick bracket, `reentry_allowed` vs any just-stopped ticker, `size_bracket`; if `contracts>0` → `place_order(action="buy")`, record `entry_ask`, log.
9. Persist runtime; return summary.

- [ ] **Step 1: Write `trade_log.py` and its test**

```python
# trade_log.py
"""Audit-record schema for the trading loop. trade_state owns the GitHub IO;
this builds the typed records appended to trade_log.jsonl."""
from __future__ import annotations

from datetime import datetime, timezone


def build_record(kind: str, **fields) -> dict:
    """One audit row: kind in {entry, exit, skip, halt}, plus arbitrary fields."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind}
    rec.update(fields)
    return rec
```

```python
# tests/test_trade_log.py
import trade_log


def test_build_record_has_ts_and_kind():
    r = trade_log.build_record("entry", ticker="A", side="yes", count=1)
    assert r["kind"] == "entry" and r["ticker"] == "A"
    assert "ts" in r
```

- [ ] **Step 2: Write the failing trader test** (all deps faked)

```python
# tests/test_trader.py
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import trader


@dataclass
class Deps:
    state: dict
    snap: dict
    contracts: dict          # variable -> list[contract]
    book: dict               # ticker -> normalized orderbook
    implied: dict            # variable -> implied dict
    bal: float = 10.0
    positions_list: list = field(default_factory=list)
    runtime: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    logs: list = field(default_factory=list)

    # injected callables
    def load_state(self): return self.state
    def load_runtime(self): return self.runtime
    def save_runtime(self, r): self.runtime = r
    def snapshot(self): return self.snap
    def balance(self): return self.bal
    def positions(self): return self.positions_list
    def fetch_contracts(self, var, day): return self.contracts.get(var, [])
    def fetch_orderbook(self, ticker): return self.book.get(ticker, {"yes": [], "no": []})
    def implied_forecast(self, var, day): return self.implied.get(var)
    def place_order(self, **kw): self.orders.append(kw); return {"shadow": True, **kw}
    def append_log(self, rec): self.logs.append(rec)
    def notify(self, title, msg): pass


def _live_params(**over):
    base = {"kill_switch": False, "mode": "shadow", "enabled_variables": ["high"],
            "min_resolved": 0.70, "agreement_tol": 1.0, "max_price": 0.94,
            "min_price": 0.10, "kelly_fraction": 0.25, "per_market_cap": 0.50,
            "max_open_per_variable": 1, "daily_loss_cap": None, "stop_loss": 0.20,
            "slippage_cap": 0.02, "market_open": "06:00", "market_close": "20:00"}
    base.update(over)
    return base


NOON = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("America/Chicago"))


def test_kill_switch_blocks_everything():
    d = Deps(state=_live_params(kill_switch=True), snap={}, contracts={}, book={},
             implied={})
    out = trader.run_once(now=NOON, deps=d)
    assert out["halted"] == "kill_switch"
    assert d.orders == []


def test_outside_window_no_entry():
    d = Deps(state=_live_params(), snap={}, contracts={}, book={}, implied={})
    early = datetime(2026, 7, 24, 4, 0, tzinfo=ZoneInfo("America/Chicago"))
    out = trader.run_once(now=early, deps=d)
    assert out["halted"] == "closed"


def test_reconcile_failure_trades_nothing():
    d = Deps(state=_live_params(), snap={}, contracts={}, book={}, implied={}, bal=None)
    out = trader.run_once(now=NOON, deps=d)
    assert out["halted"] == "reconcile_failed"
    assert d.orders == []


def test_entry_when_agreement_and_gates_clear():
    contract = {"ticker": "KXHIGHTDAL-26JUL24-B99", "strike_type": "between",
                "floor": 98, "cap": 99, "yes_ask": 0.60, "no_ask": 0.42}
    d = Deps(
        state=_live_params(),
        snap={"today": {"day": "2026-07-24",
                        "high": {"consensus": 98.4, "resolved": 0.9,
                                 "low_forming": False, "peak_locked": True,
                                 "front_widened": False, "convective_widened": False,
                                 "probabilities": {"98": 0.4, "99": 0.4, "97": 0.2}}}},
        contracts={"high": [contract]},
        book={"KXHIGHTDAL-26JUL24-B99": {"yes": [], "no": [[0.42, 50]]}},
        implied={"high": {"ev": 98.6}},
    )
    out = trader.run_once(now=NOON, deps=d)
    assert len(d.orders) == 1
    assert d.orders[0]["action"] == "buy"
    assert d.orders[0]["ticker"] == contract["ticker"]


def test_no_entry_when_disagree():
    contract = {"ticker": "KXHIGHTDAL-26JUL24-B99", "strike_type": "between",
                "floor": 98, "cap": 99, "yes_ask": 0.60, "no_ask": 0.42}
    d = Deps(
        state=_live_params(),
        snap={"today": {"day": "2026-07-24",
                        "high": {"consensus": 98.4, "resolved": 0.9,
                                 "low_forming": False, "peak_locked": True,
                                 "probabilities": {"98": 0.5, "99": 0.5}}}},
        contracts={"high": [contract]},
        book={"KXHIGHTDAL-26JUL24-B99": {"yes": [], "no": [[0.42, 50]]}},
        implied={"high": {"ev": 101.0}},        # > 1°F from model
    )
    out = trader.run_once(now=NOON, deps=d)
    assert d.orders == []


def test_respects_one_open_per_variable():
    contract = {"ticker": "KXHIGHTDAL-26JUL24-B99", "strike_type": "between",
                "floor": 98, "cap": 99, "yes_ask": 0.60, "no_ask": 0.42}
    d = Deps(
        state=_live_params(),
        snap={"today": {"day": "2026-07-24",
                        "high": {"consensus": 98.4, "resolved": 0.9,
                                 "low_forming": False, "peak_locked": True,
                                 "probabilities": {"98": 0.5, "99": 0.5}}}},
        contracts={"high": [contract]},
        book={"KXHIGHTDAL-26JUL24-B99": {"yes": [], "no": [[0.42, 50]]}},
        implied={"high": {"ev": 98.6}},
        positions_list=[{"ticker": "KXHIGHTDAL-26JUL24-B97", "side": "yes",
                         "count": 1, "variable": "high"}],
        runtime={"entries": {"KXHIGHTDAL-26JUL24-B97":
                             {"entry_ask": 0.55, "side": "yes"}}},
    )
    out = trader.run_once(now=NOON, deps=d)
    # already at max_open_per_variable=1 for high -> no new buy
    assert all(o["action"] != "buy" for o in d.orders)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_trader.py tests/test_trade_log.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader'`.

- [ ] **Step 4: Write `trader.py`**

```python
# trader.py
"""Autonomous trading orchestrator — one pass per run.

Reconciles truth from the Kalshi account, consults the model/kelly decision
engine, then runs an EXIT pass followed by an ENTRY pass, placing marketable-limit
orders through the isolated write client. Every external call is reached through a
`Deps` bundle so the whole loop is unit-testable with fakes and no network. Ships
SAFE: does nothing unless kill_switch is off AND (for real fills) mode is live.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import trade_logic
import trade_log
import trade_params


@dataclass
class Deps:
    load_state: callable
    load_runtime: callable
    save_runtime: callable
    snapshot: callable
    balance: callable
    positions: callable
    fetch_contracts: callable
    fetch_orderbook: callable
    implied_forecast: callable
    place_order: callable
    append_log: callable
    notify: callable


def _current_ask(book: dict, side: str):
    """Best current ask for the held side, from the normalized order book."""
    from sources import kalshi as _k
    ladder = _k.ask_ladder(book, side)
    return ladder[0][0] if ladder else None


def _best_bid(contract: dict, side: str):
    """Price a marketable SELL fills into (the current bid on the held side)."""
    return contract.get("yes_bid") if side == "yes" else contract.get("no_bid")


def run_once(now: datetime | None = None, *, deps: Deps) -> dict:
    import settlement
    now = now or datetime.now()
    params = deps.load_state()

    if params["kill_switch"]:
        return {"halted": "kill_switch"}
    if not trade_params.within_market_window(now, params):
        return {"halted": "closed"}

    today: date = settlement.climate_day_of(now)
    today_iso = today.isoformat()
    runtime = deps.load_runtime() or {}
    runtime.setdefault("entries", {})
    if runtime.get("halt_day") == today_iso:
        return {"halted": "daily_loss"}

    bal = deps.balance()
    if bal is None:
        return {"halted": "reconcile_failed"}
    held = deps.positions()

    mode = params["mode"]
    bucket = now.strftime("%Y%m%dT%H%M")          # idempotency run-bucket
    summary = {"entries": 0, "exits": 0}
    just_stopped: dict[str, str] = {}             # variable -> ticker stopped this run

    # Per-variable market + model context.
    ctx = {}
    day_snap = (deps.snapshot() or {}).get("today") or {}
    for var in params["enabled_variables"]:
        vs = day_snap.get(var)
        if not vs:
            continue
        contracts = deps.fetch_contracts(var, today)
        implied = deps.implied_forecast(var, today)
        target = None
        ok_entry, reason = trade_logic.entry_allowed(vs, implied, params, var)
        if ok_entry:
            mkt = trade_logic.market_center(implied)
            target = trade_logic.select_bracket(contracts, mkt, var)
        gates_ok, _ = trade_logic.gates_clear(vs)
        ctx[var] = {"vs": vs, "contracts": contracts, "implied": implied,
                    "target": target, "ok_entry": ok_entry, "reason": reason,
                    "gates_ok": gates_ok,
                    "by_ticker": {c["ticker"]: c for c in contracts}}

    # ---- EXIT pass (before entry) ----
    for pos in held:
        var = pos["variable"]
        c = ctx.get(var)
        entry = (runtime["entries"].get(pos["ticker"]) or {})
        pos = {**pos, "entry_ask": entry.get("entry_ask")}
        if c is None or pos["entry_ask"] is None:
            continue                              # can't stop-loss without an entry ref
        contract = c["by_ticker"].get(pos["ticker"])
        book = deps.fetch_orderbook(pos["ticker"])
        cur_ask = _current_ask(book, pos["side"])
        target_tkr = c["target"]["ticker"] if c["target"] else None
        do_exit, why = trade_logic.should_exit(pos, cur_ask, target_tkr,
                                               c["gates_ok"], params)
        if not do_exit:
            continue
        bid = _best_bid(contract or {}, pos["side"])
        deps.place_order(ticker=pos["ticker"], side=pos["side"], action="sell",
                         count=pos["count"], price=bid if bid is not None else 0.01,
                         client_order_id=f"{pos['ticker']}:{today_iso}:exit:{bucket}",
                         mode=mode)
        runtime["entries"].pop(pos["ticker"], None)
        if "stop" in why:
            just_stopped[var] = pos["ticker"]
        deps.append_log(trade_log.build_record("exit", ticker=pos["ticker"],
                        side=pos["side"], count=pos["count"], reason=why, mode=mode))
        summary["exits"] += 1

    # Recount holdings per variable AFTER exits (source of truth stays Kalshi, but
    # within one run we track our own staged sells).
    open_by_var: dict[str, int] = {}
    for pos in held:
        if pos["ticker"] in runtime["entries"]:
            open_by_var[pos["variable"]] = open_by_var.get(pos["variable"], 0) + 1

    # ---- ENTRY pass ----
    for var in params["enabled_variables"]:
        c = ctx.get(var)
        if not c or not c["ok_entry"] or not c["target"]:
            if c and not c["ok_entry"]:
                deps.append_log(trade_log.build_record("skip", variable=var,
                                reason=c["reason"], mode=mode))
            continue
        if open_by_var.get(var, 0) >= params["max_open_per_variable"]:
            deps.append_log(trade_log.build_record("skip", variable=var,
                            reason="max_open_per_variable", mode=mode))
            continue
        target = c["target"]
        if not trade_logic.reentry_allowed(just_stopped.get(var), target["ticker"]):
            deps.append_log(trade_log.build_record("skip", variable=var,
                            reason="re-entry into just-stopped bracket", mode=mode))
            continue
        book = deps.fetch_orderbook(target["ticker"])
        sizing = trade_logic.size_bracket(target, c["vs"], book, bal, params)
        if sizing["contracts"] <= 0:
            deps.append_log(trade_log.build_record("skip", variable=var,
                            ticker=target["ticker"], reason=sizing["note"], mode=mode))
            continue
        entry_ask = sizing["avg_price"]
        deps.place_order(ticker=target["ticker"], side=sizing["side"], action="buy",
                         count=sizing["contracts"], price=entry_ask,
                         client_order_id=f"{target['ticker']}:{today_iso}:entry:{bucket}",
                         mode=mode)
        runtime["entries"][target["ticker"]] = {
            "entry_ask": entry_ask, "side": sizing["side"], "ts": now.isoformat()}
        open_by_var[var] = open_by_var.get(var, 0) + 1
        deps.append_log(trade_log.build_record("entry", ticker=target["ticker"],
                        side=sizing["side"], count=sizing["contracts"],
                        entry_ask=entry_ask, stake=sizing["stake"], mode=mode))
        summary["entries"] += 1

    deps.save_runtime(runtime)
    return {"halted": None, **summary}


def _real_deps() -> Deps:
    import calibration
    import model
    import notify
    import trade_state
    from sources import kalshi, kalshi_orders, kalshi_portfolio

    def snapshot():
        calib = calibration.get(refresh=True)
        return model.snapshot(calib) if calib else {}

    return Deps(
        load_state=trade_state.load_state,
        load_runtime=trade_state.load_runtime,
        save_runtime=trade_state.save_runtime,
        snapshot=snapshot,
        balance=kalshi_portfolio.balance,
        positions=kalshi_portfolio.positions,
        fetch_contracts=kalshi.fetch_contracts,
        fetch_orderbook=kalshi.fetch_orderbook,
        implied_forecast=kalshi.implied_forecast,
        place_order=kalshi_orders.place_order,
        append_log=lambda rec: trade_state.append_jsonl(trade_state.LOG_PATH, rec),
        notify=notify.send_ntfy,
    )


def main() -> None:
    from sources.common import TZ
    out = run_once(now=datetime.now(TZ), deps=_real_deps())
    print(f"trader run: {out}")


if __name__ == "__main__":
    main()
```

Note on the daily-loss breaker: the algorithm step 6 (compute realized+unrealized vs `daily_loss_cap` and set `runtime["halt_day"]`) is implemented once `positions()` exposes unrealized P&L. For the first shipment `daily_loss_cap` defaults to `-5.00`; wire the breaker as a follow-up step in this task if `positions()`/`balance()` expose enough, otherwise leave `daily_loss_cap=None` documented as not-yet-enforced and add a `# TODO(daily-loss)` — **do not** claim it is enforced if it is not. (Prefer to implement it: compare start-of-day balance snapshot stored in runtime against current balance + open-position mark; halt when the drop exceeds the cap.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_trader.py tests/test_trade_log.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (existing 662+ plus the new tests).

- [ ] **Step 7: Commit**

```bash
git add trader.py trade_log.py tests/test_trader.py tests/test_trade_log.py
git commit -m "feat: autonomous trader orchestrator (reconcile -> exit -> entry)"
```

---

### Task 7: Market-hours cron workflow

**Files:**
- Create: `.github/workflows/trade.yml`

**Interfaces:**
- Consumes: repo secrets `KALSHI_ACCESS_KEY_ID`, `KALSHI_PRIVATE_KEY` (read+write scoped key), `TRADE_GH_REPO`, `TRADE_GH_TOKEN`, `NTFY_TOPIC`. Runs `python trader.py`.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/trade.yml
name: Autonomous trade loop

# Runs the trader on a tight cadence during KDFW market hours ONLY. Ships SAFE:
# trader.py no-ops unless kill_switch is off AND mode is live (both set from the
# Streamlit control page, stored on the trade-data branch). Separate from log.yml
# so trading never affects logging, and on its own branch so nothing force-pushes
# over the other's state.
on:
  schedule:
    - cron: "*/2 11-23 * * *"    # every 2 min, 06:00-18:00 CDT (11:00-23:00 UTC)
  workflow_dispatch: {}
  repository_dispatch:
    types: [trade-tick]

permissions:
  contents: read

concurrency:
  group: trade-loop
  cancel-in-progress: true       # collapse overlaps; run is idempotent per bucket

jobs:
  trade:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Run one trade pass
        env:
          KALSHI_ACCESS_KEY_ID: ${{ secrets.KALSHI_ACCESS_KEY_ID }}
          KALSHI_PRIVATE_KEY: ${{ secrets.KALSHI_PRIVATE_KEY }}
          TRADE_GH_REPO: ${{ github.repository }}
          TRADE_GH_BRANCH: trade-data
          TRADE_GH_TOKEN: ${{ secrets.TRADE_GH_TOKEN }}
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python trader.py
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/trade.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Document one-time setup in the workflow header comment**

Add these lines to the top comment block (after the existing comment):

```yaml
# ONE-TIME SETUP (do before first live use):
#   1. Create the trade-data branch:  git branch trade-data && git push origin trade-data
#      (or run this workflow once via workflow_dispatch — trader.py's first save_runtime
#      seeds it, provided the branch already exists; create it empty first).
#   2. Secrets: TRADE_GH_TOKEN (repo-scoped PAT with contents:write), and a Kalshi
#      API key that has TRADING permission (the read key is not enough to place orders).
#   3. Confirm cron window matches CDT/CST if DST shifts.
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/trade.yml
git commit -m "feat: market-hours autonomous trade cron (ships safe/shadow)"
```

---

### Task 8: Streamlit control page

**Files:**
- Create: `trade_view.py`
- Modify: `app.py` (register nav page + seed the trade-state write secret)
- Test: `tests/test_trade_view.py`

**Interfaces:**
- Consumes: `trade_state.load_state/save_state/load_runtime`, `trade_params.DEFAULT_PARAMS`, `sources.kalshi_portfolio.positions/balance`.
- Produces:
  - `trade_view.render()` — the page (kill switch, mode toggle, param sliders, live positions/P&L, recent decisions).
  - `trade_view.summarize_log(records: list[dict], limit=20) -> list[dict]` — pure helper (tested) that shapes log rows for display.

Follow the existing hand-rolled-HTML table pattern used by the other views (see `bet_history.py` / `accuracy_view.py`); do not introduce `st.dataframe`. Match the dark serif theme.

- [ ] **Step 1: Write the failing test for the pure helper**

```python
# tests/test_trade_view.py
import trade_view


def test_summarize_log_newest_first_and_capped():
    recs = [{"ts": f"2026-07-24T10:0{i}:00+00:00", "kind": "entry",
             "ticker": f"T{i}", "reason": ""} for i in range(5)]
    out = trade_view.summarize_log(recs, limit=3)
    assert len(out) == 3
    assert out[0]["ticker"] == "T4"       # newest first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trade_view.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trade_view'`.

- [ ] **Step 3: Write `trade_view.py`**

```python
# trade_view.py
"""Streamlit control page for the autonomous trader.

The ONLY writer of trade_state.json (the cron reads it). Exposes the kill switch,
shadow/live mode, and every parameter as live controls, plus a live positions/P&L
panel and the recent decision log. Pure display shaping lives in summarize_log so
it is testable without Streamlit.
"""
from __future__ import annotations

import trade_params
import trade_state


def summarize_log(records: list[dict], limit: int = 20) -> list[dict]:
    """Newest-first, capped view rows for the decision log."""
    rows = sorted(records, key=lambda r: r.get("ts", ""), reverse=True)[:limit]
    return [{"ts": r.get("ts", ""), "kind": r.get("kind", ""),
             "ticker": r.get("ticker", ""), "reason": r.get("reason", "")}
            for r in rows]


def render() -> None:
    import streamlit as st

    st.markdown("## Autonomous Trader")
    params = trade_state.load_state()

    # --- Master switches ---
    killed = st.toggle("Kill switch (engaged = no trading)",
                       value=params["kill_switch"])
    mode = st.radio("Mode", ["shadow", "live"],
                    index=0 if params["mode"] == "shadow" else 1, horizontal=True)
    if mode == "live" and not killed:
        st.warning("⚠️ LIVE and armed — real orders will be placed.")

    # --- Parameters ---
    with st.expander("Parameters", expanded=False):
        params["min_resolved"] = st.slider("Min resolved", 0.0, 1.0,
                                            float(params["min_resolved"]), 0.05)
        params["agreement_tol"] = st.slider("Agreement tol (°F)", 0.0, 3.0,
                                             float(params["agreement_tol"]), 0.5)
        params["max_price"] = st.slider("Max price", 0.5, 0.99,
                                        float(params["max_price"]), 0.01)
        params["per_market_cap"] = st.number_input("Per-market cap ($)",
                                                    value=float(params["per_market_cap"]),
                                                    step=0.25)
        params["stop_loss"] = st.slider("Stop-loss (ask drop)", 0.05, 0.5,
                                        float(params["stop_loss"]), 0.05)
        params["kelly_fraction"] = st.slider("Kelly fraction", 0.05, 1.0,
                                             float(params["kelly_fraction"]), 0.05)

    if st.button("Save settings"):
        params["kill_switch"] = killed
        params["mode"] = mode
        trade_state.save_state(trade_params.merge_params(params))
        st.success("Saved.")

    # --- Live positions + decision log ---
    _render_positions(st)
    _render_log(st)


def _render_positions(st) -> None:
    from sources import kalshi_portfolio
    st.markdown("### Open positions")
    try:
        pos = kalshi_portfolio.positions()
        bal = kalshi_portfolio.balance()
    except Exception as e:
        st.info(f"Positions unavailable: {e}")
        return
    st.caption(f"Cash balance: ${bal:.2f}" if bal is not None else "Balance unavailable")
    if not pos:
        st.caption("No open positions.")
        return
    rows = "".join(f"<tr><td>{p['ticker']}</td><td>{p['side']}</td>"
                   f"<td>{p['count']}</td></tr>" for p in pos)
    st.markdown(f"<table class='wxtable'><tr><th>Ticker</th><th>Side</th>"
                f"<th>Count</th></tr>{rows}</table>", unsafe_allow_html=True)


def _render_log(st) -> None:
    st.markdown("### Recent decisions")
    try:
        from sources.common import get_json  # noqa: F401  (log read via trade_state)
        import json
        raw = trade_state.GitHubTransport().get(trade_state.LOG_PATH)
        records = [json.loads(x) for x in (raw[0].splitlines() if raw else []) if x]
    except Exception as e:
        st.info(f"Log unavailable: {e}")
        return
    for r in summarize_log(records):
        st.caption(f"{r['ts']} · {r['kind']} · {r['ticker']} · {r['reason']}")
```

- [ ] **Step 4: Register the page in `app.py`**

Find where the existing pages are registered (the nav list added in memory `accuracy-and-edge-pages`: Forecast·Hourly·Journal·History·Edge·Lab·Accuracy·Status). Add a "Trader" entry following the exact same pattern the file already uses for a lazy-imported page (mirror how `bet_view` is lazily imported to dodge the cryptography gap). Example shape to match the file's existing idiom:

```python
# in the page-dispatch block, alongside the other elif branches:
elif page == "Trader":
    import trade_view
    trade_view.render()
```

And add `"Trader"` to the nav labels list in the same order position you choose (recommend right after "History").

Also seed the write secret to env where the other Kalshi/GitHub secrets are seeded near the top of `app.py` (mirror the existing `st.secrets` → `os.environ` seeding for `[kalshi]` and the `FORECAST_LOG_GH_*` block):

```python
# alongside the existing secret-seeding:
if "trade" in st.secrets:
    for k in ("TRADE_GH_REPO", "TRADE_GH_BRANCH", "TRADE_GH_TOKEN"):
        if k in st.secrets["trade"]:
            os.environ.setdefault(k, str(st.secrets["trade"][k]))
```

- [ ] **Step 5: Run the page test + full suite**

Run: `python -m pytest tests/test_trade_view.py -q && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Verify the page renders locally**

Use the `verify` skill to launch the dashboard headlessly and screenshot the new "Trader" page. Confirm the kill switch shows engaged and mode shows shadow by default.

- [ ] **Step 7: Commit**

```bash
git add trade_view.py app.py tests/test_trade_view.py
git commit -m "feat: Streamlit control page for the autonomous trader"
```

---

## Rollout after implementation

1. Merge the branch; set repo secrets (`TRADE_GH_TOKEN`, trading-scoped Kalshi key). Create the `trade-data` branch.
2. Leave `kill_switch=False`, `mode=shadow`. Enable the cron. Let it run ~1–2 weeks.
3. Review the shadow decision log + would-be orders on the Trader page.
4. Only then flip `mode=live` with `per_market_cap=$0.50`. Confirm the daily-loss breaker is enforced before scaling size.

## Self-Review notes

- **Spec coverage:** autonomy (Task 6/7), fast market-hours cron (Task 7), reconcile-from-truth (Task 6 steps 4/8), agreement entry + max_price 0.94 + min_resolved 0.70 + agreement_tol 1.0 (Tasks 3/6), bracket tie-break both directions (Task 3), ask-referenced stop-loss + reversal, no take-profit (Task 4), re-entry-on-flip (Tasks 4/6), per-market cap + one-per-variable + kill switch (Tasks 1/6), isolated write client + read-only auth untouched (Task 5), shadow-first (Tasks 5/6/7), Streamlit control page (Task 8), daily-loss breaker (Task 1 param + Task 6 note — must be finished, not left as a claim).
- **Open item flagged, not hidden:** the daily-loss breaker's P&L computation depends on what `positions()` returns; Task 6 step 4 requires it be genuinely implemented or explicitly marked unenforced — never asserted as working when it is not.
- **Schema risk flagged:** the Kalshi order-body field names (Task 5) must be confirmed against live API docs before the live path is used.
