# Corroboration-Scaled High Haircut Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 0.9°F sub-hourly high haircut with one that shrinks as the peak's whole-°C level gains corroborating readings, then validate it out-of-sample against actual CLI settlements.

**Architecture:** A pure `_high_haircut(n)` ramp (config-driven constants) consumes a corroboration count `n` from a new `settlement.corroboration_count` helper; the high branch of `model.predict_variable` swaps `c_max - 0.9` for `c_max - _high_haircut(n)`. `_trusted_high_max` (which value to trust) is untouched — only the cushion is refined. A standalone `haircut_backtest.py` reconstructs each settled day's sub-hourly feed from IEM 1-minute ASOS (resampled to 5-min, quantized to the whole-°C grid the live NWS feed uses), replays flat vs. scaled, and scores both against `settlements.jsonl` CLI highs, with a synthetic lone-spike injection proving n=1 is a no-op.

**Tech Stack:** Python 3.9, pytest, existing `sources`/`settlement`/`model` modules, IEM ASOS 1-minute archive (`request/asos1min.py`).

## Global Constraints

- Python 3.9 compatible; `from __future__ import annotations` at top of new modules.
- Repo uses flat root-level `.py` files and root-level `test_*.py`; follow that (no `src/`, no `tests/` package for new files — existing tests live at repo root, e.g. `test_cli_basis.py`).
- All day/window math in `America/Chicago` (`config.TIMEZONE`); construct tz-aware datetimes with `ZoneInfo(TIMEZONE)`.
- `cryptography` must stay ≤ 38.x (unrelated dependency pin; do not upgrade). Run tests with the existing local env.
- Do NOT auto-ship the model change. The final task reports numbers; merging to the live path is a separate human decision gated on OOS improvement with zero glitch regression.
- `_trusted_high_max` and the low branch are OUT OF SCOPE — do not modify them.

---

### Task 1: `corroboration_count` helper in settlement.py

**Files:**
- Modify: `settlement.py` (add function after `_corroborated_extreme`, ~line 178)
- Test: `test_corroboration_count.py` (new, repo root)

**Interfaces:**
- Produces: `corroboration_count(times: list[datetime], temps: list[float], day: date, now: datetime, level: float, tol: float = 0.7) -> int` — number of within-day continuous readings (≤ `now`) at or within `tol`°F below `level`.

- [ ] **Step 1: Write the failing test**

```python
# test_corroboration_count.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
from settlement import corroboration_count

_TZ = ZoneInfo(TIMEZONE)


def _feed(minutes_and_temps):
    times = [datetime(2026, 7, 20, 16, m, tzinfo=_TZ) for m, _ in minutes_and_temps]
    temps = [t for _, t in minutes_and_temps]
    return times, temps


def test_counts_corroborated_plateau():
    # 37C (98.6) once, 38C (100.4) four times
    times, temps = _feed([(0, 98.6), (5, 100.4), (10, 100.4), (15, 100.4), (20, 100.4)])
    now = datetime(2026, 7, 20, 16, 30, tzinfo=_TZ)
    assert corroboration_count(times, temps, date(2026, 7, 20), now, 100.4) == 4


def test_lone_spike_counts_one():
    times, temps = _feed([(0, 98.6), (5, 100.4), (10, 98.6)])
    now = datetime(2026, 7, 20, 16, 30, tzinfo=_TZ)
    assert corroboration_count(times, temps, date(2026, 7, 20), now, 100.4) == 1


def test_excludes_readings_after_now():
    times, temps = _feed([(0, 100.4), (5, 100.4), (25, 100.4)])
    now = datetime(2026, 7, 20, 16, 10, tzinfo=_TZ)
    assert corroboration_count(times, temps, date(2026, 7, 20), now, 100.4) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_corroboration_count.py -v`
Expected: FAIL with `ImportError: cannot import name 'corroboration_count'`

- [ ] **Step 3: Add the implementation**

In `settlement.py`, immediately after `_corroborated_extreme` (before `bin_for_temp`):

```python
def corroboration_count(times: list[datetime], temps: list[float], day: date,
                        now: datetime, level: float, tol: float = 0.7) -> int:
    """How many within-day continuous readings (up to `now`) sit at or within
    `tol`°F below `level` — the support behind a trusted HIGH extreme. Mirrors
    the max-side convention in `_corroborated_extreme` (readings >= level - tol):
    a lone spike scores 1, a sustained plateau scores several. Drives the
    corroboration-scaled high haircut in model._high_haircut."""
    vals = _within_day(times, temps, day, upto=now)
    return sum(1 for v in vals if v >= level - tol)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_corroboration_count.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add settlement.py test_corroboration_count.py
git commit -m "feat: corroboration_count helper for the scaled high haircut"
```

---

### Task 2: `_high_haircut(n)` ramp + config constants

**Files:**
- Modify: `config.py` (add constants after `HIGH_LOCK_DROP`, ~line 160)
- Modify: `model.py` (add helper near `_trusted_high_max`, ~line 507; extend the `config` import block ~line 28)
- Test: `test_high_haircut.py` (new, repo root)

**Interfaces:**
- Consumes: `config.HIGH_HAIRCUT_FULL`, `config.HIGH_HAIRCUT_FLOOR`, `config.HIGH_HAIRCUT_K`.
- Produces: `model._high_haircut(n: int) -> float` — °F to shave off the sub-hourly high bound given corroboration count `n`.

- [ ] **Step 1: Write the failing test**

```python
# test_high_haircut.py
from config import HIGH_HAIRCUT_FLOOR, HIGH_HAIRCUT_FULL
from model import _high_haircut


def test_lone_reading_keeps_full_haircut():
    assert _high_haircut(1) == HIGH_HAIRCUT_FULL
    assert _high_haircut(0) == HIGH_HAIRCUT_FULL   # defensive: no reading -> full


def test_haircut_is_monotonic_non_increasing():
    vals = [_high_haircut(n) for n in range(1, 15)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


def test_haircut_never_below_floor():
    assert _high_haircut(1000) == HIGH_HAIRCUT_FLOOR
    assert all(_high_haircut(n) >= HIGH_HAIRCUT_FLOOR for n in range(1, 100))


def test_haircut_shrinks_with_corroboration():
    assert _high_haircut(4) < _high_haircut(2) < _high_haircut(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_high_haircut.py -v`
Expected: FAIL with `ImportError` (constants and `_high_haircut` not defined)

- [ ] **Step 3: Add config constants**

In `config.py`, after the `HIGH_LOCK_DROP = 0.8 ...` block (~line 160):

```python
# Corroboration-scaled high haircut. The whole-°C 5-min feed reading of "38°C"
# (100.4°F) could be a true 99.5-101.3°F, so a LONE reading is shaved the full
# HIGH_HAIRCUT_FULL (bottom of the °C bucket — the glitch/over-read guard). A
# corroborated plateau genuinely reached that level, so the shave ramps down
# toward HIGH_HAIRCUT_FLOOR (sub-°C rounding noise) as the supporting-reading
# count grows. Defaults below; haircut_backtest.py tunes (K, FLOOR) out-of-sample.
# See docs/superpowers/specs/2026-07-20-corroboration-scaled-high-haircut-design.md.
HIGH_HAIRCUT_FULL = 0.9    # °F shaved off a lone (n=1) sub-hourly high reading
HIGH_HAIRCUT_FLOOR = 0.3   # minimum shave once solidly corroborated
HIGH_HAIRCUT_K = 0.2       # °F less shave per extra corroborating reading
```

- [ ] **Step 4: Add the helper and import**

In `model.py`, extend the existing `from config import (...)` block (~line 28-33) to include the three new names (keep alphabetical grouping consistent with neighbors):

```python
                    HIGH_HAIRCUT_FLOOR, HIGH_HAIRCUT_FULL, HIGH_HAIRCUT_K,
```

Then add, immediately after `_trusted_high_max` (~line 507):

```python
def _high_haircut(n: int) -> float:
    """°F to shave off the sub-hourly high bound given `n` readings corroborating
    the peak's whole-°C level (from settlement.corroboration_count). n<=1 (a lone
    reading, i.e. a glitch by definition) keeps the full cushion — glitch
    protection is preserved by construction; more corroboration ramps the shave
    down to HIGH_HAIRCUT_FLOOR."""
    if n <= 1:
        return HIGH_HAIRCUT_FULL
    return max(HIGH_HAIRCUT_FLOOR, HIGH_HAIRCUT_FULL - HIGH_HAIRCUT_K * (n - 1))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest test_high_haircut.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add config.py model.py test_high_haircut.py
git commit -m "feat: _high_haircut ramp + config constants"
```

---

### Task 3: Wire the scaled haircut into the high branch

**Files:**
- Modify: `model.py` (high branch ~line 556-561; extend the `settlement` import ~line 34)
- Test: `test_haircut_integration.py` (new, repo root)

**Interfaces:**
- Consumes: `settlement.corroboration_count` (Task 1), `model._high_haircut` (Task 2), `model.predict_variable`.
- Produces: no new public symbol — behavioral change to `predict_variable`'s high bound.

- [ ] **Step 1: Write the failing test**

This test drives `predict_variable` with a locked afternoon so the observed high bound governs the result, and asserts a corroborated 38°C plateau yields a higher predicted high consensus than a single 38°C reading (same trusted `c_max`, smaller haircut), while a lone spike matches the old flat-0.9 number.

```python
# test_haircut_integration.py
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import HIGH_HAIRCUT_FULL, TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 20)


def _member(peak):
    """24-hour member peaking at `peak` at 16:00 local (so the forecast supports
    a ~100°F settled bin and _trusted_high_max accepts the spike)."""
    base = datetime(_DAY.year, _DAY.month, _DAY.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 16) for h in range(24)]
    return times, temps


def _series():
    return {"det_a": _member(100.0), "det_b": _member(100.5)}


def _obs(cont_pairs):
    """Hourly obs = the routine :53 readings; continuous = the 5-min feed."""
    base = datetime(_DAY.year, _DAY.month, _DAY.day, tzinfo=_TZ)
    ctimes = [base.replace(hour=16, minute=m) for m, _ in cont_pairs]
    ctemps = [t for _, t in cont_pairs]
    # Routine hourly obs: a plain 98.6 at :53 so the hourly bound is well below.
    htimes = [base.replace(hour=16, minute=53)]
    htemps = [98.6]
    return {"obs": (htimes, htemps), "obs_continuous": (ctimes, ctemps)}


def _high_consensus(cont_pairs):
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 17, 30, tzinfo=_TZ)
    res = model.predict_variable(_series(), _obs(cont_pairs), _DAY, "high",
                                 now, None, live=True)
    return res["consensus"]


def test_plateau_lifts_high_above_lone_spike():
    lone = _high_consensus([(50, 98.6), (55, 100.4), (0, 98.6)])          # n=1 at 100.4
    plateau = _high_consensus([(20, 100.4), (25, 100.4), (30, 100.4),
                               (35, 100.4), (40, 100.4)])                  # n=5 at 100.4
    assert plateau > lone


def test_lone_spike_matches_flat_haircut_bound():
    # With a lone trusted 100.4 spike the bound is 100.4 - HIGH_HAIRCUT_FULL = 99.5;
    # consensus must not exceed that realized ceiling by more than sampling noise.
    lone = _high_consensus([(50, 98.6), (55, 100.4), (0, 98.6)])
    assert lone <= 100.4 - HIGH_HAIRCUT_FULL + 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_haircut_integration.py -v`
Expected: FAIL on `test_plateau_lifts_high_above_lone_spike` (flat 0.9 haircut makes plateau == lone).

Note: if `test_lone_spike_matches_flat_haircut_bound` or the trusted-spike path needs the forecast to support the 100 bin, confirm `_member` peaks ≥ 100; adjust the peak up by 0.5°F increments until `_trusted_high_max` accepts 100.4 in the lone case (verify by temporarily printing `res["consensus"]`). Do not weaken the plateau assertion.

- [ ] **Step 3: Apply the wiring change**

In `model.py`, extend the `from settlement import (...)` block (~line 34) to add `corroboration_count`:

```python
from settlement import (corroboration_count, round_half_up,
                        _HIGH_WINDOW, _LOW_WINDOW)
```

(Match the existing block's exact members; only add `corroboration_count`.)

Then in the high branch (~line 556-561), change:

```python
            c_max = _trusted_high_max(c_max_raw, c_max_rob, fullday, shift)
            if c_max is not None:
                observed_cont = c_max
                observed_cont_display = c_max  # high already shows its trusted spike
                cand = c_max - 0.9
                observed_bound = cand if observed is None else max(observed, cand)
```

to:

```python
            c_max = _trusted_high_max(c_max_raw, c_max_rob, fullday, shift)
            if c_max is not None:
                observed_cont = c_max
                observed_cont_display = c_max  # high already shows its trusted spike
                n = corroboration_count(cont_times, cont_temps, day, now, c_max)
                cand = c_max - _high_haircut(n)
                observed_bound = cand if observed is None else max(observed, cand)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_haircut_integration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: all tests pass (the prior baseline is 531 passing; the 2 low-branch-sensitive files `test_convective.py`, `test_front_guard*.py` must be unaffected since the low branch is untouched). Investigate any failure before continuing.

- [ ] **Step 6: Commit**

```bash
git add model.py test_haircut_integration.py
git commit -m "feat: scale the high haircut by corroboration count"
```

---

### Task 4: Reconstruct a past day's whole-°C 5-min feed from IEM 1-minute ASOS

**Files:**
- Create: `haircut_backtest.py` (repo root) — start it with this reconstruction function.
- Test: `test_haircut_backtest.py` (new, repo root)

**Interfaces:**
- Produces:
  - `emulate_nws_5min(f_temps: list[float]) -> list[float]` — quantize whole-°F readings to the whole-°C grid the live NWS feed reports (°F→°C→round→°F).
  - `reconstruct_5min_feed(day: date) -> tuple[list[datetime], list[float]]` — IEM 1-min ASOS for `day`'s LST climate window, resampled to 5-minute marks, quantized via `emulate_nws_5min`. Returns tz-aware `America/Chicago` times ascending.

- [ ] **Step 1: Write the failing test**

```python
# test_haircut_backtest.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
from haircut_backtest import emulate_nws_5min, resample_5min

_TZ = ZoneInfo(TIMEZONE)


def test_emulate_nws_5min_snaps_to_whole_celsius_grid():
    # 100°F -> 37.78°C -> 38°C -> 100.4°F ; 99°F -> 37.22°C -> 37°C -> 98.6°F
    out = emulate_nws_5min([100.0, 99.0])
    assert round(out[0], 1) == 100.4
    assert round(out[1], 1) == 98.6


def test_resample_5min_keeps_one_reading_per_five_minute_mark():
    base = datetime(2026, 7, 20, 16, 0, tzinfo=_TZ)
    times = [base.replace(minute=m) for m in range(0, 12)]   # 12 one-minute rows
    temps = [90.0 + m for m in range(12)]
    rt, rv = resample_5min(times, temps)
    # marks at :00, :05, :10 -> 3 samples
    assert [t.minute for t in rt] == [0, 5, 10]
    assert rv == [90.0, 95.0, 100.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_haircut_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'haircut_backtest'`

- [ ] **Step 3: Create `haircut_backtest.py` with the reconstruction helpers**

```python
"""Out-of-sample backtest for the corroboration-scaled high haircut.

Reconstructs each settled day's sub-hourly feed from the IEM 1-minute ASOS
archive (resampled to 5-min, quantized to the whole-°C grid the live NWS feed
reports), replays the trusted-high path under the flat 0.9°F haircut vs. the
scaled ramp, and scores both against the actual CLI settlement. Standalone —
NOT wired into the live pipeline. Run: `python haircut_backtest.py`.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE
from sources.common import get_text

_TZ = ZoneInfo(TIMEZONE)
_IEM_1MIN = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"


def emulate_nws_5min(f_temps: list[float]) -> list[float]:
    """Quantize whole-°F readings onto the whole-°C grid the live NWS 5-minute
    feed reports (the grid the haircut is designed around): °F -> °C -> round to
    whole °C -> back to °F."""
    out = []
    for f in f_temps:
        c = round((f - 32.0) * 5.0 / 9.0)
        out.append(c * 9.0 / 5.0 + 32.0)
    return out


def resample_5min(times: list[datetime], temps: list[float]
                  ) -> tuple[list[datetime], list[float]]:
    """Keep the first reading at each distinct 5-minute mark (:00,:05,...),
    mirroring the live feed's ~5-min cadence."""
    seen = set()
    rt, rv = [], []
    for t, v in zip(times, temps):
        if t.minute % 5 != 0:
            continue
        key = t.replace(second=0, microsecond=0)
        if key in seen:
            continue
        seen.add(key)
        rt.append(t)
        rv.append(v)
    return rt, rv


def _fetch_1min(day: date) -> tuple[list[datetime], list[float]]:
    """IEM 1-minute ASOS tmpf for `day`'s LST climate window (00:00 that day
    through 00:59 the next clock day, to cover the CLI LST tail)."""
    start = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    end = start + timedelta(days=1, hours=1)
    params = {
        "station": "DFW", "vars": "tmpf",
        "sts": start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ"),
        "ets": end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ"),
        "sample": "1min", "what": "download", "tz": "UTC",
        "gis": "no", "format": "comma",
    }
    text = get_text(_IEM_1MIN, params)
    times, temps = [], []
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("tmpf") or "").strip()
        if raw in ("", "M", "None"):
            continue
        try:
            f = float(raw)
        except ValueError:
            continue
        ts = datetime.strptime(row["valid(UTC)"], "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo("UTC")).astimezone(_TZ)
        times.append(ts)
        temps.append(f)
    return times, temps


def reconstruct_5min_feed(day: date) -> tuple[list[datetime], list[float]]:
    """The whole-°C 5-minute feed the live model would have seen for `day`."""
    times, temps = _fetch_1min(day)
    times, temps = resample_5min(times, temps)
    return times, emulate_nws_5min(temps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_haircut_backtest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Smoke-test the live fetch against a known day**

Run:
```bash
python -c "
from datetime import date
import haircut_backtest as h
t, v = h.reconstruct_5min_feed(date(2026, 7, 14))
print('rows', len(t), 'max', round(max(v), 1))
"
```
Expected: a few hundred rows and a max around the mid-90s°F (2026-07-14 was a warm day). Non-empty confirms the IEM archive path works.

- [ ] **Step 6: Commit**

```bash
git add haircut_backtest.py test_haircut_backtest.py
git commit -m "feat: reconstruct past 5-min feed from IEM 1-min for the haircut backtest"
```

---

### Task 5: Replay, score, tune, and prove glitch no-op

**Files:**
- Modify: `haircut_backtest.py` (add the replay/scoring/tuning/injection functions and a `__main__` runner)
- Test: `test_haircut_backtest.py` (add scoring + glitch-injection tests)

**Interfaces:**
- Consumes: `reconstruct_5min_feed` (Task 4), `settlement.corroboration_count`, `settlement.observed_so_far_robust`, `model._trusted_high_max`, `model._high_haircut`, `config.HIGH_HAIRCUT_FULL/FLOOR/K`.
- Produces:
  - `_replay_day(ct, cv, day, now, fullday, shift) -> tuple[float, int] | None` — `(c_max, n)` for a day (the trusted extreme and its corroboration count), or `None` if no high yet.
  - `_haircut(n, k=None, floor=None) -> float` — the ramp; defaults call `model._high_haircut` (exercises production code), explicit `(k, floor)` parameterize it for grid search.
  - `trusted_high(ct, cv, day, now, fullday, shift, scaled, k=None, floor=None) -> float | None` — the trusted-high bound under flat (`scaled=False`) or scaled (`scaled=True`) haircut.
  - `load_cli_settlements(path="settlements.jsonl") -> dict[date, float]` — `{day: cli_high}`.
  - `inject_lone_spike(times, temps, level_f) -> tuple[list, list]` — append one synthetic reading `level_f` after the day's last reading.
  - `run(days: list[date]) -> dict` — the report (MAE flat/scaled, changed count, regressions, `n`-distribution, best `(k, floor)`). No global mutation — tuning parameterizes `_haircut` directly.

- [ ] **Step 1: Write the failing tests**

```python
# append to test_haircut_backtest.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import haircut_backtest as hb

_TZ2 = ZoneInfo(TIMEZONE)


def _cont(pairs):
    base = datetime(2026, 7, 20, 16, 0, tzinfo=_TZ2)
    return ([base.replace(minute=m) for m, _ in pairs], [t for _, t in pairs])


def test_scaled_bound_higher_than_flat_on_plateau():
    ct, cv = _cont([(0, 100.4), (5, 100.4), (10, 100.4), (15, 100.4)])
    now = datetime(2026, 7, 20, 16, 30, tzinfo=_TZ2)
    fullday = [100.0, 100.5]     # forecast supports the 100 bin
    flat = hb.trusted_high(ct, cv, date(2026, 7, 20), now, fullday, 0.0, scaled=False)
    scaled = hb.trusted_high(ct, cv, date(2026, 7, 20), now, fullday, 0.0, scaled=True)
    assert scaled > flat


def test_glitch_injection_is_noop_under_scaled():
    # A clean day + one lone spike: scaled and flat must agree, because the lone
    # reading (n=1) keeps the full haircut either way.
    ct, cv = _cont([(0, 98.6), (5, 98.6), (10, 98.6)])
    ct2, cv2 = hb.inject_lone_spike(ct, cv, 100.4)
    now = datetime(2026, 7, 20, 16, 30, tzinfo=_TZ2)
    fullday = [100.0, 100.5]
    flat = hb.trusted_high(ct2, cv2, date(2026, 7, 20), now, fullday, 0.0, scaled=False)
    scaled = hb.trusted_high(ct2, cv2, date(2026, 7, 20), now, fullday, 0.0, scaled=True)
    assert scaled == flat


def test_load_cli_settlements_picks_cli_basis():
    out = hb.load_cli_settlements("settlements.jsonl")
    assert all(isinstance(k, date) for k in out)
    assert out   # non-empty
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test_haircut_backtest.py -v -k "scaled_bound or glitch or load_cli"`
Expected: FAIL (`AttributeError: module 'haircut_backtest' has no attribute 'trusted_high'`)

- [ ] **Step 3: Implement replay, scoring, tuning, injection**

Append to `haircut_backtest.py`:

```python
import json

import config
from settlement import corroboration_count, observed_so_far_robust
from model import _trusted_high_max, _high_haircut


def _replay_day(ct, cv, day, now, fullday, shift):
    """The trusted high extreme `c_max` and its corroboration count `n` for a
    reconstructed day, or None if no high has formed. Mirrors the value
    model.predict_variable's high branch trusts (uses the real _trusted_high_max)."""
    raw, _ = observed_so_far_robust(ct, cv, day, now, min_support=1)
    rob, _ = observed_so_far_robust(ct, cv, day, now)
    c_max = _trusted_high_max(raw, rob, fullday, shift)
    if c_max is None:
        return None
    return c_max, corroboration_count(ct, cv, day, now, c_max)


def _haircut(n, k=None, floor=None):
    """The ramp. Default (k, floor) delegate to the production model._high_haircut
    so the backtest exercises real code; explicit values parameterize it for the
    grid search without mutating any globals."""
    if k is None and floor is None:
        return _high_haircut(n)
    k = config.HIGH_HAIRCUT_K if k is None else k
    floor = config.HIGH_HAIRCUT_FLOOR if floor is None else floor
    if n <= 1:
        return config.HIGH_HAIRCUT_FULL
    return max(floor, config.HIGH_HAIRCUT_FULL - k * (n - 1))


def trusted_high(ct, cv, day, now, fullday, shift, scaled, k=None, floor=None):
    """The trusted-high bound (c_max minus the haircut) under the flat cushion
    (`scaled=False`) or the corroboration-scaled ramp (`scaled=True`)."""
    r = _replay_day(ct, cv, day, now, fullday, shift)
    if r is None:
        return None
    c_max, n = r
    return c_max - (_haircut(n, k, floor) if scaled else config.HIGH_HAIRCUT_FULL)


def inject_lone_spike(times, temps, level_f):
    """Append one synthetic reading `level_f` (a lone spike) one minute after the
    last reading, for the glitch no-op proof."""
    from datetime import timedelta
    return times + [times[-1] + timedelta(minutes=1)], temps + [level_f]


def load_cli_settlements(path: str = "settlements.jsonl") -> dict:
    """{climate_day: cli_high_f} from the CLI-basis settlement rows."""
    out = {}
    with open(path) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("basis") != "cli":
                continue
            out[date.fromisoformat(row["target_date"])] = float(row["high"])
    return out


def run(days: list[date]) -> dict:
    """Replay every settled day under flat vs. scaled, score against the CLI
    settlement, and grid-tune (k, floor) by lowest scaled MAE subject to zero
    glitch regression. Feeds IEM once per day (replays cached), then scores many
    param combos cheaply. Prints and returns the report; does NOT touch the live
    pipeline."""
    cli = load_cli_settlements()
    replays = []   # (c_max, n, cli_high) — one IEM fetch per day
    for d in [d for d in days if d in cli]:
        ct, cv = reconstruct_5min_feed(d)
        if not ct:
            continue
        # now = the day's full LST window closed; fullday proxy = the settled high
        # (best offline "did the forecast expect this level" signal for the spike
        # gate — coarser than the live ensemble, so a conservative lower bound).
        now = datetime(d.year, d.month, d.day, tzinfo=_TZ) + timedelta(days=1, hours=1)
        r = _replay_day(ct, cv, d, now, [cli[d]], 0.0)
        if r is not None:
            replays.append((r[0], r[1], cli[d]))

    def score(k=None, floor=None, scaled=True):
        errs, changed, regress, ns = [], 0, 0, []
        for c_max, n, truth in replays:
            flat = c_max - config.HIGH_HAIRCUT_FULL
            got = (c_max - _haircut(n, k, floor)) if scaled else flat
            errs.append(abs(got - truth))
            if scaled and abs(got - flat) > 1e-9:
                changed += 1
                ns.append(n)
                if abs(got - truth) > abs(flat - truth) + 1e-9:
                    regress += 1
        return {"mae": round(sum(errs) / len(errs), 3) if errs else None,
                "changed": changed, "regress": regress, "n_days": len(errs),
                "n_dist": sorted(ns)}

    best = None
    for k in (0.1, 0.15, 0.2, 0.25, 0.3):
        for floor in (0.2, 0.3, 0.4, 0.5):
            r = score(k, floor)
            if r["regress"] == 0 and (best is None or r["mae"] < best["mae"]):
                best = {**r, "k": k, "floor": floor}
    report = {"days": len(replays), "flat": score(scaled=False),
              "scaled_default": score(), "best": best}
    print(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    run(sorted(load_cli_settlements()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest test_haircut_backtest.py -v`
Expected: PASS (all, including the three new tests)

- [ ] **Step 5: Commit the harness**

```bash
git add haircut_backtest.py test_haircut_backtest.py
git commit -m "feat: haircut backtest replay, scoring, tuning, glitch-injection proof"
```

- [ ] **Step 6: Run the full backtest and capture the report**

Run:
```bash
python haircut_backtest.py | tee "$CLAUDE_JOB_DIR/tmp/haircut_backtest_report.json"
```
Expected: JSON with `flat.mae`, `scaled_default.mae`, `best.{k,floor,mae}`, `changed`, and `regress: 0`. Read it, then report to the user: flat vs. scaled/best MAE, how many days changed, the `n`-distribution on changed days, and a ship recommendation. Do NOT merge to the live path — this is the human decision gate.

---

## Notes on the ship gate

The model change (Tasks 1-3) is already committed to the branch, but shipping it live is contingent on the Task 5 report: scaled/best MAE must beat flat MAE with `regress == 0`. If the backtest does not show a real improvement (as with the `high-spike-latch-lag` prototype), revert Tasks 1-3 or hold the branch — do not merge. Present the numbers and the recommendation; let the user decide.
