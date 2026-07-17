# Season Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the model returning a confident `0` for contracts that fall outside its 60–110 bin range, by widening the range to −10..115, making bin labels self-describing, and abstaining where a query cuts inside an open-ended tail.

**Architecture:** `model.bin_temp` currently maps the `"<= 60"` tail to the *config constant* `BIN_LOW`, so `prob_at_most(probs, 59)` finds no bin ≤ 59 and returns `0` instead of "unknown". `market_view` turns that `0` into `edge_no = (1 - 0) - na` — a `BUY NO +85` signal on a near-certain YES bucket, which tops Top-3 and gets sized by Kelly. The fix has three parts: widen `BIN_LOW`/`BIN_HIGH` to bracket DFW's real climate; make `bin_temp` parse the number out of its label so logged rows stay self-describing across range changes (no migration); and return `None` — not `0` — when a query cuts inside a tail, propagating that `None` to five consumers which each degrade visibly.

**Tech Stack:** Python 3.9, pytest, Streamlit (dashboard), pandas/altair (display). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-17-season-readiness-design.md`

## Global Constraints

- **Branch:** `season-readiness-bins` (already created; spec committed at afecaa7).
- **Interpreter is `python3`** — there is no bare `python` on this machine.
- **Test baseline:** `python3 -m pytest -q --continue-on-collection-errors` → **325 passed**, plus 4 `test_bet_view` failures and 3 collection errors (`test_exclusion_note`, `test_kalshi_auth`, `test_kalshi_portfolio`). Those 7 are the local `cryptography`/`streamlit` env gaps and are **NOT regressions** — they pass in CI. Never "fix" them; never let the count drop below 325.
- **`market_view` imports streamlit at module load.** Any test importing it MUST use the stub guard from `tests/test_kelly_box.py:8-15` verbatim (no-op in CI where streamlit exists).
- **Bin range values:** `BIN_LOW = -10`, `BIN_HIGH = 115`. Exactly these.
- **Do not** rewrite or migrate `forecast_log.jsonl` — the whole point of the `bin_temp` change is that no migration is needed.
- **Out of scope:** `_LOW_WINDOW = (0, 9)` / `covers_extreme` / evening-front source coverage. Deferred to its own spec. Do not touch `settlement.py:83` or `model.py:235`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `config.py:34-35` | Bin range constants + `bin_labels()` | Modify: range values + comment |
| `model.py:773-779` | `bin_temp` — label → temperature | Modify: parse label instead of returning config constant |
| `model.py:782-821` | `prob_at_*` / `prob_for_*` — probability queries | Modify: add `_tail_edges` helper + abstain guard + `None` propagation |
| `kelly.py:64-77` | `best_side` — which side to buy | Modify: `None` in → `None` out |
| `backtest.py:74-78` | `contract_points` — reliability points | Modify: skip `None` before range check |
| `scoring.py:128` | `within1` accuracy metric | Modify: `bin_temp` distance, not `LABELS.index` |
| `market_view.py:1036-1063` | Market table loop — rows, signals, picks, holds | Modify: `p is None` guard at top of loop |
| `market_view.py:1092-1096` | Open-positions box | Modify: explicit `None` check |
| `edge_report.py:29-32` | `is_boundary` — Kalshi edge proximity | Modify: derive edges from config |
| `tests/test_season_bins.py` | All new behavior for this change | **Create** |

Task order is dependency-driven: `bin_temp` (Task 1) underpins the guard (Task 2), which produces the `None` that Tasks 3–5 consume. Tasks 6–7 are independent collateral and could run in any order after Task 1.

---

### Task 1: `bin_temp` parses its label

**Files:**
- Modify: `model.py:773-779`
- Test: `tests/test_season_bins.py` (create)

**Interfaces:**
- Produces: `model.bin_temp(label: str) -> int` — unchanged signature, new semantics. `"<= 60"` → `60`, `">= 115"` → `115`, `"90"` → `90`. No longer reads `BIN_LOW`/`BIN_HIGH`.

This is the keystone. Today `bin_temp("<= 60")` returns whatever `BIN_LOW` currently is, so the moment Task 6 widens the range, every historical `"<= 60"` row in `forecast_log.jsonl` would silently re-read as `-10`. Parsing the label makes each logged row mean what it meant when written — which is why this task comes before the range change, and why no migration is needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_season_bins.py`:

```python
"""Season readiness: self-describing bin labels + the tail abstain guard.

See docs/superpowers/specs/2026-07-17-season-readiness-design.md
"""
import model


def test_bin_temp_parses_legacy_tail_labels():
    # A row logged under the old 60..110 range must keep its original meaning
    # even after the range widens — bin_temp reads the label, not the config.
    assert model.bin_temp("<= 60") == 60
    assert model.bin_temp(">= 110") == 110


def test_bin_temp_parses_new_tail_labels():
    assert model.bin_temp("<= -10") == -10
    assert model.bin_temp(">= 115") == 115


def test_bin_temp_parses_interior_label():
    assert model.bin_temp("90") == 90


def test_bin_temp_ignores_config_range():
    # The whole point: changing the config must not change what a label means.
    original = (model.BIN_LOW, model.BIN_HIGH)
    try:
        model.BIN_LOW, model.BIN_HIGH = -99, 999
        assert model.bin_temp("<= 60") == 60
        assert model.bin_temp(">= 110") == 110
    finally:
        model.BIN_LOW, model.BIN_HIGH = original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py -v`

Expected: `test_bin_temp_parses_legacy_tail_labels` FAILS with `assert 60 == 60` passing but `test_bin_temp_parses_new_tail_labels` FAILING — `bin_temp("<= -10")` returns `60` (the current `BIN_LOW`), not `-10`. `test_bin_temp_ignores_config_range` FAILS: returns `-99`, not `60`.

- [ ] **Step 3: Write minimal implementation**

Replace `model.py:773-779` entirely:

```python
def bin_temp(label: str) -> int:
    """Integer temperature a bin label represents.

    Parses the number out of the label rather than reading BIN_LOW/BIN_HIGH, so
    a row logged under an older bin range still means what it meant when it was
    written. Without this, widening the range would silently re-interpret every
    historical "<= 60" tail as the new BIN_LOW.
    """
    if label.startswith("<=") or label.startswith(">="):
        return int(label[2:])
    return int(label)
```

(`int(" 60")` handles the space after the operator — no strip needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py -v`
Expected: 4 passed.

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **329 passed** (325 baseline + 4 new), same 4 failures / 3 errors as baseline.

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_season_bins.py
git commit -m "fix: bin_temp parses its label instead of reading the config range

A logged row's tail label meant a specific temperature when it was
written. Reading BIN_LOW/BIN_HIGH instead would re-interpret every
historical '<= 60' the moment the range changes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Abstain when a query cuts inside a tail

**Files:**
- Modify: `model.py:782-821`
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `model.bin_temp` (Task 1).
- Produces:
  - `model._tail_edges(probs: dict) -> tuple[int | None, int | None]` — `(low_edge, high_edge)` from the dict's own tail labels; `None` where that tail is absent.
  - `model.prob_at_least(probs, threshold) -> float | None`
  - `model.prob_at_most(probs, threshold) -> float | None`
  - `model.prob_greater_than`, `model.prob_less_than`, `model.prob_for_contract`, `model.prob_for_strike` — all `-> float | None`.

Tail edges come from **the probs dict itself**, not from config. A legacy `"<= 60"` row genuinely cannot answer `P(low <= 59)`, and must abstain — judged by its own range, not the new one.

The guard is narrow. Only two cases are unanswerable: `prob_at_most(t)` where `t` is strictly below the low tail edge, and `prob_at_least(t)` where `t` is strictly above the high tail edge. Everything else keeps working exactly as today — a threshold landing *on* a tail edge is that whole tail's mass; `prob_at_least(t)` for `t <= low_edge` is 1.0; and a dict with no tail labels is a closed set where mass outside genuinely is zero.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`:

```python
# A September cold-front low near 55, as the OLD 60..110 range would log it.
_LEGACY_FRONT = {"<= 60": 0.97, "61": 0.02, "62": 0.01}
# A closed dict with no tails at all (as several existing tests build).
_CLOSED = {"90": 0.5, "91": 0.5}


def test_abstains_when_query_cuts_inside_low_tail():
    # The bug: this returned 0 — a confident "impossible" for a near-certain low.
    assert model.prob_at_most(_LEGACY_FRONT, 59) is None
    assert model.prob_at_most(_LEGACY_FRONT, 55) is None


def test_abstains_when_query_cuts_inside_high_tail():
    probs = {"108": 0.01, "109": 0.02, ">= 110": 0.97}
    assert model.prob_at_least(probs, 111) is None


def test_threshold_on_the_tail_edge_is_answerable():
    # "<= 60" IS exactly the mass at or below 60 — no resolution needed inside
    # it, so this is answerable: the tail's own 0.97, not the whole dict.
    assert abs(model.prob_at_most(_LEGACY_FRONT, 60) - 0.97) < 1e-9


def test_query_past_the_far_tail_is_answerable():
    # Everything is >= 60 when 60 is the low edge; no tail-splitting required.
    assert model.prob_at_least(_LEGACY_FRONT, 60) == 1.0
    assert model.prob_at_least(_LEGACY_FRONT, 55) == 1.0


def test_closed_dict_without_tails_never_abstains():
    # No open tail => mass outside the set is genuinely zero, not unknown.
    assert model.prob_at_most(_CLOSED, 50) == 0.0
    assert model.prob_at_least(_CLOSED, 200) == 0.0


def test_abstain_propagates_through_contract_helpers():
    assert model.prob_less_than(_LEGACY_FRONT, 60) is None      # -> at_most(59)
    assert model.prob_for_contract(_LEGACY_FRONT, "<", 60) is None


def test_abstain_propagates_through_kalshi_strikes():
    # "59 or below" and "between 54-55" both need sub-tail resolution.
    assert model.prob_for_strike(_LEGACY_FRONT, "less", None, 60) is None
    assert model.prob_for_strike(_LEGACY_FRONT, "between", 54, 55) is None


def test_answerable_strike_still_prices():
    p = model.prob_for_strike(_LEGACY_FRONT, "between", 61, 62)
    assert abs(p - 0.03) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py -v`

Expected: `test_abstains_when_query_cuts_inside_low_tail` FAILS with `assert 0 is None` — this is the bug reproduced. `test_abstains_when_query_cuts_inside_high_tail`, `test_abstain_propagates_through_contract_helpers`, and `test_abstain_propagates_through_kalshi_strikes` FAIL the same way.

- [ ] **Step 3: Write minimal implementation**

Replace `model.py:782-821` (from `def prob_at_least` through the end of `prob_for_strike`):

```python
def _tail_edges(probs: dict) -> tuple[int | None, int | None]:
    """(low_edge, high_edge) from the dict's OWN open-ended tail labels.

    Read from the dict rather than from config so a row logged under an older
    bin range is judged by the range it was written with. None where a tail is
    absent (a closed dict has no unanswerable queries).
    """
    lo = hi = None
    for k in probs:
        if k.startswith("<="):
            lo = bin_temp(k)
        elif k.startswith(">="):
            hi = bin_temp(k)
    return lo, hi


def prob_at_least(probs: dict, threshold: int) -> float | None:
    """P(value >= threshold), or None if that cuts inside the high tail.

    The ">= H" tail is open-ended: it holds the mass at H or hotter without
    resolving how it splits, so P(value >= H+1) is genuinely unknown. Returning
    None makes callers abstain; returning 0 would assert impossibility.
    """
    _lo, hi = _tail_edges(probs)
    if hi is not None and threshold > hi:
        return None
    return sum(v for k, v in probs.items() if bin_temp(k) >= threshold)


def prob_at_most(probs: dict, threshold: int) -> float | None:
    """P(value <= threshold), or None if that cuts inside the low tail."""
    lo, _hi = _tail_edges(probs)
    if lo is not None and threshold < lo:
        return None
    return sum(v for k, v in probs.items() if bin_temp(k) <= threshold)


def prob_greater_than(probs: dict, threshold: int) -> float | None:
    """P(value > threshold) under whole-degree settlement — i.e. value >= T+1.
    Matches a Robinhood 'Greater than T°' (high) contract resolving YES."""
    return prob_at_least(probs, threshold + 1)


def prob_less_than(probs: dict, threshold: int) -> float | None:
    """P(value < threshold) under whole-degree settlement — i.e. value <= T-1.
    Matches a Robinhood 'Lower than T°' (low) contract resolving YES."""
    return prob_at_most(probs, threshold - 1)


def prob_for_contract(probs: dict, kind: str, strike: int) -> float | None:
    """Model YES probability for a Robinhood ladder contract ('>' high / '<' low).
    None when the model can't price it (see prob_at_least/prob_at_most)."""
    return prob_greater_than(probs, strike) if kind == ">" \
        else prob_less_than(probs, strike)


def prob_for_strike(probs: dict, strike_type: str, floor, cap) -> float | None:
    """Model YES probability for a Kalshi contract, from its strike fields.

    - 'less'    (e.g. cap=88, "87° or below"): value <= cap-1
    - 'greater' (e.g. floor=95, "96° or above"): value >= floor+1
    - 'between' (floor..cap inclusive): floor <= value <= cap

    None when any leg falls inside an open tail — the model abstains rather than
    reporting a false 0.
    """
    if strike_type == "less":
        return prob_at_most(probs, cap - 1)
    if strike_type == "greater":
        return prob_at_least(probs, floor + 1)
    hi = prob_at_most(probs, cap)
    lo = prob_at_most(probs, floor - 1)
    if hi is None or lo is None:
        return None
    return hi - lo
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py -v`
Expected: 12 passed.

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **333 passed** (325 + 8 new), same 4 failures / 3 errors. If any *previously passing* test now fails, a consumer is unguarded — that is Tasks 3–5's job; note it and continue only if the failure is in `backtest`/`scoring`/`market_view`/`kelly`.

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_season_bins.py
git commit -m "fix: abstain instead of returning a false 0 inside an open tail

prob_at_most(probs, 59) on a '<= 60' distribution returned 0 — a
confident 'impossible' for a near-certain low. Tail edges are read from
the probs dict itself, so a legacy row is judged by its own range.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `kelly.best_side` can't size what the model can't price

**Files:**
- Modify: `kelly.py:64-77`
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `None` from `model.prob_for_strike` / `prob_for_contract` (Task 2).
- Produces: `kelly.best_side(p: float | None, yes_ask, no_ask) -> tuple | None` — `None` when `p is None`.

One guard here covers all three call sites (`market_view.py:1208`, the `edged` filter at `:1235`, and `:1247`). It fits the function's existing contract — it already returns `None` when no side is worth buying, and an unpriceable contract simply isn't sizable. Without it, `p - yes_ask` raises `TypeError`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`:

```python
import kelly


def test_best_side_abstains_when_model_cannot_price():
    # Without the guard this raises TypeError on `p - yes_ask`.
    assert kelly.best_side(None, 0.40, 0.55) is None


def test_best_side_still_picks_the_edge_when_priced():
    side, win, ask = kelly.best_side(0.70, 0.55, 0.42)
    assert side == "yes"
    assert win == 0.70
    assert ask == 0.55
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py::test_best_side_abstains_when_model_cannot_price -v`
Expected: FAIL with `TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'`

- [ ] **Step 3: Write minimal implementation**

In `kelly.py`, replace the body of `best_side` (lines 64-77) — add the guard as the first statement after the docstring:

```python
def best_side(p, yes_ask, no_ask):
    """The side to buy: whichever of YES (win-prob p) / NO (win-prob 1-p) has
    the larger positive edge vs its ask. None if neither side has an edge or
    its ask is missing. Mirrors the market table's >0 edge signal.

    `p` is None when the model can't price the contract (it falls inside an
    open-ended bin tail) — an unpriceable contract isn't sizable.
    """
    if p is None:
        return None
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py tests/test_kelly.py tests/test_kelly_box.py -v`
Expected: all pass (14 in `test_season_bins.py`).

- [ ] **Step 5: Commit**

```bash
git add kelly.py tests/test_season_bins.py
git commit -m "fix: best_side abstains when the model can't price the contract

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `backtest.contract_points` skips unpriceable strikes

**Files:**
- Modify: `backtest.py:74-78`
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `None` from `model.prob_for_contract` (Task 2).
- Produces: `backtest.contract_points(probs, actual, variable) -> list[tuple[float, float]]` — unchanged signature; silently drops strikes the model can't price.

`contract_points` sweeps `range(BIN_LOW, BIN_HIGH + 1)` and filters with `if not (0.01 <= p <= 0.99)`. On `None` that raises `TypeError: '<=' not supported between instances of 'float' and 'NoneType'` (verified). It must skip `None` *before* the range check. This also matters for `scoring.py`, which calls it on every logged row.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`:

```python
import backtest


def test_contract_points_skips_unpriceable_strikes():
    # A legacy-range row swept against the new wider strike range hits strikes
    # the model can't price; those must be skipped, not crash.
    pts = backtest.contract_points(_LEGACY_FRONT, 55.0, "low")
    assert isinstance(pts, list)
    assert all(p is not None and 0.01 <= p <= 0.99 for p, _won in pts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py::test_contract_points_skips_unpriceable_strikes -v`
Expected: FAIL with `TypeError: '<=' not supported between instances of 'float' and 'NoneType'`

- [ ] **Step 3: Write minimal implementation**

In `backtest.py`, in `contract_points`, replace the loop body's filter (around line 75-77):

```python
    for strike in range(BIN_LOW, BIN_HIGH + 1):
        p = model.prob_for_contract(probs, kind, strike)
        if p is None:          # model can't price this strike (inside a tail)
            continue
        if not (0.01 <= p <= 0.99):
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py tests/test_accuracy.py -v`
Expected: all pass (15 in `test_season_bins.py`).

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_season_bins.py
git commit -m "fix: contract_points skips strikes the model can't price

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: The market table shows `—` instead of a phantom edge

**Files:**
- Modify: `market_view.py:1036-1063` (main loop), `market_view.py:1092-1096` (open positions)
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `None` from `adapter.model_prob` (Task 2), `kelly.best_side` returning `None` (Task 3).
- Produces: no new API. An unpriceable contract renders with `—` in the model column, no signal, and is absent from `picks` (Top-3) and `holds` (safe-hold).

This is where the bug actually cost money. The guard **must be the first statement in the loop body**: both `edge_no = (1 - p) - na` and the holds loop's `("NO", 1 - p, na)` raise `TypeError` on `None`.

The abstain row must carry the **same seven keys** as the priced path (`market_view.py:1067-1075`) or `pd.DataFrame(rows)` produces ragged columns: `"Contract"`, `"Model %"`, `"Yes (Bid/Ask)"`, `"No (Bid/Ask)"`, `"Spread"`, `"Last"`, `"Signal"`. Helpers: `cents(x)` (`:625`) renders `None` as `—`; `spread_c(ask, bid)` (`:629`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`. Note the streamlit stub guard — `market_view` imports streamlit at module load and it isn't installed locally:

```python
import sys

try:
    import streamlit  # noqa: F401
except ModuleNotFoundError:
    from unittest.mock import MagicMock
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import market_view


class _AbstainAdapter:
    """model_prob returns each contract's stashed p — None means unpriceable."""
    def model_prob(self, probs, c):
        return c["p"]


def test_unpriceable_contract_is_not_a_kelly_pick():
    # The phantom-edge scenario: a near-certain YES bucket the model can't
    # price, priced cheap on the NO side. It must NOT become a pick.
    contracts = [{"label": "54-55", "p": None, "yes_ask": 0.85, "no_ask": 0.15}]
    assert market_view._kelly_pick(contracts, {}, _AbstainAdapter()) is None


def test_priceable_contract_still_picked_alongside_unpriceable():
    contracts = [
        {"label": "54-55", "p": None, "yes_ask": 0.85, "no_ask": 0.15},
        {"label": "90-91", "p": 0.70, "yes_ask": 0.55, "no_ask": 0.42},
    ]
    pick = market_view._kelly_pick(contracts, {}, _AbstainAdapter())
    assert pick is not None
    assert pick[0]["label"] == "90-91"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py::test_unpriceable_contract_is_not_a_kelly_pick -v`

Expected: PASS already — Task 3's `best_side` guard covers `_kelly_pick`. This is a **regression guard**, confirming the Task 3 fix reaches this consumer. If it FAILS with `TypeError`, Task 3 was not applied correctly.

- [ ] **Step 3: Write the implementation**

In `market_view.py`, in the main contract loop (starting ~line 1036), insert the guard as the **first** statement after `p = adapter.model_prob(probs, c)`:

```python
        for c in contracts:
            p = adapter.model_prob(probs, c)
            # The model can't price this contract — it falls inside an
            # open-ended bin tail. Show the market, abstain on the model:
            # never a signal, a Top-3 pick, a safe hold, or a Kelly size.
            # (A 0 here would read as "impossible" and manufacture a
            # huge phantom edge on the opposite side.)
            if p is None:
                rows.append({
                    "Contract": c["label"],
                    "Model %": "—",
                    "Yes (Bid/Ask)": f"{cents(c['yes_bid'])}/{cents(c['yes_ask'])}",
                    "No (Bid/Ask)": f"{cents(c['no_bid'])}/{cents(c['no_ask'])}",
                    "Spread": "—",
                    "Last": cents(c["last"]),
                    "Signal": "—",
                })
                continue
            ya, na = c["yes_ask"], c["no_ask"]
```

The guard reads `c["yes_bid"]` / `c["no_bid"]` directly because the loop's `ya, na = ...` and
`yb, nb = ...` unpacking happens *after* it.

Then in the open-positions box (~line 1092), replace the exception-driven path with an explicit check:

```python
            for p in open_here:
                yes_p = adapter.model_prob(probs, p)
                if yes_p is None:
                    model_pct = "—"
                else:
                    side_p = yes_p if p["side"] == "yes" else 1 - yes_p
                    model_pct = f"{side_p*100:.0f}%"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py -v`
Expected: 17 passed.

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **342 passed** (325 + 17), same 4 failures / 3 errors.

- [ ] **Step 5: Commit**

```bash
git add market_view.py tests/test_season_bins.py
git commit -m "fix: show an em-dash, not a phantom edge, when the model abstains

An unpriceable contract used to read as 0% -> BUY NO +85 on a
near-certain YES bucket, topping Top-3 and getting sized by Kelly.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Widen the bin range to −10..115

**Files:**
- Modify: `config.py:34-35`
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `model.bin_temp` parsing (Task 1) — without it this task corrupts every historical row.
- Produces: `config.BIN_LOW = -10`, `config.BIN_HIGH = 115`; `config.bin_labels()` → 126 labels, `"<= -10"` … `">= 115"`.

This is the payload; Tasks 1–5 are what make it safe. 51% of DFW days (11yr, 4,018 days) have a min ≤ 60, and the sample's extremes are −2°F and 110°F against all-time records of about −8°F and 113°F. −10..115 clears both with margin, so the tails hold negligible mass on any real day and the Task 2 guard should never fire in practice.

Cost is ~3.8ms per `_bin_probabilities` call (2.51 → 6.32 ms, measured best-of-5 with warmup), called a handful of times per render.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`:

```python
import config


def test_bin_range_brackets_dfw_climate():
    # DFW all-time records are about -8 and 113; the sample low is -2 (Feb 2021)
    # and the sample high is 110. The range must clear both with margin.
    assert config.BIN_LOW == -10
    assert config.BIN_HIGH == 115


def test_bin_labels_span_the_new_range():
    labels = config.bin_labels()
    assert labels[0] == "<= -10"
    assert labels[-1] == ">= 115"
    assert len(labels) == 126


def test_september_front_low_is_now_priceable():
    # THE regression this whole change exists for. Under the old range this
    # distribution was '<= 60': ~1.0 and P(low <= 55) came back a confident 0.
    from settlement import bin_for_temp
    assert bin_for_temp(55) == "55"          # 55 is its own bin now, not a tail
    probs = {lbl: 0.0 for lbl in config.bin_labels()}
    probs["55"] = 0.6
    probs["56"] = 0.4
    p = model.prob_at_most(probs, 55)
    assert p is not None
    assert abs(p - 0.6) < 1e-9


def test_hot_tail_contract_is_now_priceable():
    # 3 of 4018 days hit >= 110; 111 used to be unpriceable.
    from settlement import bin_for_temp
    assert bin_for_temp(111) == "111"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py -v -k "range or span or september or hot_tail"`
Expected: all 4 FAIL — `assert 60 == -10`, `assert '<= 60' == '<= -10'`, `assert '<= 60' == '55'`, `assert '>= 110' == '111'`.

- [ ] **Step 3: Write minimal implementation**

Replace `config.py:30-35` (the comment block and the two constants):

```python
# --- Market bins ---
# Settlement rounds to a whole degree F, so each integer degree is its own bin:
# the bin labelled T captures the event round(daily_high) == T. The two tails
# capture "<= LOW" and ">= HIGH".
#
# The range brackets DFW's CLIMATE, not just the currently listed market. The
# tails are open-ended: a query that needs to resolve INSIDE one can't be
# answered and the model abstains (model.prob_at_most / prob_at_least), so a
# range that real weather reaches would cost live pricing. DFW's all-time
# records are about -8F and 113F; 11 years of dailies (2015-2025) span -2F to
# 110F. -10..115 clears both with margin, so the tails stay negligible.
BIN_LOW = -10   # lowest explicit integer-degree bin
BIN_HIGH = 115  # highest explicit integer-degree bin
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py -v`
Expected: 21 passed.

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **346 passed** (325 + 21), same 4 failures / 3 errors.

**If `tests/test_settlement.py::test_bin_for_temp` fails**, it asserts `bin_for_temp(40) == "<= 60"` and `bin_for_temp(130) == ">= 110"` (`tests/test_settlement.py:137-138`). Those assertions encode the OLD range. Update them to the new range — 40 is now its own bin:

```python
    assert S.bin_for_temp(-20) == f"<= {S.BIN_LOW}"
    assert S.bin_for_temp(130) == f">= {S.BIN_HIGH}"
```

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_season_bins.py tests/test_settlement.py
git commit -m "feat: widen the bin range to -10..115 to bracket DFW's climate

51% of DFW days (11yr, 4018 days) have a min at or below 60, so more
than half the year the low sat in an open-ended tail the model couldn't
price. The hot tail is reachable this summer (3 days >= 110).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Collateral — `scoring.within1` and `edge_report.is_boundary`

**Files:**
- Modify: `scoring.py:128`, `edge_report.py:29-32`
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: `model.bin_temp` (Task 1), `config.BIN_LOW`/`BIN_HIGH` (Task 6).
- Produces: no signature changes.

Two independent leftovers that both hardcode the old range.

`scoring.py:128` computes `within1` as `abs(LABELS.index(peak_label) - LABELS.index(actual_label)) <= 1`. After Task 6, `LABELS` no longer contains `"<= 60"`, so a legacy row peaking in a tail would raise `ValueError`. The live data branch has no such row today (164 rows, verified — none peak in a tail), so this is defensive, but the `bin_temp` form is both equivalent and immune: index distance and temperature distance agree on interior bins *and* tails (`LABELS.index("<= 60")=0, index("61")=1` → 1; `bin_temp` → 60, 61 → 1).

`edge_report.is_boundary` hardcodes `range(60, 120, 2)`. It's analysis-only (Plan C's boundary slice), never a live bet, but it misfiles any consensus below 60.5 as mid-bin.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_season_bins.py`:

```python
import edge_report


def test_is_boundary_edges_follow_the_config_range():
    # A September front low near a Kalshi even|odd edge must register as a
    # boundary case; the old hardcoded range(60, 120, 2) missed everything <60.
    assert edge_report.is_boundary(58.5) is True     # on the 58|59 edge
    assert edge_report.is_boundary(58.0) is True     # 0.5 away
    assert edge_report.is_boundary(57.4) is False    # 0.9 from 56.5 and 58.5


def test_is_boundary_unchanged_in_the_old_range():
    # tests/test_edge_report.py:34 assertions must keep holding.
    assert edge_report.is_boundary(96.5) is True
    assert edge_report.is_boundary(97.0) is True
    assert edge_report.is_boundary(95.4) is False
    assert edge_report.is_boundary(97.6) is False


def test_within1_matches_index_distance_on_legacy_tail_labels():
    # bin_temp distance == LABELS.index distance, but can't ValueError on a
    # legacy label absent from the widened LABELS.
    assert abs(model.bin_temp("<= 60") - model.bin_temp("61")) == 1
    assert abs(model.bin_temp("108") - model.bin_temp(">= 110")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_season_bins.py -v -k "boundary or within1"`
Expected: `test_is_boundary_edges_follow_the_config_range` FAILS — `is_boundary(58.5)` returns `False` (nearest hardcoded edge is 60.5, distance 2.0).

- [ ] **Step 3: Write minimal implementation**

In `edge_report.py`, replace `is_boundary` (lines 29-32):

```python
def is_boundary(consensus: float, half_width: float = 0.5) -> bool:
    """True when consensus is within half_width of an even|odd Kalshi edge (even+0.5).

    Edges follow the model's bin range rather than a hardcoded span, so a winter
    or front-day consensus is classified on the same footing as a summer one.
    """
    start = BIN_LOW if BIN_LOW % 2 == 0 else BIN_LOW + 1
    edges = [e + 0.5 for e in range(start, BIN_HIGH + 1, 2)]
    return min(abs(consensus - e) for e in edges) <= half_width
```

Add the import at the top of `edge_report.py` (check the existing import block and match its style):

```python
from config import BIN_HIGH, BIN_LOW
```

In `scoring.py`, replace line 128:

```python
        within1 = abs(bin_temp(peak_label) - bin_temp(actual_label)) <= 1
```

Add `bin_temp` to `scoring.py`'s imports. It currently has `from backtest import contract_points, reliability_bins, _brier, LABELS` (line 17) — add:

```python
from model import bin_temp
```

Leave the `LABELS` import in place only if still used elsewhere in the file; if line 128 was its sole use, drop `LABELS` from the import to avoid an unused name. Check with: `grep -n LABELS scoring.py`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_season_bins.py tests/test_edge_report.py tests/test_accuracy.py -v`
Expected: all pass (24 in `test_season_bins.py`).

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **349 passed** (325 + 24), same 4 failures / 3 errors.

- [ ] **Step 5: Commit**

```bash
git add scoring.py edge_report.py tests/test_season_bins.py
git commit -m "fix: within1 and is_boundary follow the bin range instead of hardcoding it

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Characterization — summer behavior is unchanged

**Files:**
- Test: `tests/test_season_bins.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no production code. This task is a proof, not a change.

The spec's central claim is that widening the range doesn't move summer output: the old tails held ≤0.55% mass on the worst logged row (164 rows on the live data branch, measured), and that mass merely redistributes into now-explicit bins. Prove it rather than assert it.

`prob_table` also needs pinning: the spec argues it can never yield `None` because its thresholds are `bin_temp` of the dict's *own* labels, which land exactly on tail edges rather than inside them.

- [ ] **Step 1: Write the test**

Append to `tests/test_season_bins.py`:

```python
def test_summer_day_probabilities_are_effectively_unchanged():
    # A typical summer high near 97: the old 60..110 tails held ~0 mass, so
    # widening must not move the distribution.
    samples = [95.0 + i * 0.3 for i in range(40)]
    weights = [1.0] * len(samples)
    probs = model._bin_probabilities(samples, 2.0, weights)

    assert abs(sum(probs.values()) - 1.0) < 1e-9        # still normalized
    assert probs["<= -10"] < 1e-12                      # tails hold nothing
    assert probs[">= 115"] < 1e-12
    # Mass sits where it did before, in the explicit bins.
    assert sum(v for k, v in probs.items()
               if k not in ("<= -10", ">= 115")) > 0.999


def test_prob_table_thresholds_never_abstain():
    # prob_table feeds bin_temp of the dict's OWN labels back into the
    # cumulative helpers; those land ON tail edges, never inside them.
    samples = [95.0 + i * 0.3 for i in range(40)]
    probs = model._bin_probabilities(samples, 2.0, [1.0] * len(samples))
    for label in probs:
        t = model.bin_temp(label)
        assert model.prob_at_least(probs, t) is not None
        assert model.prob_at_most(probs, t) is not None
```

- [ ] **Step 2: Run the test**

Run: `python3 -m pytest tests/test_season_bins.py -v -k "unchanged or never_abstain"`
Expected: both PASS. They should pass on the first run — that is the point. If `test_summer_day_probabilities_are_effectively_unchanged` fails, the widening moved real mass and the range choice needs revisiting before shipping.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest -q --continue-on-collection-errors 2>&1 | tail -3`
Expected: **351 passed** (325 + 26), plus the same 4 `test_bet_view` failures and 3 collection errors as the baseline. **Any other failure is a real regression.**

- [ ] **Step 4: Commit**

```bash
git add tests/test_season_bins.py
git commit -m "test: characterize summer output as unchanged by the wider bin range

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification Before Merge

The dashboard cannot be launched locally (no `streamlit`, disk full — `pip install` fails with `[Errno 28]`). So `market_view`'s rendering is covered by the pure-logic tests above, and the visual check happens on deploy.

- [ ] Full suite: **351 passed**, 4 known failures, 3 known collection errors.
- [ ] Sanity-check the original bug is dead:

```bash
python3 -c "
import model, config
probs = {lbl: 0.0 for lbl in config.bin_labels()}
probs['55'] = 0.6; probs['56'] = 0.4
print('P(low <= 55)          ->', model.prob_at_most(probs, 55))
print('Kalshi 55-or-below    ->', model.prob_for_strike(probs, 'less', None, 56))
print('legacy row abstains   ->', model.prob_at_most({'<= 60': 1.0}, 59))
"
```
Expected:
```
P(low <= 55)          -> 0.6
Kalshi 55-or-below    -> 0.6
legacy row abstains   -> None
```

- [ ] Confirm no historical row broke:

```bash
python3 -c "
import json, scoring, model
rows = [json.loads(l) for l in open('forecast_log.jsonl') if l.strip()]
bad = [r for r in rows if not r.get('probabilities')]
print('rows:', len(rows), 'without probabilities:', len(bad))
for r in rows[:5]:
    peak = max(r['probabilities'], key=r['probabilities'].get)
    print(' ', r['target_date'], r['variable'], 'peak', peak, '->', model.bin_temp(peak))
"
```
Expected: every peak label resolves to the temperature printed in the label (e.g. `peak 90 -> 90`), with no `ValueError`.

- [ ] On deploy, confirm the Kalshi page still renders both markets with model percentages (no `—` on a normal summer day — the guard should never fire).
