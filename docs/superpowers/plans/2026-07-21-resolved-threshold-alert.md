# Resolved-Threshold Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a push-only ntfy alert the first time each day that a variable's displayed Resolved % reaches 70% — one for the high, one for the low, fired independently.

**Architecture:** Move the pure `displayed_resolved` helper from `market_view.py` (Streamlit-heavy) into `model.py` so the headless cron can use it. Add `scheduled_log._maybe_alert_resolved(snap, now)` that checks each variable's displayed Resolved % against a 70 threshold, sends one ntfy per variable per day, and records the day in `resolved_alert_state.json` on the `data` branch.

**Tech Stack:** Python 3.11, Streamlit, pytest, GitHub Actions, ntfy (via existing `notify.py`).

## Global Constraints

- Threshold constant `RESOLVED_ALERT_PCT = 70`; only 70 ships.
- Use the **displayed** Resolved % (`model.displayed_resolved`), not raw `resolved`.
- Everything **best-effort**: any failure skips the alert (logged) and never blocks the surrounding forecast/consensus logging.
- Alerts are **per variable, once per day** (high and low independent), keyed to today's **climate day** (`settlement.climate_day_of(now)`).
- Reuse the existing `NTFY_TOPIC` secret and `notify.send_ntfy`.
- Message: title `Dallas {High,Low} locking in`; body `{pct}% resolved · ≈{consensus}°F`.
- An empty/corrupt state file (0-byte from the `git show … || true` restore) must be tolerated as `{}` — same rule as `cli_alert_state.json`.

---

### Task 1: Move `displayed_resolved` into `model.py`

**Files:**
- Modify: `model.py` (add `CONVECTIVE_RESOLVED_CAP` + `displayed_resolved`)
- Modify: `market_view.py` (delete local defs; re-import from `model`)
- Test: `tests/test_model_displayed_resolved.py`

**Interfaces:**
- Produces: `model.CONVECTIVE_RESOLVED_CAP = 90`; `model.displayed_resolved(d: dict) -> int`.
- `market_view.displayed_resolved` and `market_view.CONVECTIVE_RESOLVED_CAP` remain valid names (re-imported), so existing call sites and tests are unaffected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_displayed_resolved.py`:

```python
"""displayed_resolved lives in model (pure, no Streamlit)."""
import model


def _d(resolved, conv=False, front=False):
    return {"resolved": resolved, "convective_widened": conv, "front_widened": front}


def test_full_window_is_100():
    assert model.displayed_resolved(_d(1.0)) == 100


def test_capped_on_convective_or_front():
    assert model.displayed_resolved(_d(1.0, conv=True)) == model.CONVECTIVE_RESOLVED_CAP
    assert model.displayed_resolved(_d(1.0, front=True)) == model.CONVECTIVE_RESOLVED_CAP
    assert model.CONVECTIVE_RESOLVED_CAP == 90


def test_partial_uncapped():
    assert model.displayed_resolved(_d(0.72)) == 72
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_model_displayed_resolved.py -q`
Expected: FAIL — `AttributeError: module 'model' has no attribute 'displayed_resolved'`.

- [ ] **Step 3: Add the definitions to `model.py`**

Append at the end of `model.py` (after `_storm_status`):

```python


# On a convective-downside day the low can be reset by evening storms, so the
# Resolved metric is capped below 100% no matter how much of the diurnal window
# has closed — the low isn't truly settled until midnight passes storm-free. The
# same cap applies when the front guard (`front_widened`) projects a colder evening
# reading — a forecast front is no more settled than a storm risk.
CONVECTIVE_RESOLVED_CAP = 90


def displayed_resolved(d):
    """Resolved % for the metric card, clamped on a convective- or front-risk day.

    `resolved` measures how much of the *diurnal* uncertainty is settled and hits
    100% once the extreme's window closes. But on a storm day the low's daily min
    can still be reset lower by evening convection (convective.py), or when a forecast
    front is active, the low may be undercut by a colder post-noon reading — either way,
    a locked dawn trough is not a resolved low. Cap the display so the metric stops
    contradicting the risk caption. Display-only — the raw `resolved` and the
    probabilities are untouched."""
    pct = int(d.get("resolved", 1 - d.get("locked_ratio", 0.0)) * 100)
    if d.get("convective_widened") or d.get("front_widened"):
        pct = min(pct, CONVECTIVE_RESOLVED_CAP)
    return pct
```

- [ ] **Step 4: Delete the local defs in `market_view.py` and re-import**

Remove the block at `market_view.py` ~lines 994–1015 (the `# On a convective-downside day …` comment, `CONVECTIVE_RESOLVED_CAP = 90`, and the whole `def displayed_resolved(d):` function). Delete from the comment's first line through the function's `return pct`.

Then add the re-import right after the existing `import model` (line 23):

```python
import model
from model import CONVECTIVE_RESOLVED_CAP, displayed_resolved  # metric-card helper, shared with the cron
```

- [ ] **Step 5: Run the new test + the existing consumers to confirm the re-import works**

Run: `python3 -m pytest tests/test_model_displayed_resolved.py tests/test_lock_status_front.py tests/test_lock_status_convective.py tests/test_lock_status_resolved.py -q`
Expected: PASS. (`test_lock_status_front/convective` import `displayed_resolved` from `market_view` — they exercise the re-import.)

- [ ] **Step 6: Commit**

```bash
git add model.py market_view.py tests/test_model_displayed_resolved.py
git commit -m "refactor: move displayed_resolved into model (pure, cron-safe)"
```

---

### Task 2: `_maybe_alert_resolved` in `scheduled_log.py`

**Files:**
- Modify: `scheduled_log.py` (constants; a shared state-load helper; `_maybe_alert_resolved`; call site in `_log_snapshots`; refactor `_maybe_alert_cli` to use the helper)
- Test: `tests/test_resolved_alert.py`

**Interfaces:**
- Consumes: `model.displayed_resolved`, `notify.send_ntfy`, `settlement.climate_day_of`, and the `cli_snap` dict from `_log_snapshots` (`snap["today"]["high"|"low"]` each with `resolved` + `consensus`).
- Produces: `scheduled_log.RESOLVED_ALERT_PCT = 70`; `scheduled_log.RESOLVED_STATE_PATH`; `scheduled_log._maybe_alert_resolved(snap: dict, now: datetime) -> None`; `scheduled_log._load_alert_state(path: str) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolved_alert.py`:

```python
"""Per-variable 70%-resolved ntfy alert in scheduled_log."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import scheduled_log

_TZ = ZoneInfo(TIMEZONE)


def _snap(high_res, low_res, high_c=97.0, low_c=80.0):
    def _v(res, c):
        return {"resolved": res, "consensus": c,
                "convective_widened": False, "front_widened": False}
    return {"today": {"high": _v(high_res, high_c), "low": _v(low_res, low_c)}}


def _patch(monkeypatch, tmp_path, sends):
    monkeypatch.setattr(scheduled_log, "RESOLVED_STATE_PATH",
                        str(tmp_path / "resolved.json"))
    import notify
    monkeypatch.setattr(notify, "send_ntfy",
                        lambda title, message: sends.append((title, message)) or True)


_NOW = datetime(2026, 7, 21, 15, 0, tzinfo=_TZ)


def test_fires_at_70_not_69(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.695, 0.60), _NOW)  # 69% / 60%
    assert sends == []
    scheduled_log._maybe_alert_resolved(_snap(0.70, 0.60), _NOW)   # 70% / 60%
    assert len(sends) == 1
    assert sends[0][0] == "Dallas High locking in"
    assert "70% resolved" in sends[0][1] and "97" in sends[0][1]


def test_high_and_low_independent(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.85, 0.50), _NOW)  # only high ≥70
    assert [t for t, _ in sends] == ["Dallas High locking in"]
    scheduled_log._maybe_alert_resolved(_snap(0.90, 0.75), _NOW)  # low now ≥70
    assert [t for t, _ in sends] == ["Dallas High locking in", "Dallas Low locking in"]


def test_once_per_day_then_rearms(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.80), _NOW)
    scheduled_log._maybe_alert_resolved(_snap(0.95, 0.95), _NOW)  # same day
    assert len(sends) == 2  # one high + one low, no repeats
    tomorrow = datetime(2026, 7, 22, 15, 0, tzinfo=_TZ)
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.80), tomorrow)
    assert len(sends) == 4  # re-armed next day


def test_empty_state_file_does_not_block(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    (tmp_path / "resolved.json").write_text("")  # 0-byte restore artifact
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.50), _NOW)
    assert len(sends) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_resolved_alert.py -q`
Expected: FAIL — `AttributeError: module 'scheduled_log' has no attribute '_maybe_alert_resolved'`.

- [ ] **Step 3: Add constants + a shared state-load helper**

In `scheduled_log.py`, next to the existing `STATE_PATH` constant, add:

```python
RESOLVED_STATE_PATH = os.path.join(os.path.dirname(__file__), "resolved_alert_state.json")
RESOLVED_ALERT_PCT = 70
```

Add this helper above `_maybe_alert_cli`:

```python
def _load_alert_state(path: str) -> dict:
    """Load a JSON alert-state dict, tolerating a missing/empty/corrupt file.

    The workflow's `git show … > file || true` restore leaves a 0-byte file when
    the state doesn't yet exist on the data branch; treat any parse failure as
    empty so it never blocks an alert."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}
```

- [ ] **Step 4: Refactor `_maybe_alert_cli` to use the helper**

In `_maybe_alert_cli`, replace the inline state-load block:

```python
        # An empty/corrupt state file must not block the alert. The workflow's
        # `git show … > state.json || true` restore leaves a 0-byte file when the
        # state doesn't exist on the data branch yet, so tolerate a parse failure.
        state = {}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH) as fh:
                    state = json.load(fh)
            except (OSError, ValueError):
                state = {}
        if not isinstance(state, dict):
            state = {}
        if state.get("last_alerted_day") == today.isoformat():
```

with:

```python
        state = _load_alert_state(STATE_PATH)
        if state.get("last_alerted_day") == today.isoformat():
```

- [ ] **Step 5: Add `_maybe_alert_resolved`**

Add below `_maybe_alert_cli`:

```python
def _maybe_alert_resolved(snap: dict, now: datetime) -> None:
    """Ping once per variable per day the first time its displayed Resolved %
    reaches RESOLVED_ALERT_PCT. High and low fire independently. Best-effort —
    a failure logs and never blocks the surrounding logging."""
    try:
        import notify
        today = settlement.climate_day_of(now).isoformat()
        state = _load_alert_state(RESOLVED_STATE_PATH)
        dirty = False
        for var in ("high", "low"):
            d = (snap.get("today") or {}).get(var)
            if not d:
                continue
            pct = model.displayed_resolved(d)
            if pct < RESOLVED_ALERT_PCT or state.get(var) == today:
                continue
            title = f"Dallas {var.capitalize()} locking in"
            body = f"{pct}% resolved · ≈{d['consensus']:g}°F"
            if notify.send_ntfy(title, body):
                state[var] = today
                dirty = True
                print(f"Resolved alert sent: {var} {pct}%")
            else:
                print(f"Resolved alert: send_ntfy False for {var} ({pct}%)")
        if dirty:
            with open(RESOLVED_STATE_PATH, "w") as fh:
                json.dump(state, fh)
    except Exception as e:
        print(f"Resolved alert skipped: {e}")
```

- [ ] **Step 6: Wire it into `_log_snapshots`**

In `_log_snapshots`, right after `_attach_market(cli_snap, now)`:

```python
    _attach_market(cli_snap, now)
    _maybe_alert_resolved(cli_snap, now)
```

- [ ] **Step 7: Run the resolved-alert test + the cli-alert test (refactor regression)**

Run: `python3 -m pytest tests/test_resolved_alert.py tests/test_cli_alert.py -q`
Expected: PASS (4 + 4).

- [ ] **Step 8: Commit**

```bash
git add scheduled_log.py tests/test_resolved_alert.py
git commit -m "feat: ntfy alert when high/low displayed Resolved % hits 70"
```

---

### Task 3: Persist `resolved_alert_state.json` in the workflow

**Files:**
- Modify: `.github/workflows/log.yml`

No unit test (config change); verified by reading the diff. `NTFY_TOPIC` is already passed to the "Append this snapshot" step — no env change needed.

- [ ] **Step 1: Restore the state file from the data branch**

In the "Restore existing logs from the data branch" step, add alongside the other `git show` restores (next to the `cli_alert_state.json` line):

```yaml
            git show origin/data:resolved_alert_state.json > resolved_alert_state.json 2>/dev/null || true
```

- [ ] **Step 2: Publish the state file to the data branch**

In the "Publish the logs to the data branch" step, add a copy line next to the `cli_alert_state.json` cp line:

```bash
          [ -f resolved_alert_state.json ] && cp resolved_alert_state.json "$tmp/resolved_alert_state.json"
```

and an add line next to the `cli_alert_state.json` add line:

```bash
          [ -f resolved_alert_state.json ] && git add -f resolved_alert_state.json
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/log.yml
git commit -m "ci: persist resolved_alert_state.json on the data branch"
```

---

### Final verification

- [ ] Run the whole suite:

Run: `python3 -m pytest -q`
Expected: all pass (existing 625 + the new tests).

- [ ] Optional live smoke test (after merge): trigger the log workflow and read the log —
  `gh workflow run log.yml --repo jaredmcelreath-a11y/Weather-Model`, then
  `gh run view <id> --log | grep -i "Resolved alert"`. Whether it fires depends on
  the live Resolved % at that moment; a `send_ntfy False` line would flag a config
  problem, `Resolved alert sent: <var>` confirms a real push.
