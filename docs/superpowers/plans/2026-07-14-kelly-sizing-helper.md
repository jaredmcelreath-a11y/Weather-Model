# Kelly Sizing Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-market Kelly bet-sizing helper that walks the live Kalshi order book so the recommended stake stops adding contracts once slippage makes the marginal contract not worth it.

**Architecture:** A new pure-math module `kelly.py` (fee, book-walk cost, classic Kelly, side choice, and the log-growth optimizer that returns a `Sizing` result). Kalshi's order-book fetch and its fiddly ask-ladder reconstruction live in `sources/kalshi.py`. A thin Streamlit box in `market_view.py` wires a contract dropdown + live balance + Kelly-fraction slider to `kelly.optimal_size` and draws a size-vs-return chart. All sizing logic is unit-tested; the UI is glue.

**Tech Stack:** Python 3, pytest, Streamlit, Altair, pandas. Existing helpers: `sources.common.get_json` (TTL disk cache), `sources.kalshi_portfolio.balance()`.

## Global Constraints

- Prices are handled in **dollars 0–1** in `kelly.py` and the view; the Kalshi order-book endpoint returns **integer cents**, converted at the `sources/kalshi.py` boundary.
- Model win-probability is `q`; for a contract the model's YES probability is `p = adapter.model_prob(probs, c)`, and NO win-prob is `1 - p`.
- Fees are **in scope**: Kalshi trading fee `= ceil_to_cent(0.07 · n · price · (1 − price))`, folded into every cost.
- Fractional Kelly rule: recommended `N` = largest N with `cost(N) ≤ λ · cost(N*)`, where `N*` is the full-Kelly optimum. Slider λ ∈ [0.25, 1.0], default 0.5.
- Never recommend past the negative-EV ceiling `N_max` (largest N whose marginal cost < `q`).
- Tests are flat files in `tests/`, named `test_*.py`, plain pytest functions. Run from repo root.
- Follow existing module style: `from __future__ import annotations`, module docstring, small pure functions.

---

### Task 1: Kalshi trading fee

**Files:**
- Create: `kelly.py`
- Test: `tests/test_kelly.py`

**Interfaces:**
- Produces: `kelly.fee(n: int, price: float) -> float` — trading fee in dollars for `n` contracts filled at `price` (dollars 0–1), rounded up to the next cent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kelly.py
"""Unit tests for kelly.py — the bet-sizing math. All pure; no network."""
import math

import kelly


def test_fee_matches_kalshi_formula():
    # Kalshi: fee = ceil_to_cent(0.07 * n * p * (1-p)).
    # 100 @ $0.50 -> 0.07*100*0.25 = 1.75 exactly.
    assert kelly.fee(100, 0.50) == 1.75


def test_fee_rounds_up_to_the_cent():
    # 1 @ $0.50 -> 0.07*0.25 = 0.0175 -> rounds UP to $0.02.
    assert kelly.fee(1, 0.50) == 0.02


def test_fee_zero_contracts_is_zero():
    assert kelly.fee(0, 0.50) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kelly'`

- [ ] **Step 3: Write minimal implementation**

```python
# kelly.py
"""Kelly bet-sizing on Kalshi temperature buckets, accounting for order-book
slippage. A single bucket is one binary bet (all contracts share the outcome),
so this is Kelly on a binary bet with a lumpy, size-dependent cost curve. Pure
functions — no network, no Streamlit. The Kalshi book convention lives in
sources/kalshi.py; here a ladder is just an ascending list of (price, size).
"""
from __future__ import annotations

import math


def fee(n: int, price: float) -> float:
    """Kalshi trading fee in dollars for `n` contracts filled at `price`
    (dollars 0-1): ceil to the next cent of 0.07 * n * price * (1 - price)."""
    if n <= 0:
        return 0.0
    raw = 0.07 * n * price * (1.0 - price)
    return math.ceil(raw * 100 - 1e-9) / 100.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add kelly.py tests/test_kelly.py
git commit -m "feat: Kalshi trading-fee formula for Kelly sizing"
```

---

### Task 2: Book-walk cost

**Files:**
- Modify: `kelly.py`
- Test: `tests/test_kelly.py`

**Interfaces:**
- Consumes: `kelly.fee`
- Produces: `kelly.cost_to_buy(ladder: list[tuple[float, int]], n: int, include_fees: bool = True) -> float | None` — total dollars to acquire `n` contracts by walking the ascending ask `ladder` (levels of `(price, size)`), fees applied per level. Returns `None` if `n` exceeds the book's total depth.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_kelly.py
LADDER = [(0.55, 40), (0.58, 120), (0.63, 300)]  # ascending asks


def test_cost_walks_the_book_gross():
    # Buy 100: 40@55 + 60@58 = 22.00 + 34.80 = 56.80 -> avg 56.8c.
    cost = kelly.cost_to_buy(LADDER, 100, include_fees=False)
    assert round(cost, 4) == 56.80
    assert round(cost / 100, 3) == 0.568


def test_cost_partial_first_level():
    assert kelly.cost_to_buy(LADDER, 40, include_fees=False) == 40 * 0.55


def test_cost_includes_per_level_fees():
    # 40 @ 55c: fee = ceil(0.07*40*0.55*0.45) = ceil(0.693) -> $0.70.
    gross = 40 * 0.55
    assert kelly.cost_to_buy(LADDER, 40, include_fees=True) == gross + 0.70


def test_cost_none_when_deeper_than_book():
    assert kelly.cost_to_buy(LADDER, 461, include_fees=False) is None
    assert kelly.cost_to_buy(LADDER, 460, include_fees=False) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -k cost -v`
Expected: FAIL with `AttributeError: module 'kelly' has no attribute 'cost_to_buy'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to kelly.py
def cost_to_buy(ladder, n, include_fees=True):
    """Total dollars to buy `n` contracts walking the ascending ask `ladder`
    (levels of (price, size)); fees applied per level on the block taken from
    that level. None if `n` exceeds total book depth."""
    if n <= 0:
        return 0.0
    remaining = n
    total = 0.0
    for price, size in ladder:
        take = min(remaining, size)
        total += take * price
        if include_fees:
            total += fee(take, price)
        remaining -= take
        if remaining == 0:
            return total
    return None  # book too thin to fill n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly.py -k cost -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add kelly.py tests/test_kelly.py
git commit -m "feat: order-book cost walk with per-level fees"
```

---

### Task 3: Classic single-price Kelly fraction

**Files:**
- Modify: `kelly.py`
- Test: `tests/test_kelly.py`

**Interfaces:**
- Produces: `kelly.kelly_fraction(q: float, price: float) -> float` — fraction of bankroll to risk at a fixed price; `(q - price) / (1 - price)`, clamped at 0 when non-positive.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_kelly.py
def test_kelly_fraction_positive_edge():
    # q=0.60, price=0.50 -> (0.60-0.50)/(1-0.50) = 0.20.
    assert round(kelly.kelly_fraction(0.60, 0.50), 4) == 0.20


def test_kelly_fraction_no_edge_is_zero():
    assert kelly.kelly_fraction(0.50, 0.50) == 0.0
    assert kelly.kelly_fraction(0.40, 0.50) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -k kelly_fraction -v`
Expected: FAIL with `AttributeError: module 'kelly' has no attribute 'kelly_fraction'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to kelly.py
def kelly_fraction(q, price):
    """Classic Kelly fraction of bankroll to risk on a binary contract bought
    at fixed `price` with win-probability `q`. Clamped at 0 (no bet) when the
    edge is non-positive. Reference point for the book-walk optimizer."""
    if price >= 1.0 or price <= 0.0:
        return 0.0
    f = (q - price) / (1.0 - price)
    return max(0.0, f)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly.py -k kelly_fraction -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add kelly.py tests/test_kelly.py
git commit -m "feat: classic single-price Kelly fraction"
```

---

### Task 4: Choose the side with edge

**Files:**
- Modify: `kelly.py`
- Test: `tests/test_kelly.py`

**Interfaces:**
- Produces: `kelly.best_side(p: float, yes_ask: float | None, no_ask: float | None) -> tuple[str, float, float] | None` — picks the side (`"yes"`/`"no"`) whose edge (win-prob − ask) is highest and positive; returns `(side, win_prob, ask)` or `None` when neither side has positive edge.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_kelly.py
def test_best_side_picks_yes_when_underpriced():
    # p=0.65, yes_ask=0.55 -> edge_yes +0.10; no_ask=0.50 -> edge_no 0.35-0.50<0.
    assert kelly.best_side(0.65, 0.55, 0.50) == ("yes", 0.65, 0.55)


def test_best_side_picks_no_when_yes_overpriced():
    # p=0.30 -> no win-prob 0.70; no_ask=0.55 -> edge_no +0.15 beats yes.
    assert kelly.best_side(0.30, 0.80, 0.55) == ("no", 0.70, 0.55)


def test_best_side_none_when_no_edge():
    assert kelly.best_side(0.50, 0.55, 0.55) is None


def test_best_side_ignores_missing_ask():
    # yes_ask missing -> only NO considered; NO win-prob 0.35 vs 0.50 ask is
    # negative edge, so nothing clears -> None.
    assert kelly.best_side(0.65, None, 0.50) is None
    # yes_ask present with edge, no_ask missing -> picks YES.
    assert kelly.best_side(0.65, 0.55, None) == ("yes", 0.65, 0.55)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -k best_side -v`
Expected: FAIL with `AttributeError: module 'kelly' has no attribute 'best_side'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to kelly.py
def best_side(p, yes_ask, no_ask):
    """The side to buy: whichever of YES (win-prob p) / NO (win-prob 1-p) has
    the larger positive edge vs its ask. None if neither side has an edge or
    its ask is missing. Mirrors the market table's >0 edge signal."""
    cands = []
    if yes_ask is not None:
        cands.append(("yes", p, yes_ask, p - yes_ask))
    if no_ask is not None:
        cands.append(("no", 1.0 - p, no_ask, (1.0 - p) - no_ask))
    cands = [c for c in cands if c[3] > 0]
    if not cands:
        return None
    side, win, ask, _edge = max(cands, key=lambda c: c[3])
    return (side, win, ask)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly.py -k best_side -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add kelly.py tests/test_kelly.py
git commit -m "feat: choose the edge side for Kelly sizing"
```

---

### Task 5: The optimizer — `optimal_size`

**Files:**
- Modify: `kelly.py`
- Test: `tests/test_kelly.py`

**Interfaces:**
- Consumes: `kelly.fee`
- Produces:
  - `kelly.Sizing` dataclass with fields: `side: str`, `contracts: int`, `avg_price: float | None`, `stake: float`, `ev: float`, `full_kelly: int`, `ev_ceiling: int`, `curve: list[tuple[int, float]]`, `note: str`.
  - `kelly.optimal_size(ladder, q, bankroll, kelly_frac, side="") -> Sizing` — walks `ladder` contract-by-contract (per-level fee rounding), finds the negative-EV ceiling `N_max`, the full-Kelly log-growth optimum `N*`, and the fractional recommendation, all bounded by bankroll.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_kelly.py
def test_optimal_size_recommends_within_ceiling():
    # Flat deep book at 55c, q=0.65: every contract is +EV until bankroll/ceiling.
    ladder = [(0.55, 1000)]
    s = kelly.optimal_size(ladder, q=0.65, bankroll=1000.0, kelly_frac=1.0)
    assert s.contracts > 0
    assert s.contracts <= s.ev_ceiling
    assert s.ev > 0


def test_optimal_size_stops_at_negative_ev():
    # Book climbs past q: 55c x40 (+EV), then 70c (>q=0.65, -EV). Ceiling=40.
    ladder = [(0.55, 40), (0.70, 1000)]
    s = kelly.optimal_size(ladder, q=0.65, bankroll=1_000_000.0, kelly_frac=1.0)
    assert s.ev_ceiling == 40
    assert s.contracts <= 40


def test_fractional_kelly_is_monotone_and_smaller():
    ladder = [(0.55, 1000)]
    half = kelly.optimal_size(ladder, 0.65, 1000.0, kelly_frac=0.5)
    full = kelly.optimal_size(ladder, 0.65, 1000.0, kelly_frac=1.0)
    assert 0 < half.contracts <= full.contracts
    # cost(half) <= 0.5 * cost(full) + one contract's slack
    assert half.stake <= 0.5 * full.stake + 0.55 + 0.05


def test_optimal_size_no_bet_when_best_ask_exceeds_q():
    ladder = [(0.70, 100)]
    s = kelly.optimal_size(ladder, q=0.65, bankroll=1000.0, kelly_frac=1.0)
    assert s.contracts == 0
    assert s.ev_ceiling == 0
    assert "No bet" in s.note


def test_optimal_size_flags_thin_book():
    # Whole book is +EV (never hits the ceiling within depth).
    ladder = [(0.55, 20)]
    s = kelly.optimal_size(ladder, q=0.90, bankroll=1_000_000.0, kelly_frac=1.0)
    assert s.contracts == 20
    assert "depth" in s.note.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -k optimal_size -v`
Expected: FAIL with `AttributeError: module 'kelly' has no attribute 'optimal_size'`

- [ ] **Step 3: Write minimal implementation**

```python
# add near the top of kelly.py, after the imports
from dataclasses import dataclass, field


@dataclass
class Sizing:
    side: str = ""
    contracts: int = 0
    avg_price: float | None = None
    stake: float = 0.0
    ev: float = 0.0
    full_kelly: int = 0
    ev_ceiling: int = 0
    curve: list = field(default_factory=list)   # (n, ev_dollars)
    note: str = ""
```

```python
# add to kelly.py
def optimal_size(ladder, q, bankroll, kelly_frac, side=""):
    """Recommended contract count on an ascending ask `ladder` for a binary
    contract with model win-prob `q`, sizing against `bankroll` at Kelly
    fraction `kelly_frac`. Walks the book contract-by-contract (fees rounded
    per level), stops at the negative-EV ceiling, maximizes expected
    log-growth for the full-Kelly point, then scales the stake by kelly_frac.
    """
    s = Sizing(side=side)
    if not ladder:
        s.note = "No live order book for this contract."
        return s

    # Cumulative cost incl. per-level-rounded fees, contract by contract.
    cost = [0.0]           # cost[n] = dollars to buy n contracts
    ev = [0.0]             # ev[n]   = q*n - cost[n]
    prev_levels_cost = 0.0
    hit_ceiling = False
    ceiling = 0
    for price, size in ladder:
        for k in range(1, int(size) + 1):
            block = k * price + fee(k, price)
            n = len(cost)                 # this contract's index
            c_n = prev_levels_cost + block
            marginal = c_n - cost[-1]
            if q - marginal <= 0:         # marginal contract is -EV: stop here
                hit_ceiling = True
                break
            if c_n >= bankroll:           # can't afford the next contract
                hit_ceiling = True
                break
            cost.append(c_n)
            ev.append(q * n - c_n)
            ceiling = n
            s.curve.append((n, round(ev[-1], 2)))
        if hit_ceiling:
            break
        prev_levels_cost += size * price + fee(int(size), price)

    s.ev_ceiling = ceiling
    if ceiling == 0:
        s.note = "No bet — the best ask already meets or exceeds the model's win probability."
        return s

    # Full-Kelly optimum: maximize q*ln(B + n - cost) + (1-q)*ln(B - cost).
    def growth(n):
        win = bankroll + n - cost[n]
        lose = bankroll - cost[n]
        if win <= 0 or lose <= 0:
            return -1e18
        return q * math.log(win) + (1.0 - q) * math.log(lose)

    best_n, best_g = 0, growth(0)
    for n in range(1, ceiling + 1):
        g = growth(n)
        if g > best_g:
            best_g, best_n = g, n
    s.full_kelly = best_n

    # Fractional Kelly: largest n whose cost <= kelly_frac * cost[best_n].
    target = kelly_frac * cost[best_n]
    rec = 0
    for n in range(1, best_n + 1):
        if cost[n] <= target + 1e-9:
            rec = n
    s.contracts = rec
    s.stake = cost[rec]
    s.ev = ev[rec]
    s.avg_price = (cost[rec] / rec) if rec else None

    if not hit_ceiling and ceiling == sum(int(sz) for _, sz in ladder):
        s.note = ("Limited by book depth — the whole visible book is +EV, so the "
                  "shown size is all that's currently available to fill.")
    return s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly.py -k optimal_size -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the whole kelly suite**

Run: `python -m pytest tests/test_kelly.py -v`
Expected: PASS (all green)

- [ ] **Step 6: Commit**

```bash
git add kelly.py tests/test_kelly.py
git commit -m "feat: log-growth Kelly optimizer over the order-book walk"
```

---

### Task 6: Fetch + normalize the Kalshi order book

**Files:**
- Modify: `sources/kalshi.py`
- Test: `tests/test_kalshi_orderbook.py`

**Interfaces:**
- Consumes: `sources.common.get_json`
- Produces:
  - `kalshi.fetch_orderbook(ticker: str, fetch=None) -> dict` — `{"yes": [[price_cents, size], ...], "no": [...]}` from `GET /markets/{ticker}/orderbook`; `fetch` injectable for tests. Cached ~15s.
  - `kalshi.ask_ladder(orderbook: dict, side: str) -> list[tuple[float, int]]` — ascending ask ladder (dollars) for the side being **bought**, reconstructed from the opposite side's resting bids.

> **Book-convention note (verify during this task):** On Kalshi the `/orderbook`
> response lists resting *bids* on each side in **cents**. To BUY YES you match
> the resting NO bids: a NO bid at `n` cents is willing to sell YES at
> `100 − n` cents. So the YES ask ladder is built from `orderbook["no"]`, and the
> NO ask ladder from `orderbook["yes"]`. **Before writing the code, confirm this
> against one live market** (see Step 0) — if the field names or side mapping
> differ, adjust `ask_ladder`/`fetch_orderbook` and the fixture to match reality,
> the way this repo verifies settlement bases empirically.

- [ ] **Step 0: Verify the live endpoint shape (no code yet)**

Run (pick any currently-open ticker from the high series):
```bash
python -c "from sources import kalshi; from datetime import date; \
cs = kalshi.fetch_contracts('high', date.today()); \
print(cs[0]['ticker'] if cs else 'no open market')"
```
Then inspect the raw book for that ticker:
```bash
python -c "from sources.common import get_json; \
import json; t='PASTE_TICKER_HERE'; \
d=get_json(f'https://api.elections.kalshi.com/trade-api/v2/markets/{t}/orderbook', {'depth':100}, ttl=0); \
print(json.dumps(d, indent=2)[:800])"
```
Confirm the `orderbook.yes` / `orderbook.no` arrays of `[price_cents, size]`. Note the actual structure; if it differs, adapt the code and fixture below to match. Record what you saw in the commit message.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kalshi_orderbook.py
"""Order-book fetch + ask-ladder reconstruction for Kalshi. The fixture mirrors
the live /orderbook shape captured in Task 6 Step 0."""
from sources import kalshi

# Resting BIDS in cents. yes-bids: someone buys YES at 54/53; no-bids: buys NO at 44/40.
FIXTURE = {"orderbook": {"yes": [[53, 200], [54, 100]],
                          "no": [[40, 300], [44, 150]]}}


def test_fetch_orderbook_normalizes_sides():
    ob = kalshi.fetch_orderbook("KXHIGHTDAL-X", fetch=lambda t: FIXTURE)
    assert ob == {"yes": [[53, 200], [54, 100]], "no": [[40, 300], [44, 150]]}


def test_ask_ladder_for_yes_from_no_bids_ascending():
    # Buying YES matches NO bids: no-bid 44c -> yes ask 56c; no-bid 40c -> 60c.
    ladder = kalshi.ask_ladder(FIXTURE["orderbook"], "yes")
    assert ladder == [(0.56, 150), (0.60, 300)]


def test_ask_ladder_for_no_from_yes_bids_ascending():
    # Buying NO matches YES bids: yes-bid 54c -> no ask 46c; yes-bid 53c -> 47c.
    ladder = kalshi.ask_ladder(FIXTURE["orderbook"], "no")
    assert ladder == [(0.46, 100), (0.47, 200)]


def test_ask_ladder_empty_book():
    assert kalshi.ask_ladder({"yes": [], "no": []}, "yes") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kalshi_orderbook.py -v`
Expected: FAIL with `AttributeError: module 'sources.kalshi' has no attribute 'fetch_orderbook'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to sources/kalshi.py (below fetch_contracts)
def fetch_orderbook(ticker: str, fetch=None) -> dict:
    """Live resting-bid order book for `ticker`, as {"yes": [[price_cents, size],
    ...], "no": [...]}. Prices are integer cents. `fetch` is injectable for
    tests; the default hits GET /markets/{ticker}/orderbook, cached ~15s."""
    fetch = fetch or (lambda t: get_json(f"{BASE}/markets/{t}/orderbook",
                                         {"depth": 100}, ttl=15))
    ob = (fetch(ticker) or {}).get("orderbook") or {}
    return {"yes": ob.get("yes") or [], "no": ob.get("no") or []}


def ask_ladder(orderbook: dict, side: str) -> list:
    """Ascending ask ladder in dollars for the side being BOUGHT, reconstructed
    from the opposite side's resting bids: buying YES sells against NO bids
    (yes_ask = 1 - no_bid), and vice-versa. Levels are (price_dollars, size)."""
    opp = "no" if side == "yes" else "yes"
    ladder = [((100 - int(pc)) / 100.0, int(sz))
              for pc, sz in (orderbook.get(opp) or []) if int(sz) > 0]
    ladder.sort(key=lambda lvl: lvl[0])
    return ladder
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kalshi_orderbook.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add sources/kalshi.py tests/test_kalshi_orderbook.py
git commit -m "feat: fetch + reconstruct Kalshi order-book ask ladder

Verified against live /orderbook: <one line on the shape you saw in Step 0>"
```

---

### Task 7: The Kelly sizing box in the market view

**Files:**
- Modify: `market_view.py` (add `_kelly_sizing_box`, call it inside `render_variable` after the Safest-hold box, ~line 1167)
- Test: `tests/test_kelly_box.py`

**Interfaces:**
- Consumes: `kelly.best_side`, `kelly.optimal_size`, `kalshi.fetch_orderbook`, `kalshi.ask_ladder`, `kalshi_portfolio.balance`, `adapter.model_prob`
- Produces: `market_view._kelly_pick(contracts, probs, adapter) -> tuple | None` — pure helper choosing `(contract, side, q)` for the model's best edge across the live contracts (extracted so it is testable without Streamlit); returns `None` when nothing has an edge.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kelly_box.py
"""The pure pick-selection behind the Kelly sizing box (no Streamlit)."""
import market_view


class _Adapter:
    # model_prob returns the YES prob stashed on each synthetic contract.
    def model_prob(self, probs, c):
        return c["p"]


def test_kelly_pick_selects_highest_edge_contract():
    contracts = [
        {"label": "88-89", "p": 0.55, "yes_ask": 0.54, "no_ask": 0.48},  # +0.01
        {"label": "90-91", "p": 0.70, "yes_ask": 0.55, "no_ask": 0.42},  # +0.15
    ]
    pick = market_view._kelly_pick(contracts, probs={}, adapter=_Adapter())
    assert pick is not None
    contract, side, q = pick
    assert contract["label"] == "90-91"
    assert side == "yes"
    assert q == 0.70


def test_kelly_pick_none_when_no_edge():
    contracts = [{"label": "88-89", "p": 0.50, "yes_ask": 0.55, "no_ask": 0.55}]
    assert market_view._kelly_pick(contracts, probs={}, adapter=_Adapter()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly_box.py -v`
Expected: FAIL with `AttributeError: module 'market_view' has no attribute '_kelly_pick'`

- [ ] **Step 3: Write the pure helper**

```python
# add to market_view.py (near the other module-level helpers, e.g. after exit_plan)
import kelly
from sources import kalshi as _kalshi

def _kelly_pick(contracts, probs, adapter):
    """The model's single best-edge (contract, side, win_prob) across the live
    contracts, or None if nothing clears a positive edge. Pure — no Streamlit."""
    best = None  # (edge, contract, side, q)
    for c in contracts:
        p = adapter.model_prob(probs, c)
        chosen = kelly.best_side(p, c.get("yes_ask"), c.get("no_ask"))
        if chosen is None:
            continue
        side, q, ask = chosen
        edge = q - ask
        if best is None or edge > best[0]:
            best = (edge, c, side, q)
    if best is None:
        return None
    _edge, c, side, q = best
    return (c, side, q)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_kelly_box.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the Streamlit box (glue) and call it**

```python
# add to market_view.py, below _kelly_pick
from sources import kalshi_portfolio

def _kelly_sizing_box(contracts, probs, adapter, variable):
    """Interactive Kelly bet-sizing box for one market. Lets the user pick a
    live contract, set a Kelly fraction, and see the recommended stake plus a
    size-vs-return curve that shows where extra contracts stop being worth it.
    Thin glue over kelly.optimal_size + the live order book + account balance."""
    box = st.container(border=True)
    box.markdown(f"**{variable.capitalize()} Kelly Sizing Helper** — how much to bet")

    edged = [c for c in contracts
             if kelly.best_side(adapter.model_prob(probs, c),
                                c.get("yes_ask"), c.get("no_ask")) is not None]
    if not edged:
        box.caption("No live contract has a positive edge right now — nothing to size.")
        return

    labels = [c["label"] for c in edged]
    default = _kelly_pick(edged, probs, adapter)
    default_idx = labels.index(default[0]["label"]) if default else 0
    label = box.selectbox("Contract", labels, index=default_idx,
                          key=f"kelly_ct_{variable}")
    c = next(x for x in edged if x["label"] == label)
    side, q, _ask = kelly.best_side(adapter.model_prob(probs, c),
                                    c.get("yes_ask"), c.get("no_ask"))

    bal = kalshi_portfolio.balance()
    bankroll = box.number_input(
        "Bankroll ($)", min_value=1.0,
        value=float(round(bal, 2)) if bal else 100.0, step=10.0,
        key=f"kelly_bank_{variable}",
        help="Auto-filled from your live Kalshi cash balance; edit to size against "
             "a different pool." if bal else "Enter the pool you're sizing against.")
    frac = box.slider("Kelly fraction", 0.25, 1.0, 0.5, 0.05,
                      key=f"kelly_frac_{variable}",
                      help="Fraction of full Kelly. 0.5 (half Kelly) is the safe "
                           "default; full Kelly (1.0) is aggressive.")

    try:
        ob = _kalshi.fetch_orderbook(c["ticker"])
        ladder = _kalshi.ask_ladder(ob, side)
    except Exception:
        box.caption("Couldn't load the live order book — try again in a moment.")
        return

    s = kelly.optimal_size(ladder, q, bankroll, frac, side=side)
    if s.contracts == 0:
        box.info(s.note or "No bet recommended.")
        return

    m1, m2, m3 = box.columns(3)
    m1.metric("Buy", f"{s.contracts} × {side.upper()}")
    m2.metric("Avg fill", cents(s.avg_price))
    m3.metric("Stake", f"${s.stake:,.2f}")
    n1, n2 = box.columns(2)
    n1.metric("Expected value", f"${s.ev:,.2f}")
    n2.metric("Max +EV size", f"{s.ev_ceiling}")
    if s.curve:
        cdf = pd.DataFrame(s.curve, columns=["contracts", "ev"])
        chart = (alt.Chart(cdf).mark_line().encode(
                    x=alt.X("contracts", title="contracts bought"),
                    y=alt.Y("ev", title="expected value ($)"))
                 + alt.Chart(pd.DataFrame({"contracts": [s.contracts]}))
                    .mark_rule(color="green").encode(x="contracts"))
        box.altair_chart(chart, use_container_width=True)
    box.caption(
        f"At {frac:g}× Kelly against ${bankroll:,.0f}, buy **{s.contracts} "
        f"{side.upper()}** on {label} (avg {cents(s.avg_price)}, stake "
        f"${s.stake:,.2f}, EV +${s.ev:,.2f}). Beyond {s.ev_ceiling} contracts the "
        "order book climbs past the model's win probability — those add negative "
        "expected value. The green line marks the recommended size."
        + (f" {s.note}" if s.note else ""))
```

```python
# in render_variable, immediately AFTER the Safest-hold box block (the if/else
# ending near line 1167, before `def exclusion_note`), add:
        _kelly_sizing_box(contracts, probs, adapter, variable)
```

> `import altair as alt` and `import pandas as pd` are already imported at the top
> of `market_view.py`; do not re-import. Verify with `grep -n "^import altair\|^import pandas" market_view.py`.

- [ ] **Step 6: Re-run the pure test + import-smoke the module**

Run: `python -m pytest tests/test_kelly_box.py -v && python -c "import market_view"`
Expected: PASS (2 passed) and the import prints nothing (no syntax/import errors).

- [ ] **Step 7: Commit**

```bash
git add market_view.py tests/test_kelly_box.py
git commit -m "feat: interactive Kelly sizing box on each market"
```

---

### Task 8: Full suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS (all green, including the pre-existing suite)

- [ ] **Step 2: Launch the app and eyeball the box**

Run: `streamlit run app.py` (or the project's documented run command), open the page, and confirm on a market with live contracts:
- the "Kelly Sizing Helper" box renders with a contract dropdown, bankroll (pre-filled from balance if creds present), and the fraction slider;
- changing the slider changes the recommended contract count and moves the green rule on the chart;
- a market with no edge shows the "nothing to size" caption instead of the box body.

- [ ] **Step 3: Commit any fixups**

```bash
git add -A
git commit -m "test: verify Kelly sizing helper end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** fee (Task 1), book-walk cost (Task 2), classic Kelly (Task 3), side choice (Task 4), log-growth optimizer + fractional rule + ceiling + edge cases (Task 5), order-book fetch/normalization with empirical verification (Task 6), standalone UI box with dropdown/balance/slider/curve/empty-states (Task 7), full verification (Task 8). All spec sections map to a task.
- **Fractional-Kelly monotonicity, negative-EV ceiling, thin-book, and no-bet** edge cases are each covered by a Task 5 test.
- **Type consistency:** `optimal_size` returns `Sizing`; the view reads `.contracts/.side/.avg_price/.stake/.ev/.ev_ceiling/.curve/.note`. `best_side` returns `(side, q, ask)` and is consumed identically in Tasks 5/7. `ask_ladder` returns `list[(price_dollars, size)]`, the exact shape `optimal_size`/`cost_to_buy` consume.
- **Book convention** is the one real-world unknown; Task 6 Step 0 forces empirical confirmation before the code is written.
