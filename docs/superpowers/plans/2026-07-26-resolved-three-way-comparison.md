# Resolved Three-Way Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a convergence-driven "hybrid" Resolved number, make it the live human-facing figure, and show it beside the current and original formulas (plus log all three) so the best one can be chosen from a few days of data.

**Architecture:** Add two new fields to the per-variable model output (`resolved_hybrid`, `resolved_orig`) next to the existing `resolved`/`resolved_collapse`. Parameterize the display helper so the card can render each formula with the right caps. Switch only the human-facing consumers (card, lock badge, captions) to the hybrid; leave the automated consumers (70% ntfy alert, auto-trader) on the current number. Log all three raw values per intraday snapshot for later review.

**Tech Stack:** Python 3.11, Streamlit 1.50, pytest. No new dependencies.

## Global Constraints

- Do NOT upgrade `cryptography` (must stay ≤ 38.x) or other pinned deps.
- The full test suite (~764 tests) must stay green; run `pytest -q`.
- `render_variable` is shared by both cities (KDFW, KAUS) — a single change covers both; no per-city duplication.
- `displayed_resolved(d)` with no `which` argument MUST keep its current behavior byte-for-byte (existing callers, incl. the 70% alert, depend on it).
- The auto-trader (`trade_logic.entry_allowed`) and the 70% ntfy alert (`scheduled_log._maybe_alert_resolved`) MUST keep reading the **current** `resolved`, not the hybrid.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Work on branch `resolved-three-way-compare` (already created).
- UI verification uses the `verify` skill (local Streamlit + headless screenshot), including a 390px-wide mobile check.

---

### Task 1: Model — `resolved_hybrid` and `resolved_orig` fields

**Files:**
- Modify: `model.py` — add pure helper `_resolved_variants`, call it in `predict_variable` (def at `model.py:532`; `locked_ratio` at `:759`, `resolved`/`resolved_collapse` block at `:771-781`, return dict at `:886-905`).
- Test: `tests/test_resolved_hybrid.py` (create)

**Interfaces:**
- Produces: `model._resolved_variants(locked_ratio: float, collapse: float) -> tuple[float, float]` returning `(orig, hybrid)`. And two new keys in the `predict_variable` return dict: `"resolved_orig"`, `"resolved_hybrid"` (both floats 0.0–1.0, rounded 2 dp).

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolved_hybrid.py`:

```python
"""The two extra Resolved formulations for the live three-way comparison."""
import model


def test_pre_obs_is_zero():
    # Pre-observation: locked_ratio == 1.0, collapse == 0.0.
    assert model._resolved_variants(1.0, 0.0) == (0.0, 0.0)


def test_orig_is_one_minus_locked_ratio():
    orig, _ = model._resolved_variants(0.3, 0.4)
    assert orig == 0.7


def test_hybrid_hits_one_when_fully_converged():
    # locked_ratio -> 0 means the ensemble has fully collapsed.
    _, hybrid = model._resolved_variants(0.0, 0.0)
    assert hybrid == 1.0


def test_hybrid_hits_one_when_fully_ruled_out():
    # collapse -> 1 means observations have ruled out all other mass.
    _, hybrid = model._resolved_variants(0.5, 1.0)
    assert hybrid == 1.0


def test_hybrid_beats_collapse_alone_at_peak_near_mean():
    # Peak landing at the forecast mean -> collapse ~0.5, but the ensemble is
    # half-converged, so the hybrid still resolves past 0.5.
    _, hybrid = model._resolved_variants(0.5, 0.5)
    assert hybrid == 0.75
    assert hybrid > 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resolved_hybrid.py -v`
Expected: FAIL with `AttributeError: module 'model' has no attribute '_resolved_variants'`

- [ ] **Step 3: Write minimal implementation**

In `model.py`, add the helper just above `predict_variable` (before `model.py:532`):

```python
def _resolved_variants(locked_ratio: float, collapse: float) -> tuple[float, float]:
    """Two extra 'Resolved' readings for the live three-way comparison.

    orig   = 1 - locked_ratio                     (the month-ago metric; a pure
             ensemble-convergence readout, can be non-monotonic)
    hybrid = 1 - (1 - collapse) * locked_ratio    (convergence-driven; no clock
             term). Reaches 1.0 when EITHER the ensemble fully converges
             (locked_ratio -> 0) OR observations rule out all mass (collapse -> 1).

    Both are 0.0 pre-observation (locked_ratio == 1.0, collapse == 0.0).
    """
    orig = 1.0 - locked_ratio
    hybrid = 1.0 - (1.0 - collapse) * locked_ratio
    return orig, hybrid
```

In `predict_variable`, right after the existing `resolved`/`resolved_collapse` block (after `model.py:781`), compute the variants:

```python
    resolved_orig, resolved_hybrid = _resolved_variants(locked_ratio, resolved_collapse)
```

In the return dict, add the two keys immediately after the existing `"resolved_collapse"` line (`model.py:893`):

```python
        "resolved_orig": round(resolved_orig, 2),
        "resolved_hybrid": round(resolved_hybrid, 2),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resolved_hybrid.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_resolved_hybrid.py
git commit -m "feat: add resolved_hybrid + resolved_orig model fields

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `displayed_resolved(d, which=...)`

**Files:**
- Modify: `model.py:1211-1232` (`displayed_resolved`)
- Test: `tests/test_model_displayed_resolved.py` (extend)

**Interfaces:**
- Consumes: `"resolved"`, `"resolved_hybrid"`, `"resolved_orig"` keys from Task 1.
- Produces: `model.displayed_resolved(d, which: str = "current") -> int`. `which` ∈ {`"current"`, `"hybrid"`, `"original"`}. `"current"`/`"hybrid"` apply the convective/front + dawn-low caps; `"original"` is uncapped. Default `"current"` is unchanged from before.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_model_displayed_resolved.py`:

```python
def test_default_is_current_and_unchanged():
    d = {"resolved": 0.72}
    assert model.displayed_resolved(d) == 72
    assert model.displayed_resolved(d, "current") == 72


def test_hybrid_reads_hybrid_field_and_is_capped():
    d = {"resolved_hybrid": 1.0, "convective_widened": True}
    assert model.displayed_resolved(d, "hybrid") == model.CONVECTIVE_RESOLVED_CAP
    d2 = {"resolved_hybrid": 1.0, "low_forming": True}
    assert model.displayed_resolved(d2, "hybrid") == model.LOW_FORMING_RESOLVED_CAP


def test_original_reads_orig_field_and_is_uncapped():
    d = {"resolved_orig": 1.0, "convective_widened": True, "low_forming": True}
    assert model.displayed_resolved(d, "original") == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_displayed_resolved.py -v`
Expected: FAIL — `test_hybrid_reads_hybrid_field_and_is_capped` / `test_original_...` error on the unexpected `which` argument.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `displayed_resolved` (`model.py:1211-1232`):

```python
def displayed_resolved(d, which: str = "current"):
    """Resolved % for the metric card, clamped on a convective- or front-risk day.

    `which` selects the formula for the live three-way comparison:
      "current"  -> d["resolved"]        (capped)  [default; existing callers]
      "hybrid"   -> d["resolved_hybrid"] (capped)  [the live headline number]
      "original" -> d["resolved_orig"]   (UNCAPPED; faithful to a month ago)

    `resolved` measures how much of the *diurnal* uncertainty is settled and hits
    100% once the extreme's window closes. But on a storm day the low's daily min
    can still be reset lower by evening convection (convective.py), or when a forecast
    front is active, the low may be undercut by a colder post-noon reading — either way,
    a locked dawn trough is not a resolved low. Cap the display so the metric stops
    contradicting the risk caption. Display-only — the raw fields and the
    probabilities are untouched. The original formula keeps its month-ago identity
    (uncapped) so the comparison shows each as it really behaves."""
    key = {"current": "resolved", "hybrid": "resolved_hybrid",
           "original": "resolved_orig"}[which]
    pct = int(d.get(key, 1 - d.get("locked_ratio", 0.0)) * 100)
    if which == "original":
        return pct
    if d.get("convective_widened") or d.get("front_widened"):
        pct = min(pct, CONVECTIVE_RESOLVED_CAP)
    # Dawn low still forming: the clock term inflates the card toward 90% before
    # the trough is physically in. Until it locks, cap at "half-open, wait".
    # Older snapshots lack the flag and are untouched.
    if d.get("low_forming"):
        pct = min(pct, LOW_FORMING_RESOLVED_CAP)
    return pct
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_displayed_resolved.py -v`
Expected: PASS (all, including the pre-existing cases)

- [ ] **Step 5: Commit**

```bash
git add model.py tests/test_model_displayed_resolved.py
git commit -m "feat: parameterize displayed_resolved (current/hybrid/original)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Forecast card — two rows (Consensus|Spread, then Hybrid|Current|Original)

**Files:**
- Modify: `market_view.py:1217-1232` (`render_variable` card rows) and the mobile CSS block (`market_view.py:127-140`, add a `pair_` sibling rule).
- Verify: `verify` skill (Forecast page, both cities, incl. 390px mobile)

**Interfaces:**
- Consumes: `model.displayed_resolved(d, which)` from Task 2.
- Produces: no Python interface; a two-row metric layout per variable.

- [ ] **Step 1: Add the mobile CSS for the 2-column row**

The existing trio CSS targets `[class*="st-key-mini_"]` and forces 33%-wide columns — reused as-is by naming the new trio container `mini_resolved3_<variable>`. Row 1 becomes two columns, so add a sibling `pair_` rule. Insert immediately after `market_view.py:140` (the last `st-key-mini_` `.wxcard-l` rule):

```python
        # Row-1 Consensus|Spread pair: keep the two side by side on phones (50/50)
        "[class*=\"st-key-pair_\"] [data-testid=\"stHorizontalBlock\"]"
        "{flex-wrap:nowrap!important;gap:0.35rem!important;}"
        "[class*=\"st-key-pair_\"] [data-testid=\"stColumn\"]"
        "{flex:1 1 50%!important;min-width:0!important;width:50%!important;}"
        "[class*=\"st-key-pair_\"] .wxcard{padding:0.5rem 0.4rem 0.55rem!important;}"
        "[class*=\"st-key-pair_\"] .wxcard-v{font-size:1.1rem!important;}"
        "[class*=\"st-key-pair_\"] .wxcard-l{font-size:0.66rem!important;white-space:nowrap!important;}"
```

- [ ] **Step 2: Replace the card metric rows**

Replace `market_view.py:1217-1232` (from the `# keyed so a mobile CSS rule...` comment through the Resolved `c3.markdown(...)` call) with:

```python
        # Row 1: Consensus | Spread (keyed so a mobile CSS rule keeps the pair
        # on one row rather than stacking).
        with st.container(key=f"pair_{variable}"):
            c1, c2 = st.columns(2)
        c1.markdown(metric_card("Consensus", f"{d['consensus']}°F"), unsafe_allow_html=True)
        c2.markdown(metric_card("Spread", f"{d['sigma_used']}°F (±1σ)",
                    "One standard deviation of the model's forecast — its error "
                    "bars. About 68% of outcomes should land within ±this of the "
                    "consensus, ~95% within ±2σ. Wider = more uncertain; this is "
                    "what turns the consensus into contract probabilities. It gets "
                    "inflated for day-ahead forecasts until the scoring log matures."),
                    unsafe_allow_html=True)
        # Row 2 (TEMPORARY three-way Resolved comparison, 2026-07-26): Hybrid is
        # the live number (drives the lock badge + captions); Current + Original
        # are shown to pick a winner over the next few days. Collapse back to a
        # single "Resolved" once chosen. Container keyed `mini_...` so it reuses
        # the existing trio CSS that holds three on one row on phones.
        st.caption("Resolved — comparing three formulas (temporary). Hybrid is the live number.")
        with st.container(key=f"mini_resolved3_{variable}"):
            r1, r2, r3 = st.columns(3)
        r1.markdown(metric_card("Hybrid", f"{displayed_resolved(d, 'hybrid')}%",
                    "Convergence-driven Resolved — the live number. Rises as the "
                    "models collapse onto the extreme; no time-of-day component."),
                    unsafe_allow_html=True)
        r2.markdown(metric_card("Current", f"{displayed_resolved(d, 'current')}%",
                    "The current monotonic Resolved (part clock-driven). Comparison only."),
                    unsafe_allow_html=True)
        r3.markdown(metric_card("Original", f"{displayed_resolved(d, 'original')}%",
                    "The month-ago Resolved (1 − locked_ratio), uncapped. Comparison only."),
                    unsafe_allow_html=True)
```

- [ ] **Step 3: Run the full suite (no display regressions)**

Run: `pytest -q`
Expected: PASS (no test asserts the old single-Resolved card layout)

- [ ] **Step 4: Verify the UI**

Use the `verify` skill to run the dashboard locally and screenshot the Forecast page for both cities. Confirm: Row 1 shows Consensus | Spread; Row 2 shows the caption + Hybrid | Current | Original trio; the trio stays on one row at 390px wide (no stacking/overflow).

- [ ] **Step 5: Commit**

```bash
git add market_view.py
git commit -m "feat: three-way Resolved comparison on the Forecast card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Human-facing consumers read the hybrid

**Files:**
- Modify: `market_view.py:1012` (`lock_status` resolved read) and `market_view.py:1228` (card lock badge).
- Test: `tests/test_lock_status_hybrid.py` (create)

**Interfaces:**
- Consumes: `"resolved_hybrid"` (Task 1), `displayed_resolved(d, "hybrid")` (Task 2).
- Produces: `lock_status(d, variable)` unchanged signature; its internal resolved figure and the badge now derive from the hybrid.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lock_status_hybrid.py`:

```python
"""The lock badge/captions read the hybrid Resolved, not the current one."""
from market_view import lock_status


def _high(**over):
    d = {"observed_so_far": 100.0, "consensus": 100.0, "sigma_used": 0.7,
         "peak_locked": False, "locked_ratio": 0.1,
         "resolved": 0.10, "resolved_hybrid": 0.90}
    d.update(over)
    return d


def test_badge_uses_hybrid_high():
    # Hybrid 90% clears the 85% "locked" gate even though current is only 10%.
    level, headline, _ = lock_status(_high(), "high")
    assert level == "success"


def test_badge_stays_open_when_hybrid_low():
    # Hybrid 40% keeps it "locking" even though current is 99%.
    level, headline, _ = lock_status(_high(resolved=0.99, resolved_hybrid=0.40), "high")
    assert level == "info"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lock_status_hybrid.py -v`
Expected: FAIL — `test_badge_uses_hybrid_high` gets `info` (still reading current 10%).

- [ ] **Step 3: Write minimal implementation**

In `market_view.py:1012`, change the resolved source in `lock_status` from `resolved` to `resolved_hybrid` (the flag branches above the 85% checks remain the effective caps, exactly as today):

```python
    # Human-facing badge/captions read the HYBRID Resolved (the live number);
    # the convective/front/low_forming branches above still gate the green.
    resolved = int(d.get("resolved_hybrid", 1 - lr) * 100)
```

In `market_view.py:1228`, route the card badge to the hybrid displayed value:

```python
        locked_pct = displayed_resolved(d, "hybrid")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_lock_status_hybrid.py tests/test_lock_status_convective.py tests/test_lock_status_front.py tests/test_low_forming_guard.py -v`
Expected: PASS (new tests pass; the flag-cap tests still pass — they set the flags that short-circuit before the resolved read).

- [ ] **Step 5: Commit**

```bash
git add market_view.py tests/test_lock_status_hybrid.py
git commit -m "feat: lock badge + captions read the hybrid Resolved

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Log all three (raw) per intraday snapshot

**Files:**
- Modify: `consensus_log.py:142-148` (the `rec` dict in `record`)
- Test: `tests/test_consensus_log_resolved.py` (create)

**Interfaces:**
- Consumes: the new model fields (Task 1) via the snapshot's per-variable dict.
- Produces: extra keys on each `consensus_history.jsonl` row: `resolved_hybrid`, `resolved`, `resolved_orig`, `resolved_collapse`, `locked_ratio`, and (when set) `peak_locked`, `low_forming`, `convective_widened`, `front_widened`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_consensus_log_resolved.py`:

```python
"""Consensus history carries the raw three-way Resolved fields for review."""
import json
import consensus_log


def _snapshot():
    d = {"consensus": 100.0, "resolved_hybrid": 0.6, "resolved": 0.8,
         "resolved_orig": 0.5, "resolved_collapse": 0.4, "locked_ratio": 0.2,
         "low_forming": True, "probabilities": {"100": 1.0}}
    return {"updated": "2026-07-26T14:00:00-05:00", "station": "KDFW",
            "current": {"temp": 99.0},
            "today": {"day": "2026-07-26", "high": d, "low": dict(d)}}


def test_resolved_fields_and_flags_persisted(tmp_path):
    path = str(tmp_path / "consensus_history.jsonl")
    consensus_log.record(_snapshot(), path=path, basis="cli", station="KDFW")
    rows = [json.loads(line) for line in open(path)]
    high = next(r for r in rows if r["variable"] == "high")
    assert high["resolved_hybrid"] == 0.6
    assert high["resolved"] == 0.8
    assert high["resolved_orig"] == 0.5
    assert high["resolved_collapse"] == 0.4
    assert high["locked_ratio"] == 0.2
    assert high["low_forming"] is True
    # Unset flags are omitted (kept out of the row), read as falsy via .get().
    assert "convective_widened" not in high
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_consensus_log_resolved.py -v`
Expected: FAIL — `KeyError: 'resolved_hybrid'` (row lacks the fields).

- [ ] **Step 3: Write minimal implementation**

In `consensus_log.py`, immediately after the `rec = {...}` literal (after `consensus_log.py:148`), add:

```python
            # Three-way "Resolved" comparison (temporary experiment, 2026-07-26):
            # persist the RAW (uncapped) values of all three formulas plus the
            # terms + flags, so the winning formula can be picked from the logged
            # intraday tracks. Reconstruct the capped display offline via
            # model.displayed_resolved. Fields omitted when absent (older rows
            # and calm days stay minimal; read via .get()).
            for k in ("resolved_hybrid", "resolved", "resolved_orig",
                      "resolved_collapse", "locked_ratio"):
                v = d.get(k)
                if v is not None:
                    rec[k] = v
            for flag in ("peak_locked", "low_forming",
                         "convective_widened", "front_widened"):
                if d.get(flag):
                    rec[flag] = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_consensus_log_resolved.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus_log.py tests/test_consensus_log_resolved.py
git commit -m "feat: log all three Resolved values per intraday snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Regression fence — automated consumers stay on `current`

**Files:**
- Test: `tests/test_resolved_automated_unchanged.py` (create). No source changes — this locks in that the auto-trader keeps reading the current `resolved`, not the hybrid.

**Interfaces:**
- Consumes: `trade_logic.entry_allowed(var_snap, implied, params, variable)`.

- [ ] **Step 1: Write the test (must pass immediately — it fences existing behavior)**

Create `tests/test_resolved_automated_unchanged.py`:

```python
"""The auto-trader's resolved floor must read the current number, not hybrid."""
import trade_logic


def _params():
    return {"min_resolved": 0.7, "agreement_tol": 2.0}


def test_resolved_floor_uses_current_not_hybrid():
    # Current below the floor, hybrid high -> blocked ON resolved (uses current).
    ok, reason = trade_logic.entry_allowed(
        {"resolved": 0.10, "resolved_hybrid": 0.99, "consensus": 100.0},
        None, _params(), "high")
    assert not ok
    assert reason.startswith("resolved")

    # Current above the floor, hybrid zero -> passes the floor using current 75%
    # (it blocks later for lack of a market center, NOT on resolved).
    ok, reason = trade_logic.entry_allowed(
        {"resolved": 0.75, "resolved_hybrid": 0.0, "consensus": 100.0},
        None, _params(), "high")
    assert "resolved" not in reason
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_resolved_automated_unchanged.py -v`
Expected: PASS (2 passed). If it fails, a prior task wrongly switched an automated consumer to the hybrid — fix that, do not weaken the test.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS (all ~770 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_resolved_automated_unchanged.py
git commit -m "test: fence the auto-trader resolved floor to the current number

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Hybrid formula `1 − (1 − collapse)·locked_ratio` → Task 1. ✓
- Three numbers (hybrid/current/original) → Tasks 1–2. ✓
- Display: three mini-metrics, both cities, mobile-safe → Task 3 (shared `render_variable`; reuses `mini_` CSS + new `pair_` rule; 390px verify step). ✓
- Caps: current+hybrid capped, original uncapped → Task 2. ✓
- Human-facing → hybrid (card badge + captions) → Tasks 3–4. ✓
- Automated stay on current (70% alert + trader) → default `displayed_resolved` (Task 2) + fence (Task 6); no change to `scheduled_log`/`trade_logic`. ✓
- Log all three raw + flags per snapshot → Task 5. ✓
- Temporary/experimental framing → comments in Tasks 3 & 5. ✓
- Tests for hybrid math, caps, guards → Tasks 1, 2, 4, 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code and exact commands. ✓

**Type consistency:** `_resolved_variants(locked_ratio, collapse) -> (orig, hybrid)` used consistently; field names `resolved_hybrid`/`resolved_orig`/`resolved_collapse`/`resolved` identical across model, display, lock_status, and logging; `displayed_resolved(d, which=...)` signature consistent across Tasks 2–4. ✓

**Note:** The 70% ntfy alert (`scheduled_log._maybe_alert_resolved`) already calls `displayed_resolved(d)` with no `which` — Task 2 keeps that default = current, so it needs no edit and is implicitly fenced by `test_default_is_current_and_unchanged`.
