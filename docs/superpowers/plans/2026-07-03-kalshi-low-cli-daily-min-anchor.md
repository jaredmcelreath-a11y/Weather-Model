# CLI daily-min anchor for the Kalshi low — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Kalshi (CLI) page, anchor today's low to the IEM daily-summary CLI min when it is available and colder than the hourly reading, so the consensus and top bin track the value Kalshi actually settles on.

**Architecture:** Two changes in `model.py`. (1) `predict_variable` gains a low-only branch that, on the CLI basis, prefers the whole-°F daily-summary CLI min over the whole-°C 5-min feed and uses the *measured* gap (`cli_low − observed`) instead of the flat average offset — gated so a not-yet-locked low only anchors on the authoritative daily-summary and only when `live`. (2) `gather_series` fetches that daily-summary once per live snapshot (Kalshi only) via a small `try/except` helper and threads it into the obs bundle as `cli_daily`.

**Tech Stack:** Python 3, stdlib only; `pytest`. Reuses `sources.station_history.fetch_actual_cli` (IEM daily-summary) already used by calibration.

## Global Constraints

- **Low only, CLI basis only.** The high (`variable == "high"`) keeps its existing `observed_cont` branch verbatim. Robinhood (`settle_offset is None`) is byte-for-byte unchanged.
- **Backtest safety:** the not-locked anchor fires only when `live is True`. The locked branch is NOT live-gated (preserves existing locked behavior at `live` default `False`).
- **Colder-only + clamp:** a not-locked anchor applies only when `-MAX_CLI_GAP <= gap < 0`; the locked measured gap applies at `-MAX_CLI_GAP <= gap <= 0`. `MAX_CLI_GAP = 3.0` (°F).
- **Constant-shift invariant:** anchoring only sets `settle_shift`/`settle_gap_std`; it must not otherwise touch sigma, `locked_ratio`, the hard bound, or the display fields.
- Follow existing `tests/test_cli_basis.py` patterns (`_series`, `_obs`, `_member`).

---

### Task 1: The low CLI-min anchor in `predict_variable`

**Files:**
- Modify: `config.py` (add `MAX_CLI_GAP` constant)
- Modify: `model.py:25-28` (add `MAX_CLI_GAP` to the config import)
- Modify: `model.py:464-471` (replace the settle-shift block)
- Test: `tests/test_cli_basis.py` (append new tests + a `_top` helper)

**Interfaces:**
- Consumes: `model.predict_variable(series, obs_series, day, variable, now, calib, settle_offset=None, live=False)` — unchanged signature. Reads `obs_series.get("cli_daily", {})`, a `{date: (max_f, min_f)}` dict (populated by Task 2; absent → `{}`).
- Consumes: `model.bin_temp(label) -> int` (existing, model.py:558).
- Produces: no new public symbols; behavior change only.

- [ ] **Step 1: Add the config constant**

In `config.py`, add near the other lock/settlement constants (after `HIGH_LOCK_DROP`, around line 113):

```python
MAX_CLI_GAP = 3.0   # °F; largest CLI-vs-hourly low gap we trust as a live anchor (spike clamp)
```

- [ ] **Step 2: Import it in model.py**

Edit the `from config import (...)` block at `model.py:25-28` to include `MAX_CLI_GAP` (keep alphabetical-ish grouping):

```python
from config import (BIN_HIGH, BIN_LOW, CALM_WIND_MAX, CLEAR_CLOUD_MAX,
                    HIGH_LOCK_DROP, HIGH_LOCK_NOON_OFFSET_HOURS,
                    LEAD_SIGMA_INFLATION, LOW_LOCK_RISE, MAX_CLI_GAP,
                    PEAK_LOCK_DROP, TIMEZONE, bin_labels, lead_bucket)
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_cli_basis.py`. First a helper (place it just after the `_obs` helper near line 239):

```python
def _top(probs):
    """Integer degree of the highest-probability bin."""
    return model.bin_temp(max(probs, key=probs.get))


# A morning low that is SET but not yet locked: min 79 @03:00, ticked to 80
# @04:00 (rise 1°F < 2°F fallback, and pre-sunrise so the early-lock gate is off).
_NL_TEMPS = [82, 81, 80, 79, 80]
_NL_NOW_H = 4
_ZERO_OFF = {"low": 0.0, "low_std": 0.0, "high": 0.0, "high_std": 0.0}
```

Then the tests:

```python
def test_live_low_anchors_on_daily_summary_cli_min():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False)
    obs_cli["cli_daily"] = {day: (95.0, 78.0)}      # CLI min 78 = 1°F below hourly 79
    anchored = model.predict_variable(series, obs_cli, day, "low", now, None,
                                      _ZERO_OFF, live=True)
    assert not base["peak_locked"] and not anchored["peak_locked"]
    # Measured gap (78 - 79 = -1) replaces the zero average offset -> center -1°F.
    assert anchored["consensus"] == round(base["consensus"] - 1.0, 1)
    # Constant shift: spread unchanged, top bin drops one degree.
    assert anchored["sigma_used"] == base["sigma_used"]
    assert _top(anchored["probabilities"]) == _top(base["probabilities"]) - 1


def test_live_low_ignores_daily_summary_when_warmer_or_too_cold():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=True)
    warm = _obs(day, _NL_TEMPS, False); warm["cli_daily"] = {day: (95.0, 80.0)}
    cold = _obs(day, _NL_TEMPS, False); cold["cli_daily"] = {day: (95.0, 74.0)}
    # Warmer-than-hourly (gap >= 0) ignored; implausibly cold (gap < -3) clamped out.
    assert model.predict_variable(series, warm, day, "low", now, None,
                                  _ZERO_OFF, live=True)["consensus"] == base["consensus"]
    assert model.predict_variable(series, cold, day, "low", now, None,
                                  _ZERO_OFF, live=True)["consensus"] == base["consensus"]


def test_backtest_low_ignores_daily_summary_when_not_live():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, _ZERO_OFF, live=False)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    replay = model.predict_variable(series, obs_cli, day, "low", now, None,
                                    _ZERO_OFF, live=False)
    assert replay["consensus"] == base["consensus"]   # not-locked anchor is live-only


def test_locked_low_prefers_daily_summary_over_5min_feed():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    obs = _obs(day, _LOCKED_LOW, True)               # 5-min feed mirrors hourly low 79
    only_cont = model.predict_variable(series, obs, day, "low", now, None,
                                       _LOW_OFF, live=True)
    obs_cli = _obs(day, _LOCKED_LOW, True); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    with_cli = model.predict_variable(series, obs_cli, day, "low", now, None,
                                      _LOW_OFF, live=True)
    assert only_cont["peak_locked"] and with_cli["peak_locked"]
    # Anchors on the whole-°F 78, not the 5-min 79 -> ~1°F colder center.
    assert with_cli["consensus"] == round(only_cont["consensus"] - 1.0, 1)


def test_locked_low_daily_summary_gap_zero_no_average_offset():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, 16, tzinfo=_TZ)
    obs = _obs(day, _LOCKED_LOW, True); obs["cli_daily"] = {day: (95.0, 79.0)}
    with_cli = model.predict_variable(series, obs, day, "low", now, None,
                                      _LOW_OFF, live=True)
    no_offset = model.predict_variable(series, _obs(day, _LOCKED_LOW, True), day,
                                       "low", now, None, None, live=True)
    # Measured gap 0 -> anchored on the hourly low, NOT the -0.3 average offset.
    assert with_cli["consensus"] == no_offset["consensus"]


def test_high_ignores_daily_summary_min():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    off = {"high": 0.0, "high_std": 0.0, "low": 0.0, "low_std": 0.0}
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "high", now, None, off, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    assert model.predict_variable(series, obs_cli, day, "high", now, None,
                                  off, live=True)["consensus"] == base["consensus"]


def test_robinhood_low_ignores_daily_summary():
    day = date(2030, 7, 1)
    series = _series(day)
    now = datetime(day.year, day.month, day.day, _NL_NOW_H, tzinfo=_TZ)
    base = model.predict_variable(series, _obs(day, _NL_TEMPS, False), day,
                                  "low", now, None, None, live=True)
    obs_cli = _obs(day, _NL_TEMPS, False); obs_cli["cli_daily"] = {day: (95.0, 78.0)}
    assert model.predict_variable(series, obs_cli, day, "low", now, None,
                                  None, live=True) == base
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `pytest tests/test_cli_basis.py -k "daily_summary or backtest_low_ignores or high_ignores or robinhood_low" -v`
Expected: the new tests FAIL — currently `cli_daily` is ignored, so `anchored["consensus"]` equals `base["consensus"]` (the `round(base - 1.0, 1)` assertion fails), and the locked-prefers test sees no difference.

- [ ] **Step 5: Replace the settle-shift block**

In `model.py`, replace lines 464-471 (the `high_peak_in = ...` line through the `fullday = [...]` line) with:

```python
    # printing ~1°F too hot in the late-afternoon window (the "95.9 at 6pm when
    # it's 95" bug). The low keeps the strict `locked` gate — its downside is real.
    if settle_offset is not None and observed is not None:
        if variable == "high":
            # Unchanged: once the peak is locked or past the solar-noon gate and
            # the continuous peak is observed, anchor on the measured gap.
            if (locked or _past_high_peak_gate(day, now)) and observed_cont is not None:
                settle_shift = observed_cont - observed
                settle_gap_std = 0.0
        else:
            # Low, CLI basis: prefer the whole-°F daily-summary CLI min (the exact
            # Kalshi settlement variable) over the whole-°C 5-min feed. Use the
            # MEASURED gap instead of the flat average offset. A not-yet-locked low
            # anchors only on the authoritative daily-summary, only when it tightens
            # the low downward (gap < 0), and only `live` (backtest must not get the
            # settled value as lookahead). A locked low keeps its measured gap even
            # at gap == 0 (the settled value must beat the average offset).
            cli_daily = obs_series.get("cli_daily", {}).get(day)
            cli_low = cli_daily[1] if cli_daily else observed_cont
            if cli_low is not None:
                gap = cli_low - observed
                trust = (-MAX_CLI_GAP <= gap <= 0) if locked \
                    else (live and cli_daily is not None and -MAX_CLI_GAP <= gap < 0)
                if trust:
                    settle_shift = gap
                    settle_gap_std = 0.0
    if settle_shift:
        samples = [s + settle_shift for s in samples]
        fullday = [s + settle_shift for s in fullday]
```

(The two comment lines at the top are the tail of the existing comment block above line 464 — keep them; only the code from `high_peak_in`/`if settle_offset ...` onward changes.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_cli_basis.py -k "daily_summary or backtest_low_ignores or high_ignores or robinhood_low" -v`
Expected: PASS (all 7 new tests green).

- [ ] **Step 7: Run the full CLI-basis + accuracy suites (regression)**

Run: `pytest tests/test_cli_basis.py tests/test_accuracy.py tests/test_low_lock.py -v`
Expected: PASS. In particular `test_locked_low_anchors_on_continuous_and_skips_widening` and `test_unlocked_low_still_widens_with_continuous` stay green (the not-locked path is `live`-gated; those call with `live` default `False`).

- [ ] **Step 8: Commit**

```bash
git add config.py model.py tests/test_cli_basis.py
git commit -m "feat: anchor the Kalshi low to the daily-summary CLI min

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Fetch the daily-summary into the live snapshot

**Files:**
- Modify: `model.py:33-34` (import `fetch_actual_cli`)
- Modify: `model.py` (add `_fetch_cli_daily` helper; wire it into `gather_series` after the obs fetch, ~line 634)
- Test: `tests/test_cli_basis.py` (append two helper tests)

**Interfaces:**
- Produces: `model._fetch_cli_daily(day: date) -> dict` — returns `{date: (max_f, min_f)}` from `fetch_actual_cli(day, day)`, or `{}` on any exception. Consumed by Task 1's `obs_series.get("cli_daily", {})`.
- `gather_series(..., continuous_obs=True, now=...)` now sets `obs["cli_daily"]` to that dict; `continuous_obs=False` leaves it absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_basis.py`:

```python
def test_fetch_cli_daily_returns_summary(monkeypatch):
    day = date(2026, 7, 3)
    monkeypatch.setattr(model, "fetch_actual_cli", lambda s, e: {day: (83.0, 78.0)})
    assert model._fetch_cli_daily(day) == {day: (83.0, 78.0)}


def test_fetch_cli_daily_swallows_errors(monkeypatch):
    def boom(s, e):
        raise RuntimeError("network down")
    monkeypatch.setattr(model, "fetch_actual_cli", boom)
    assert model._fetch_cli_daily(date(2026, 7, 3)) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli_basis.py -k fetch_cli_daily -v`
Expected: FAIL — `AttributeError: module 'model' has no attribute '_fetch_cli_daily'` (and `fetch_actual_cli` not yet a `model` attribute).

- [ ] **Step 3: Import `fetch_actual_cli` at module level**

Edit `model.py:33-34` so the daily-summary fetch is importable/monkeypatchable as a `model` attribute:

```python
from sources import (open_meteo_ensemble, open_meteo_models, nws_forecast,
                     nws_observations, iem_mos)
from sources.station_history import fetch_actual_cli
```

- [ ] **Step 4: Add the helper**

Add near `gather_series` in `model.py` (e.g., just above `def gather_series`):

```python
def _fetch_cli_daily(day: date) -> dict:
    """{date: (max_f, min_f)} from the IEM daily summary for `day`, or {} on any
    failure. Best-effort: the CLI daily min is a live *anchor* for the Kalshi low
    (see predict_variable), never a settlement floor — a miss just falls back to
    the hourly/average-offset path."""
    try:
        return fetch_actual_cli(day, day)
    except Exception:
        return {}
```

- [ ] **Step 5: Run the helper tests to verify they pass**

Run: `pytest tests/test_cli_basis.py -k fetch_cli_daily -v`
Expected: PASS.

- [ ] **Step 6: Wire it into `gather_series`**

In `model.py`, in `gather_series`, replace the obs fetch + return (currently lines 634-635):

```python
    # Observations are the settlement anchor — not degradable; let it raise.
    obs = nws_observations.fetch(continuous=continuous_obs, now=now)
    return series, obs, dropped
```

with:

```python
    # Observations are the settlement anchor — not degradable; let it raise.
    obs = nws_observations.fetch(continuous=continuous_obs, now=now)
    # CLI basis only (Kalshi): the whole-°F daily-summary min anchors today's low
    # (predict_variable). Best-effort — a miss falls back to the hourly path.
    if continuous_obs:
        obs["cli_daily"] = _fetch_cli_daily((now or datetime.now(TZ)).date())
    return series, obs, dropped
```

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 8: Live spot-check (verification)**

Run:

```bash
python3 -c "
import calibration, model
calib = calibration.get(refresh=True)
snap = model.snapshot(calib, settle_offset=(calib or {}).get('settlement_offset'), continuous_obs=True)
lo = snap['today']['low']
print('consensus', lo['consensus'], 'top bins',
      sorted(lo['probabilities'].items(), key=lambda kv: -kv[1])[:3])
print('observed_so_far', lo['observed_so_far'], 'observed_continuous', lo['observed_continuous'])
"
```

Expected: on a morning where the daily-summary CLI min is colder than the hourly low, `consensus` sits at the daily-summary min and the top bin equals it (e.g. today: consensus ≈ 78.0, top bin 78) — matching the Kalshi market, versus the pre-change 78.6 / top bin 79.

- [ ] **Step 9: Commit**

```bash
git add model.py tests/test_cli_basis.py
git commit -m "feat: thread the live daily-summary CLI min into the Kalshi snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- CLI realized-low value (daily-summary preferred → `observed_cont` → None): Task 1 Step 5 (`cli_low = cli_daily[1] if cli_daily else observed_cont`). ✓
- Split-by-variable anchor, high unchanged: Task 1 Step 5 (`if variable == "high"` keeps the existing `observed_cont` branch). ✓
- Colder-guard (`gap < 0`) + locked `gap <= 0` + clamp `MAX_CLI_GAP`: Task 1 Step 5 `trust = ...`; tests `..._warmer_or_too_cold`, `..._gap_zero_no_average_offset`. ✓
- `live` gate on the not-locked path: Task 1 Step 5 (`live and ...`); test `..._not_live`. ✓
- Live daily-summary fetch, Kalshi-only, `try/except → {}`: Task 2 (`_fetch_cli_daily`, `if continuous_obs`). ✓
- Per-day lookup (tomorrow gets None): `obs_series.get("cli_daily", {}).get(day)` returns None for `tomorrow` since the dict only holds today. ✓
- Robinhood + high unchanged: tests `test_robinhood_low_ignores_daily_summary`, `test_high_ignores_daily_summary_min`. ✓
- `MAX_CLI_GAP = 3.0` config: Task 1 Steps 1-2. ✓
- Expected effect (78.6 → 78.0, bin 79 → 78): Task 2 Step 8 live spot-check. ✓

**Placeholder scan:** none — every code/step is concrete.

**Type consistency:** `cli_daily` is `{date: (max_f, min_f)}` everywhere (`_fetch_cli_daily` return, `obs["cli_daily"]`, `obs_series.get("cli_daily", {}).get(day)` → `(max, min)` tuple, `cli_daily[1]` = min). `_fetch_cli_daily`/`fetch_actual_cli` names match across tasks. `_top` uses existing `model.bin_temp`. ✓
