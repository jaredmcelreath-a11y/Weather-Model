# Kalshi Gap-Uncertainty σ Inflation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Kalshi page's false locked-day confidence by widening σ with the calibrated CLI-gap std, keeping the +1 offset center.

**Architecture:** `calibration` stores the per-variable gap std (`high_std`/`low_std`) in `settlement_offset`; `model.predict_variable` and `backtest.run` add it in quadrature to σ whenever the offset is applied. The std rides through the existing `settlement_offset` plumbing, so only `calibration.py`, `model.py`, `backtest.py` (+ tests) change. Robinhood passes no offset → unchanged.

**Tech Stack:** Python 3.9, pytest. Spec: `docs/superpowers/specs/2026-06-21-kalshi-gap-uncertainty-design.md`. Parts A & B already on main.

---

## File Structure
- **`calibration.py`** (modify) — `_settlement_offset` also returns `high_std`/`low_std`.
- **`model.py`** (modify) — `predict_variable` inflates σ by `gap_std` when `settle_offset` set.
- **`backtest.py`** (modify) — `run(cli=True)` applies the same σ inflation.
- **Tests:** extend `tests/test_cli_basis.py` (update 2 existing assertions + add 3 tests).

Robinhood unchanged: σ inflation is gated on `settle_offset`/`cli`, both off for Robinhood.

---

## Task 1: Calibrate the gap std

**Files:**
- Modify: `calibration.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Update/add the failing tests**

In `tests/test_cli_basis.py`, in the EXISTING `test_settlement_offset_means_the_cli_minus_hourly_gap`, add two assertions after the existing ones:
```python
    assert off["high_std"] == 0.0   # high gaps [1, 1] -> std 0
    assert off["low_std"] == 1.0    # low gaps [0, -2] -> std 1.0
```
And change the EXISTING `test_settlement_offset_zero_when_no_overlap` assertion:
```python
    off = _settlement_offset({date(2026, 6, 8): (95.0, 78.0)}, {})
    assert off == {"high": 0.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0, "n_days": 0}
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -k settlement_offset -q`
Expected: FAIL — `KeyError: 'high_std'` (and the no-overlap dict mismatch).

- [ ] **Step 3: Implement in `calibration.py`**

Add a small helper just above `_settlement_offset`:
```python
def _mean_std(xs: list[float]) -> tuple[float, float]:
    """Population mean and std, each rounded to 2 dp."""
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return round(m, 2), round(var ** 0.5, 2)
```

Replace the body of `_settlement_offset` so it returns the stds too:
```python
def _settlement_offset(cli: dict, hourly: dict) -> dict:
    """Mean and std of the (CLI - hourly) daily-extreme gap, per variable.

    The Kalshi page adds the mean to the hourly forecast (to reach the CLI
    settlement basis) and the std in quadrature to its spread (the gap is an
    unobservable average, not exact). Zeros when there is no overlapping
    history (safe degrade to current behavior)."""
    dh, dl = [], []
    for day, (chi, clo) in cli.items():
        if day not in hourly:
            continue
        hhi, hlo = hourly[day]
        dh.append(chi - hhi)
        dl.append(clo - hlo)
    if not dh:
        return {"high": 0.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0, "n_days": 0}
    hm, hs = _mean_std(dh)
    lm, ls = _mean_std(dl)
    return {"high": hm, "low": lm, "high_std": hs, "low_std": ls, "n_days": len(dh)}
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_cli_basis.py
git commit -m "calibration: settlement_offset carries the gap std (high_std/low_std)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Inflate σ in the model

**Files:**
- Modify: `model.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli_basis.py`:

```python
def test_settle_offset_std_widens_sigma_without_moving_center():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0})
    wide = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0, "high_std": 2.0, "low_std": 0.0})
    assert wide["consensus"] == base["consensus"]    # center unchanged
    assert wide["sigma_used"] > base["sigma_used"]   # gap std widened sigma


def test_settle_offset_zero_std_matches_no_std():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    a = model.predict_variable(series, obs, day, "high", None, None,
                               {"high": 1.0, "low": 0.0})
    b = model.predict_variable(series, obs, day, "high", None, None,
                               {"high": 1.0, "low": 0.0, "high_std": 0.0, "low_std": 0.0})
    assert a == b
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py::test_settle_offset_std_widens_sigma_without_moving_center -q`
Expected: FAIL — `sigma_used` is equal (no inflation yet), so `wide > base` is False.

- [ ] **Step 3: Implement in `model.py`**

In `predict_variable`, immediately AFTER the line
`sigma = max(sigma_base * locked_ratio, _SIGMA_FLOOR)` and BEFORE
`probs = _bin_probabilities(samples, sigma)`, add:
```python
    # The CLI settlement offset is an average; its gap has irreducible spread
    # (std from calibration) we can't observe live, so widen sigma by it in
    # quadrature whenever the offset is applied. Center (consensus) is unchanged.
    if settle_offset:
        gap_std = settle_offset.get(f"{variable}_std", 0.0)
        if gap_std:
            sigma = math.hypot(sigma, gap_std)
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: all pass.

- [ ] **Step 5: Run the FULL suite (Robinhood-unchanged regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (no `settle_offset` / no `*_std` ⇒ σ identical to before).

- [ ] **Step 6: Commit**

```bash
git add model.py tests/test_cli_basis.py
git commit -m "model: widen sigma by the CLI gap std when settle_offset is set

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Inflate σ in the CLI backtest

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_cli_basis.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli_basis.py`:

```python
def test_backtest_cli_std_widens_distribution(monkeypatch):
    day = date(2026, 6, 10)
    series = {"det_a": _member(day, 90.0)}
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: series)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {day: (91.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    narrow = backtest.run(cli=True, settle_offset={"high": 1.0, "low": 0.0})
    wide = backtest.run(cli=True,
                        settle_offset={"high": 1.0, "low": 0.0,
                                       "high_std": 3.0, "low_std": 0.0})
    # consensus is centered on the actual (91), so a wider sigma -> higher CRPS
    assert wide["high"]["crps"] > narrow["high"]["crps"]
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py::test_backtest_cli_std_widens_distribution -q`
Expected: FAIL — CRPS equal (no σ inflation yet).

- [ ] **Step 3: Implement in `backtest.py`**

In `run`, inside the `for var in ("high", "low"):` loop, immediately AFTER the
existing `off = (settle_offset or {}).get(var, 0.0) if cli else 0.0` line, add:
```python
        if cli:
            sigma = math.hypot(sigma, (settle_offset or {}).get(f"{var}_std", 0.0))
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_cli_basis.py -q`
Expected: all pass.

- [ ] **Step 5: Run the FULL suite (hourly-unchanged regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (`cli=False` ⇒ no inflation, backtest unchanged).

- [ ] **Step 6: Commit**

```bash
git add backtest.py tests/test_cli_basis.py
git commit -m "backtest: widen CLI-basis sigma by the gap std (match live model)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** gap std in calibration (Task 1) ← spec §1; model σ inflation in quadrature on both paths (Task 2) ← spec §2; backtest σ inflation when cli (Task 3) ← spec §3. No `app.py`/`scheduled_log.py`/`market_view.py` changes (spec: std rides existing plumbing) — correct, none planned. Back-compat (missing `*_std` → 0 → no inflation) covered by `test_settle_offset_zero_std_matches_no_std` and the full-suite regressions. Robinhood-unchanged = σ gated on `settle_offset`/`cli`.
- **Type consistency:** keys `high_std`/`low_std` produced by `_settlement_offset` (Task 1) are read as `settle_offset.get(f"{variable}_std", 0.0)` in `model.py` (Task 2) and `(settle_offset or {}).get(f"{var}_std", 0.0)` in `backtest.py` (Task 3) — consistent. `math.hypot` used in both; `math` already imported in `model.py` and `backtest.py`.
- **Placeholder scan:** none — every code step is complete.
- **Center-unchanged invariant:** σ inflation never touches `samples`/`consensus`; Task 2 test asserts `consensus` equal while `sigma_used` grows.
