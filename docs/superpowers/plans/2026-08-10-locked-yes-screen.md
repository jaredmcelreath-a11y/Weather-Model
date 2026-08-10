# Locked/Guarded YES Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Screen surface brackets the day has already won and the market still prices cheaply — the YES side it has never had.

**Architecture:** Two new predicates in `screen_rules.py` beside their fade mirrors, emitted by `screen_pass` into a **separate** `scan_locked.jsonl` so `screen_score` is untouched, rendered as a second table, and pushed by the existing alert loop using its own state document.

**Tech Stack:** Python 3.11 (CI) / 3.9 (local), Streamlit, pytest. No new dependencies, no new network calls.

## Global Constraints

- **`screen_score.py` is not modified by any task in this plan.** The fade side's 66-settled record must stay exactly as comparable as it is today. That is the entire reason for a separate log.
- **`dead_candidate` and `forecast_candidate` are not modified.** New rules sit beside them.
- **Settled basis, never the raw reading.** Any comparison of a realized temperature against a strike goes through `settled_range()`. Comparing raw readings is what produced the false Atlanta "dead" on 2026-08-06 against a bracket Kalshi had at 91% YES.
- **No calibrated probability.** Rules report a MARGIN in degrees, as the fade side reports a gap.
- **Named constants:** `MIN_LIVE_YES_PRICE = 0.20`, `MAX_LIVE_YES_PRICE = 0.90`, margin bar is the existing `MIN_CANDIDATE_GAP_F = 4.0`.
- **Never `required_gap` for the margin bar.** It scales by forecast error at the lead where the extreme forms and demands 9.7 °F for a same-day low, which would silence every case this feature exists for.
- **Local test command:** `python3 -m pytest` from the repo root. There is no bare `python` on this Mac.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `screen_forecast.py` (modify) | Promote `_still_open` to public `still_open`; add `remaining_extreme` — the one number the guarded rule needs. |
| `screen_rules.py` (modify) | `yes_ask_of`, the YES band, `locked_candidate`, `guarded_candidate`, `settled_inside`. Beside their mirrors, sharing `winning_range`/`settled_range`. |
| `scan_log.py` (modify) | Two new path constants. |
| `screen.py` (modify) | Emit locked rows to the new log; publish `remaining` into the reference. |
| `screen_alert.py` (modify) | The locked rules on the 5-minute loop, with their own state document. |
| `screen_view.py` (modify) | The second table. |

## One deliberate refinement of the spec

The spec states the guarded rule's first condition as `bracket_gap(row, bound) == 0`. This plan implements it as **`settled_inside(row, bound)`** — every whole-°F value the reading could settle at falls inside the bracket — because `bracket_gap` compares the raw reading against the strikes, which the Global Constraints forbid. Same intent, on the basis the spec insists on everywhere else. Phoenix still fires: `settled_range(93.2)` is `(92, 94)`, and the bracket's `lo` is 92.

---

### Task 1: The remaining-forecast window

**Files:**
- Modify: `screen_forecast.py:195` (`_still_open`), `screen_forecast.py:227` (its one caller)
- Test: `tests/test_screen_forecast.py`

**Interfaces:**
- Produces: `still_open(day_periods, variable) -> list` (renamed from `_still_open`; the only caller is `storm_chance`, and no test references the old name)
- Produces: `remaining_extreme(periods, day, tzname, variable, now) -> float | None`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_screen_forecast.py
def _period(hour, temp, forecast="Sunny"):
    return {"startTime": f"2026-08-09T{hour:02d}:00:00-07:00", "temperature": temp,
            "probabilityOfPrecipitation": {"value": 0}, "shortForecast": forecast}


_PHX_DAY = [_period(h, t) for h, t in
            [(5, 93), (6, 93), (14, 110), (17, 110), (18, 109), (19, 107),
             (20, 103), (21, 101), (22, 99), (23, 97)]]
_PHX_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)     # 17:18 LST


def test_a_lows_remaining_extreme_is_the_coldest_hour_still_ahead():
    # The threat to a low bracket is the temperature FALLING, so what matters is
    # the minimum still to come -- 97 at 11pm, the last hour of the climate day.
    got = screen_forecast.remaining_extreme(
        _PHX_DAY, date(2026, 8, 9), "America/Phoenix", "low", _PHX_NOW)
    assert got == 97.0


def test_a_lows_window_runs_to_midnight_not_to_the_peak():
    # An evening downdraft can still crash a low, which is why still_open does
    # not truncate for a low. The 5am readings are behind us and excluded by
    # `now`, not by the window.
    window = screen_forecast.still_open(
        screen_forecast._day_periods(_PHX_DAY, date(2026, 8, 9), "America/Phoenix"),
        "low")
    assert len(window) == len(_PHX_DAY)


def test_a_highs_remaining_extreme_is_the_hottest_hour_still_ahead():
    got = screen_forecast.remaining_extreme(
        _PHX_DAY, date(2026, 8, 9), "America/Phoenix", "high", _PHX_NOW)
    assert got == 110.0


def test_a_high_after_its_peak_has_no_remaining_window():
    # still_open truncates a high at its peak, so once the peak has passed there
    # is nothing left that can move it.
    late = datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc)      # 21:00 LST
    assert screen_forecast.remaining_extreme(
        _PHX_DAY, date(2026, 8, 9), "America/Phoenix", "high", late) is None


def test_remaining_extreme_of_a_day_with_no_periods_is_none():
    assert screen_forecast.remaining_extreme(
        [], date(2026, 8, 9), "America/Phoenix", "low", _PHX_NOW) is None
```

> Add `from datetime import date, datetime, timezone` to the test file's imports if they are not all present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: FAIL — `AttributeError: module 'screen_forecast' has no attribute 'remaining_extreme'`

- [ ] **Step 3: Write the implementation**

Rename `_still_open` to `still_open` (definition at line 195 and its use inside `storm_chance`), keeping its docstring exactly as it is, and add below `storm_chance`:

```python
def remaining_extreme(periods: list, day: date, tzname: str, variable: str,
                      now):
    """The most extreme temperature still AHEAD in this variable's window.

    The number that says whether a bracket the realized extreme has already
    satisfied can still be taken away: for a low the coldest hour still to come,
    for a high the hottest. None when no such hour is left -- a high past its
    peak cannot move, and neither can any variable once the day is over.

    Windowed exactly as storm_chance is, and for the same reason: cut on the
    WHOLE day first, then narrowed to what is ahead, so a high at 9pm does not
    re-peak on the evening and start reporting hours that cannot reach it."""
    day_periods = _day_periods(periods, day, tzname)
    window = [p for start, p in still_open(day_periods, variable)
              if start >= now]
    temps = [float(p["temperature"]) for p in window
             if p.get("temperature") is not None]
    if not temps:
        return None
    return min(temps) if variable == "low" else max(temps)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_forecast.py -q`
Expected: all pass, including the existing `storm_chance` tests.

- [ ] **Step 5: Commit**

```bash
git add screen_forecast.py tests/test_screen_forecast.py
git commit -m "$(cat <<'EOF'
feat(screen): expose the forecast still ahead in a variable's window

The number that says whether a bracket the realized extreme has already
satisfied can still be taken away. Windowed exactly as storm_chance is, so a
high past its peak correctly reports nothing left.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The YES price gate

**Files:**
- Modify: `screen_rules.py` (beside `no_ask_of`/`within_band`, ~line 127-152)
- Test: `tests/test_screen_rules_band.py`

**Interfaces:**
- Consumes: `scan_log.dollars(value) -> float | None`
- Produces: `MIN_LIVE_YES_PRICE = 0.20`, `MAX_LIVE_YES_PRICE = 0.90`,
  `yes_ask_of(market) -> float | None`, `within_yes_band(price) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_screen_rules_band.py
def test_yes_ask_is_what_buying_yes_actually_costs():
    assert screen_rules.yes_ask_of({"yes_ask_dollars": "0.3900"}) == 0.39


def test_yes_ask_falls_back_to_the_no_bid_inverted():
    # Buying YES sells against the resting NO bid, so YES ask = 1 - no bid.
    assert screen_rules.yes_ask_of({"no_bid_dollars": "0.6100"}) == 0.39


def test_yes_ask_of_an_unquoted_market_is_none():
    assert screen_rules.yes_ask_of({}) is None


def test_the_yes_band_rejects_what_the_market_already_agrees_with():
    # Above the cap there is under 11% left to win.
    assert screen_rules.within_yes_band(0.90) is True
    assert screen_rules.within_yes_band(0.91) is False


def test_the_yes_band_rejects_a_price_that_says_our_reference_is_wrong():
    # A supposedly locked bracket at 5c is far likelier to mean a bad station
    # reading than free money -- exactly when this screen must not shout.
    assert screen_rules.within_yes_band(0.20) is True
    assert screen_rules.within_yes_band(0.05) is False


def test_an_unquoted_yes_row_survives_the_band():
    # An absent quote is thin liquidity, not evidence about the bracket.
    assert screen_rules.within_yes_band(None) is True


def test_phoenix_sits_inside_the_yes_band():
    assert screen_rules.within_yes_band(0.39) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_rules_band.py -q`
Expected: FAIL — `AttributeError: module 'screen_rules' has no attribute 'yes_ask_of'`

- [ ] **Step 3: Write the implementation**

Add to `screen_rules.py`, directly after `within_band`:

```python
# The mirror band, for the side that BUYS rather than fades. Above the cap the
# market already agrees and under 11% is left to win; below the floor the market
# is saying the screen's REFERENCE is wrong, not that the price is -- a
# supposedly locked bracket at 5c is far likelier to mean a bad station reading
# than free money, and that is exactly when this screen must not shout.
#
# One band, applied at firing (screen.py), on the live loop (screen_alert) and
# at page load (screen_view), so a row cannot be logged under a standard the
# page then disagrees with.
MIN_LIVE_YES_PRICE = 0.20
MAX_LIVE_YES_PRICE = 0.90


def yes_ask_of(market: dict):
    """Dollars to BUY YES on this market right now, or None when unquoted.

    Kalshi's own YES ask when there is one, else the NO bid inverted: buying YES
    sells against the resting NO bid, so YES ask = 1 - no bid. Prices arrive as
    dollar STRINGS ("0.3900"), the gotcha that silently empties a scan pass."""
    ask = scan_log.dollars(market.get("yes_ask_dollars"))
    if ask is not None:
        return ask
    bid = scan_log.dollars(market.get("no_bid_dollars"))
    return None if bid is None else round(1.0 - bid, 2)


def within_yes_band(price) -> bool:
    """Whether a live YES price is worth showing or pushing.

    An unquoted row (None) SURVIVES, as on the fade side: an absent quote is
    thin liquidity or a market that has since closed, not evidence."""
    if price is None:
        return True
    return MIN_LIVE_YES_PRICE <= float(price) <= MAX_LIVE_YES_PRICE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_band.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add screen_rules.py tests/test_screen_rules_band.py
git commit -m "$(cat <<'EOF'
feat(screen): add the YES-side price band and its ask parser

Mirrors no_ask_of and within_band for the side that buys. The floor carries the
fade side's meaning mirrored: below 20c the market is saying our reference is
wrong, not that the price is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `locked_candidate` — the hard rule

**Files:**
- Modify: `screen_rules.py` (after `dead_candidate`, end of file)
- Test: `tests/test_screen_rules_locked.py`

**Interfaces:**
- Consumes: `winning_range(row)`, `settled_range(bound)`, `within_yes_band(price)`
- Produces: `settled_inside(row, bound) -> bool`, `_yes_candidate(...)`,
  `locked_candidate(row, bound, now_iso) -> dict | None` with
  `kind="locked"`, `side="YES"`, `margin`, `reference`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_rules_locked.py
"""locked_candidate: brackets the realized extreme has made impossible to LOSE."""
import screen_rules

_NOW = "2026-08-09T20:00:00Z"


def _row(variable, strike_type, floor=None, cap=None, yes_ask=0.40):
    return {"series": "KXLOWTPHX", "variable": variable, "ticker": "KX-26AUG09-X",
            "strike_type": strike_type, "floor": floor, "cap": cap,
            "label": "test", "yes_ask": yes_ask, "yes_bid": 0.36,
            "volume": 100, "hours_to_close": 7.0}


def test_a_low_tail_open_downward_is_locked_once_the_low_reaches_it():
    # "83 or below" (less, cap 84 -> winning range (None, 83)). A low can only
    # fall, so once it touches 83 the bracket can never be taken away.
    row = _row("low", "less", cap=84)
    got = screen_rules.locked_candidate(row, 80.0, _NOW)
    assert got is not None
    assert got["kind"] == "locked" and got["side"] == "YES"


def test_a_high_tail_open_upward_is_locked_once_the_high_reaches_it():
    # "91 or above" (greater, floor 90 -> winning range (91, None)).
    row = _row("high", "greater", floor=90)
    got = screen_rules.locked_candidate(row, 95.0, _NOW)
    assert got is not None and got["kind"] == "locked"


def test_the_phoenix_bracket_is_NOT_locked_because_a_low_can_still_fall():
    # "92 or above" for a LOW is unbounded in the direction the extreme CANNOT
    # move. Six hours of climate day remain in which a downdraft could crash it.
    row = _row("low", "greater", floor=91)
    assert screen_rules.locked_candidate(row, 93.2, _NOW) is None


def test_a_bounded_bracket_is_never_locked():
    # "90 to 91" can always be lost -- a low can keep falling out the bottom.
    row = _row("low", "between", floor=90, cap=91)
    assert screen_rules.locked_candidate(row, 90.5, _NOW) is None


def test_the_lock_is_judged_on_the_settled_basis_not_the_raw_reading():
    # 83.6F is on the whole-Celsius grid (28.667C is not -- 83.3 is). Use a
    # reading whose slack straddles the strike: settled_range(83.4) spans 83-84
    # at whole-degC slack, so "83 or below" is NOT safe.
    row = _row("low", "less", cap=84)          # winning range (None, 83)
    assert screen_rules.settled_range(83.4)[1] >= 84
    assert screen_rules.locked_candidate(row, 83.4, _NOW) is None


def test_margin_says_how_deep_the_lock_is():
    row = _row("low", "less", cap=84)          # winning range (None, 83)
    got = screen_rules.locked_candidate(row, 80.0, _NOW)
    assert got["margin"] == 83.0 - screen_rules.settled_range(80.0)[1]


def test_no_realized_bound_means_no_lock():
    assert screen_rules.locked_candidate(_row("low", "less", cap=84), None, _NOW) is None


def test_a_price_the_market_already_agrees_with_is_not_flagged():
    row = _row("low", "less", cap=84, yes_ask=0.95)
    assert screen_rules.locked_candidate(row, 80.0, _NOW) is None


def test_a_suspiciously_cheap_lock_is_not_flagged():
    row = _row("low", "less", cap=84, yes_ask=0.04)
    assert screen_rules.locked_candidate(row, 80.0, _NOW) is None


def test_a_locked_row_never_looks_like_a_fade_row():
    # The two logs are separate, but a YES row must be unmistakable even if they
    # are ever read together: no 'gap', no 'forecast'.
    got = screen_rules.locked_candidate(_row("low", "less", cap=84), 80.0, _NOW)
    assert "gap" not in got and "forecast" not in got
    assert got["reference"] == 80.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_rules_locked.py -q`
Expected: FAIL — `AttributeError: module 'screen_rules' has no attribute 'locked_candidate'`

- [ ] **Step 3: Write the implementation**

Append to `screen_rules.py`:

```python
# ---- The YES side: brackets the day has already won -----------------------
#
# Not a mirror of dead_candidate, because the physics is not symmetric. A low
# can only FALL, so a low bracket left open UPWARD ("92 or above") never becomes
# certain while the day runs -- an evening downdraft can still crash it. Only a
# tail open in the direction the extreme can still move is ever safe.

def settled_inside(row: dict, bound) -> bool:
    """Whether EVERY whole-degF value this reading could settle at wins.

    On the settled basis, never the raw reading: a 92.4 that could be reported
    as 91 does not "already sit inside" a 92-and-above bracket, however it looks
    on the thermometer."""
    if bound is None:
        return False
    lo, hi = winning_range(row)
    lowest, highest = settled_range(bound)
    if lo is not None and lowest < lo:
        return False
    if hi is not None and highest > hi:
        return False
    return True


def _yes_candidate(row: dict, kind: str, reference: float, margin: float,
                   price, now_iso: str) -> dict:
    """A YES-side candidate. Deliberately NOT the fade shape: `margin` not
    `gap`, `reference` not `forecast`, and an explicit `side`, so a row from
    this screen can never be mistaken for a fade even if the two logs are read
    together."""
    return {
        "ts": now_iso,
        "series": row.get("series"),
        "variable": row.get("variable"),
        "ticker": row.get("ticker"),
        "floor": row.get("floor"),
        "cap": row.get("cap"),
        "strike_type": row.get("strike_type"),
        "label": row.get("label"),
        "side": "YES",
        "price": price,
        "yes_bid": row.get("yes_bid"),
        "volume": row.get("volume"),
        "reference": reference,
        "margin": margin,
        "kind": kind,
        "hours_to_close": row.get("hours_to_close"),
    }


def locked_candidate(row: dict, bound, now_iso: str):
    """A bracket the realized extreme has made impossible to LOSE, or None.

    Fires only on a tail left open in the direction the extreme can still move:
    a LOW bracket unbounded BELOW once the low has reached it, or a HIGH bracket
    unbounded ABOVE. Everything else can still be taken away."""
    if bound is None:
        return None
    price = row.get("yes_ask")
    if not within_yes_band(price):
        return None
    variable = row.get("variable")
    lo, hi = winning_range(row)
    lowest_settled, highest_settled = settled_range(bound)
    if variable == "low":
        # Unbounded BELOW, and every value it could settle at already wins.
        if lo is not None or hi is None or highest_settled > hi:
            return None
        margin = round(hi - highest_settled, 2)
    elif variable == "high":
        if hi is not None or lo is None or lowest_settled < lo:
            return None
        margin = round(lowest_settled - lo, 2)
    else:
        return None
    return _yes_candidate(row, "locked", float(bound), margin, price, now_iso)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_locked.py tests/test_screen_rules_dead.py -q`
Expected: all pass — `dead_candidate` is untouched.

- [ ] **Step 5: Commit**

```bash
git add screen_rules.py tests/test_screen_rules_locked.py
git commit -m "$(cat <<'EOF'
feat(screen): flag brackets the realized extreme cannot take away

Not a mirror of dead_candidate: a low can only fall, so a low bracket open
UPWARD never becomes certain while the day runs. Only a tail open in the
direction the extreme can still move is ever safe, and the test pins Phoenix's
"92 or above" as NOT locked for exactly that reason.

Judged on the settled basis, so a reading whose whole-degF slack straddles the
strike does not claim a lock it cannot carry.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `guarded_candidate` — the soft rule

**Files:**
- Modify: `screen_rules.py` (after `locked_candidate`)
- Test: `tests/test_screen_rules_guarded.py`

**Interfaces:**
- Consumes: `settled_inside`, `winning_range`, `within_yes_band`, `_yes_candidate`, `MIN_CANDIDATE_GAP_F`
- Produces: `guarded_candidate(row, bound, remaining, now_iso) -> dict | None` with `kind="guarded"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_rules_guarded.py
"""guarded_candidate: realized already inside, and the forecast protects it."""
import screen_rules

_NOW = "2026-08-09T20:00:00Z"


def _row(variable, strike_type, floor=None, cap=None, yes_ask=0.39):
    return {"series": "KXLOWTPHX", "variable": variable, "ticker": "KX-26AUG09-X",
            "strike_type": strike_type, "floor": floor, "cap": cap,
            "label": "92° or above", "yes_ask": yes_ask, "yes_bid": 0.36,
            "volume": 9381, "hours_to_close": 7.0}


def test_the_phoenix_case_fires():
    # THE regression case, live 2026-08-09 17:18 LST. Realized low 93.2 (raw min
    # 93.0), bracket "92 or above", remaining forecast bottoming at 96 at
    # midnight, quoted 39c while the market's implied low was 91.
    row = _row("low", "greater", floor=91)          # winning range (92, None)
    got = screen_rules.guarded_candidate(row, 93.2, 96.0, _NOW)
    assert got is not None
    assert got["kind"] == "guarded" and got["side"] == "YES"
    assert got["margin"] == 4.0                     # 96 - 92, just clearing the bar


def test_a_thinner_margin_than_the_bar_does_not_fire():
    row = _row("low", "greater", floor=91)
    assert screen_rules.guarded_candidate(row, 93.2, 95.9, _NOW) is None


def test_a_low_is_only_ever_threatened_from_below():
    # A low cannot rise out of the top of a bracket, so a bounded bracket is
    # judged on its lo alone -- here 90, against a remaining forecast of 96.
    row = _row("low", "between", floor=90, cap=94)
    got = screen_rules.guarded_candidate(row, 92.0, 96.0, _NOW)
    assert got["margin"] == 6.0


def test_a_high_is_only_ever_threatened_from_above():
    row = _row("high", "between", floor=88, cap=95)
    got = screen_rules.guarded_candidate(row, 90.0, 91.0, _NOW)
    assert got["margin"] == 4.0                     # 95 - 91


def test_an_unbounded_tail_is_locked_work_not_guarded_work():
    # The two rules partition: nothing threatens this side, so guarded declines
    # and locked_candidate owns it.
    row = _row("low", "less", cap=84)               # winning range (None, 83)
    assert screen_rules.guarded_candidate(row, 80.0, 96.0, _NOW) is None


def test_a_realized_extreme_outside_the_bracket_does_not_fire():
    # This is a forecast bet, not a bracket the day has already won.
    row = _row("low", "greater", floor=91)          # winning range (92, None)
    assert screen_rules.guarded_candidate(row, 89.0, 96.0, _NOW) is None


def test_inside_is_judged_on_the_settled_basis():
    # 92.4F could be reported as 91, which loses a 92-and-above bracket.
    row = _row("low", "greater", floor=91)
    assert screen_rules.settled_range(92.4)[0] <= 91
    assert screen_rules.guarded_candidate(row, 92.4, 96.0, _NOW) is None


def test_no_remaining_forecast_means_no_guard():
    row = _row("low", "greater", floor=91)
    assert screen_rules.guarded_candidate(row, 93.2, None, _NOW) is None


def test_the_yes_band_applies_here_too():
    assert screen_rules.guarded_candidate(
        _row("low", "greater", floor=91, yes_ask=0.95), 93.2, 96.0, _NOW) is None
    assert screen_rules.guarded_candidate(
        _row("low", "greater", floor=91, yes_ask=0.04), 93.2, 96.0, _NOW) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_rules_guarded.py -q`
Expected: FAIL — `AttributeError: module 'screen_rules' has no attribute 'guarded_candidate'`

- [ ] **Step 3: Write the implementation**

Append to `screen_rules.py`:

```python
def guarded_candidate(row: dict, bound, remaining, now_iso: str):
    """A bracket the realized extreme already wins and the forecast protects.

    Two conditions: every value the realized extreme could settle at already
    wins, AND the forecast still ahead keeps it there by MIN_CANDIDATE_GAP_F.

    Each variable has exactly ONE threatened edge, which is what makes this and
    locked_candidate partition rather than overlap: a low can only fall, so only
    its `lo` can be breached; a high can only rise, so only its `hi` can. An
    unbounded threatened edge means nothing can take the bracket away, which is
    locked_candidate's job, not this one's.

    The bar is the FLAT MIN_CANDIDATE_GAP_F, never required_gap. required_gap
    scales by forecast error at the lead where the extreme FORMS; here it has
    already formed, and the only question left is whether the remaining hours
    can undercut it -- a short-range, convection-dominated risk. required_gap
    would demand 9.7F of a same-day low and silence every case this exists for.
    Read the row's Storm column beside this: it is the risk that breaks it."""
    if bound is None or remaining is None:
        return None
    price = row.get("yes_ask")
    if not within_yes_band(price):
        return None
    if not settled_inside(row, bound):
        return None
    variable = row.get("variable")
    lo, hi = winning_range(row)
    if variable == "low":
        if lo is None:                    # nothing below to breach
            return None
        margin = round(float(remaining) - lo, 2)
    elif variable == "high":
        if hi is None:
            return None
        margin = round(hi - float(remaining), 2)
    else:
        return None
    if margin < MIN_CANDIDATE_GAP_F:
        return None
    return _yes_candidate(row, "guarded", float(bound), margin, price, now_iso)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_guarded.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add screen_rules.py tests/test_screen_rules_guarded.py
git commit -m "$(cat <<'EOF'
feat(screen): flag cheap brackets the forecast is protecting

Realized extreme already inside on the settled basis, plus a 4F margin from the
forecast still ahead. Each variable has exactly one threatened edge -- a low can
only fall, a high can only rise -- which is what makes this and locked_candidate
partition rather than overlap.

The flat bar, never required_gap: the extreme has already formed, so the risk is
short-range convection, not forecast error at the forming lead. required_gap
would demand 9.7F of a same-day low and silence the Phoenix case entirely.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Emit them from the pass, into their own log

**Files:**
- Modify: `scan_log.py:29-31` (path constants)
- Modify: `screen.py` (the `if not in_progress: continue` block and the write at the end)
- Test: `tests/test_screen_locked_pass.py`

**Interfaces:**
- Produces: `scan_log.LOCKED_PATH = "scan_locked.jsonl"`,
  `scan_log.LOCKED_ALERT_STATE_PATH = "screen_locked_alert_state.json"`
- Produces: `screen_pass` returns `{"candidates", "cities", "errors", "locked"}`
- Produces: reference gains `cities[series]["remaining"] = {"YYYY-MM-DD": float|None}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_locked_pass.py
"""screen_pass emits YES rows to their OWN log, never the candidate log."""
from datetime import datetime, timezone

import scan_log
import screen

_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)     # 17:18 LST Phoenix

# Phoenix Aug 9: dawn low already at 93, evening forecast bottoming at 96.
_PERIODS = [
    {"startTime": "2026-08-09T06:00:00-07:00", "temperature": 93,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Clear"},
    {"startTime": "2026-08-09T18:00:00-07:00", "temperature": 109,
     "probabilityOfPrecipitation": {"value": 6}, "shortForecast": "Sunny"},
    {"startTime": "2026-08-09T23:00:00-07:00", "temperature": 96,
     "probabilityOfPrecipitation": {"value": 11}, "shortForecast": "Clear"},
]


def _market():
    """KXLOWTPHX "92 or above" at 39c -- the live 2026-08-09 quote."""
    return {"ticker": "KXLOWTPHX-26AUG09-T91", "yes_bid_dollars": "0.3600",
            "yes_ask_dollars": "0.3900", "no_bid_dollars": "0.6100",
            "yes_sub_title": "92° or above", "floor_strike": 91,
            "cap_strike": None, "strike_type": "greater", "volume_fp": "9381",
            "close_time": "2026-08-10T07:00:00Z"}


def _obs(temp_c=33.9):        # 93.0F
    return [{"properties": {"timestamp": f"2026-08-09T{h:02d}:00:00+00:00",
                            "temperature": {"value": temp_c}}}
            for h in (13, 14)]


def _deps(written, published):
    return screen.Deps(
        list_series=lambda: [{"ticker": "KXLOWTPHX"}],
        list_markets=lambda series, status=None: [_market()],
        resolve_point=lambda lat, lon: {
            "timezone": "America/Phoenix",
            "forecast_hourly": "https://example.test/hourly",
            "stations_url": "https://example.test/stations"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: _obs(),
        append_rows=lambda path, rows: written.setdefault(path, []).extend(rows) or len(rows),
        station_for=lambda url: "KPHX",
        sleep=lambda s: None,
        write_reference=lambda obj: published.append(obj),
    )


def test_the_phoenix_row_lands_in_the_locked_log():
    written, published = {}, []
    screen.screen_pass(_NOW, _deps(written, published))
    rows = written.get(scan_log.LOCKED_PATH) or []
    assert [r["kind"] for r in rows] == ["guarded"]
    assert rows[0]["ticker"] == "KXLOWTPHX-26AUG09-T91"
    assert rows[0]["margin"] == 4.0


def test_a_yes_row_never_touches_the_candidate_log():
    # screen_score reads that log and applies fade math to every row in it.
    written, published = {}, []
    screen.screen_pass(_NOW, _deps(written, published))
    for row in written.get(scan_log.CANDIDATES_PATH) or []:
        assert row.get("side") != "YES"


def test_a_locked_row_carries_the_storm_risk_that_could_break_it():
    written, published = {}, []
    screen.screen_pass(_NOW, _deps(written, published))
    assert "storm" in (written[scan_log.LOCKED_PATH][0])


def test_the_reference_publishes_the_forecast_still_ahead():
    # screen_alert cannot recompute forecasts; it re-folds what this publishes.
    written, published = {}, []
    screen.screen_pass(_NOW, _deps(written, published))
    assert published[0]["cities"]["KXLOWTPHX"]["remaining"]["2026-08-09"] == 96.0


def test_the_pass_reports_how_many_locked_rows_it_wrote():
    written, published = {}, []
    got = screen.screen_pass(_NOW, _deps(written, published))
    assert got["locked"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_locked_pass.py -q`
Expected: FAIL — `AttributeError: module 'scan_log' has no attribute 'LOCKED_PATH'`

- [ ] **Step 3: Write the implementation**

In `scan_log.py`, beside the existing path constants:

```python
LOCKED_PATH = "scan_locked.jsonl"             # the YES side, kept SEPARATE from
                                              # scan_candidates so screen_score
                                              # never applies fade math to it
LOCKED_ALERT_STATE_PATH = "screen_locked_alert_state.json"
```

In `screen.py`, initialise a second list beside `candidates` at the top of
`screen_pass`:

```python
    candidates, cities, errors = [], 0, 0
    locked = []
```

Then replace the `dead_candidate` block with:

```python
            for r in day_rows:
                hit = screen_rules.dead_candidate(r, bound, now_iso)
                if hit:
                    hit["storm"] = storm
                    # A dead row's Ref is the realized bound, not a forecast.
                    hit["drift"] = hit["drift_ref"] = None
                    candidates.append(hit)

            # The YES side. Published as well as used, because screen_alert
            # cannot recompute forecasts and the guarded rule needs the hours
            # still ahead -- exactly like `realized` above.
            remaining = screen_forecast.remaining_extreme(
                periods, day, tzname, variable, now)
            reference[series].setdefault("remaining", {})[day.isoformat()] = remaining
            for r in day_rows:
                # Locked first: it is the half that claims certainty, and
                # "cannot lose" is strictly more useful than "the forecast is
                # holding it".
                hit = screen_rules.locked_candidate(r, bound, now_iso)
                if hit is None:
                    hit = screen_rules.guarded_candidate(r, bound, remaining,
                                                         now_iso)
                if hit:
                    hit["storm"] = storm
                    locked.append(hit)
```

And at the end, beside the existing write:

```python
    written = deps.append_rows(scan_log.CANDIDATES_PATH, candidates)
    written_locked = deps.append_rows(scan_log.LOCKED_PATH, locked)
```

with the return becoming:

```python
    return {"candidates": written or 0, "cities": cities, "errors": errors,
            "locked": written_locked or 0}
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass. `screen_score`'s tests must be untouched and green.

- [ ] **Step 5: Commit**

```bash
git add scan_log.py screen.py tests/test_screen_locked_pass.py
git commit -m "$(cat <<'EOF'
feat(screen): write the YES side to its own log

scan_locked.jsonl, separate from scan_candidates because screen_score computes
a NO cost and a hit rate for every row it reads. A side field plus a filter
would work; physical separation cannot go wrong, and the fade side's 66-settled
record is worth that.

Also publishes `remaining` into the reference, as `realized` already is, so the
alert loop can apply the guarded rule without recomputing forecasts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Push them on the 5-minute loop

**Files:**
- Modify: `screen_alert.py`
- Test: `tests/test_screen_alert_locked.py`

**Interfaces:**
- Consumes: `screen_rules.locked_candidate/guarded_candidate/yes_ask_of/within_yes_band`, `scan_log.LOCKED_ALERT_STATE_PATH`
- Produces: `city_locked(series, day, markets, realized, now, remaining) -> list`,
  `locked_title(count) -> str`, `_locked_line(candidate) -> str`
- Produces: `Deps` gains `read_locked_state` and `write_locked_state`
- Produces: `check` returns `{"cities", "found", "new", "pushed", "locked_found", "locked_new"}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_alert_locked.py
"""The alert loop's YES side: its own rules, its own state document."""
from datetime import datetime, timezone

import screen_alert

_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)

_REFERENCE = {
    "generated": "2026-08-10T00:10:00Z",
    "cities": {"KXLOWTPHX": {"station": "KPHX", "timezone": "America/Phoenix",
                             "days": {"2026-08-09": 93.0},
                             "remaining": {"2026-08-09": 96.0}}},
}


def _market():
    return {"ticker": "KXLOWTPHX-26AUG09-T91", "yes_bid_dollars": "0.3600",
            "yes_ask_dollars": "0.3900", "no_bid_dollars": "0.6100",
            "yes_sub_title": "92° or above", "floor_strike": 91,
            "cap_strike": None, "strike_type": "greater", "volume_fp": "9381",
            "close_time": "2026-08-10T07:00:00Z"}


def _obs(temp_c=33.9):        # 93.0F
    return [{"properties": {"timestamp": f"2026-08-09T{h:02d}:00:00+00:00",
                            "temperature": {"value": temp_c}}}
            for h in (13, 14)]


class Harness:
    def __init__(self, state=None, locked_state=None, ok=True):
        self.state = state or {}
        self.locked_state = locked_state or {}
        self.ok = ok
        self.sent = []
        self.written = []
        self.locked_written = []

    def deps(self):
        return screen_alert.Deps(
            read_reference=lambda: _REFERENCE,
            read_state=lambda: dict(self.state),
            write_state=lambda obj: self.written.append(obj),
            read_locked_state=lambda: dict(self.locked_state),
            write_locked_state=lambda obj: self.locked_written.append(obj),
            list_markets=lambda series: [_market()],
            fetch_obs=lambda station, start, end: _obs(),
            notify=lambda title, body: self.sent.append((title, body)) or self.ok,
            sleep=lambda s: None,
        )


def test_a_new_locked_row_pushes_and_records_separately():
    h = Harness()
    got = screen_alert.check(_NOW, h.deps())
    assert got["locked_new"] == 1
    title, body = h.sent[0]
    assert title == "1 new locked row"
    assert "Phoenix" in body and "YES 39%" in body and "GUARDED" in body
    assert h.locked_written[0]["2026-08-09"] == ["KXLOWTPHX-26AUG09-T91"]
    assert h.written == []                 # the fade state is not touched


def test_an_already_pushed_locked_ticker_stays_quiet():
    h = Harness(locked_state={"2026-08-09": ["KXLOWTPHX-26AUG09-T91"]})
    assert screen_alert.check(_NOW, h.deps())["locked_new"] == 0
    assert h.sent == []


def test_the_fade_state_cannot_suppress_a_locked_push():
    # A bracket can be a fade in the morning and guarded by evening. One shared
    # state file would let the first push silence the second for good.
    h = Harness(state={"2026-08-09": ["KXLOWTPHX-26AUG09-T91"]})
    assert screen_alert.check(_NOW, h.deps())["locked_new"] == 1


def test_a_stale_reference_falls_back_to_locked_rows_only():
    # The guarded rule needs the forecast half; the locked rule needs only
    # observations. Same degradation dead already has.
    stale = dict(_REFERENCE, generated="2026-08-09T00:00:00Z")
    h = Harness()
    deps = h.deps()
    deps.read_reference = lambda: stale
    assert screen_alert.check(_NOW, deps)["locked_new"] == 0


def test_locked_state_is_not_advanced_when_the_push_fails():
    h = Harness(ok=False)
    screen_alert.check(_NOW, h.deps())
    assert h.locked_written == []


def test_a_locked_line_names_the_kind_so_certainty_is_never_implied():
    guarded = {"series": "KXLOWTPHX", "variable": "low", "label": "92° or above",
               "yes_price": 0.39, "reference": 93.0, "margin": 4.0,
               "kind": "guarded"}
    locked = {"series": "KXLOWTPHX", "variable": "low", "label": "83° or below",
              "yes_price": 0.25, "reference": 80.0, "margin": 3.0,
              "kind": "locked"}
    assert screen_alert._locked_line(guarded) == (
        "Phoenix low 92° or above · YES 39% · GUARDED (4° margin)")
    assert screen_alert._locked_line(locked) == (
        "Phoenix low 83° or below · YES 25% · LOCKED (min 80 already)")


def test_locked_title_is_singular_for_one():
    assert screen_alert.locked_title(1) == "1 new locked row"
    assert screen_alert.locked_title(3) == "3 new locked rows"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_alert_locked.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'read_locked_state'`

- [ ] **Step 3: Write the implementation**

Add the two `Deps` fields (after `write_state`):

```python
    read_locked_state: Callable = None
    write_locked_state: Callable = None
```

and wire them in `_real_deps`:

```python
        read_locked_state=lambda: scan_log.read_doc(scan_log.LOCKED_ALERT_STATE_PATH),
        write_locked_state=lambda obj: scan_log.write_doc(
            scan_log.LOCKED_ALERT_STATE_PATH, obj),
```

Add the selection function beside `city_candidates`:

```python
def city_locked(series: str, day: date, markets: list, realized: list,
                now: datetime, remaining) -> list:
    """Alertable YES rows for one city's in-progress climate day.

    `remaining` is None when the reference is stale or missing, which disables
    the guarded rule; the locked one needs only `realized`. Locked wins when
    both would fire -- "cannot lose" is strictly more useful than "the forecast
    is holding it"."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    variable = scan_log.variable_of_series(series)
    bound = screen_rules.realized_extreme(realized, variable)
    out = []
    for market in markets or []:
        row = scan_log.build_snapshot_row(market, series, now)
        if row is None:
            continue
        if screen_forecast.climate_day_of_ticker(row["ticker"]) != day:
            continue
        yes_price = screen_rules.yes_ask_of(market)
        if not screen_rules.within_yes_band(yes_price):
            continue
        # The LIVE price decides, not the firing-time one on the row.
        row = dict(row, yes_ask=yes_price)
        hit = screen_rules.locked_candidate(row, bound, now_iso)
        if hit is None and remaining is not None:
            hit = screen_rules.guarded_candidate(row, bound, remaining, now_iso)
        if hit is None:
            continue
        hit["yes_price"] = yes_price
        out.append(hit)
    return out
```

Add the message builders beside `_line`/`alert_title`:

```python
def locked_title(count: int) -> str:
    return f"{count} new locked row" + ("" if count == 1 else "s")


def _locked_line(c: dict) -> str:
    city = scan_cities.city_name(c.get("series"))
    label = c.get("label") or c.get("ticker")
    price = c.get("yes_price")
    shown = "—" if price is None else f"{round(float(price) * 100)}%"
    if c.get("kind") == "locked":
        word = "max" if c.get("variable") == "high" else "min"
        tail = f"LOCKED ({word} {c.get('reference'):g} already)"
    else:
        tail = f"GUARDED ({c.get('margin'):g}° margin)"
    return f"{city} {c.get('variable')} {label} · YES {shown} · {tail}"


def locked_body(candidates: list, max_lines: int = MAX_BODY_LINES) -> str:
    named = announced(candidates, max_lines)
    lines = [_locked_line(c) for c in named]
    extra = len(candidates) - len(named)
    if extra > 0:
        lines.append(f"…and {extra} more in the next push")
    return "\n".join(lines)
```

In `check`, collect the YES rows in the same city loop. After the existing
`found.extend(...)` call, add:

```python
        remaining = ((info.get("remaining") or {}).get(day.isoformat())
                     if usable else None)
        locked_found.extend(city_locked(series, day, markets,
                                        [t for _, t in readings], now,
                                        remaining))
```

initialising `locked_found = []` beside `found`, and replace the tail of
`check` with a shared dispatcher so both sides advance state identically:

```python
def _dispatch(fresh: list, state: dict, title_fn, body_fn, deps, now,
              write) -> int:
    """Push what is new and record ONLY what the body named. Returns the count
    pushed. Shared so the two sides cannot drift on the rule that matters: state
    advances on a delivered push, and never past what the notification said."""
    if not fresh:
        return 0
    named = announced(fresh)
    if not deps.notify(title_fn(len(named)), body_fn(fresh)):
        print("[screen_alert] send_ntfy False — state not advanced")
        return 0
    write(prune(record(state, named), now.date()))
    return len(named)


def check(now: datetime, deps: Deps) -> dict:
    ...
    fresh = unseen(found, state)
    pushed = _dispatch(fresh, state, alert_title, alert_body, deps, now,
                       deps.write_state)
    locked_state = (deps.read_locked_state() or {}) if deps.read_locked_state else {}
    locked_fresh = unseen(locked_found, locked_state)
    locked_pushed = _dispatch(locked_fresh, locked_state, locked_title,
                              locked_body, deps, now,
                              deps.write_locked_state or (lambda obj: None))
    return {"cities": cities, "found": len(found), "new": len(fresh),
            "pushed": pushed, "locked_found": len(locked_found),
            "locked_new": len(locked_fresh), "locked_pushed": locked_pushed}
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass, including the existing `tests/test_screen_alert_check.py` — the fade path's behaviour is unchanged and `_dispatch` preserves the `announced` rule.

- [ ] **Step 5: Commit**

```bash
git add screen_alert.py tests/test_screen_alert_locked.py
git commit -m "$(cat <<'EOF'
feat(alerts): push new same-day locked and guarded rows

Its own state document, not the fade's: a bracket can be a fade in the morning
and guarded by evening, and one shared file would let the first push silence the
second for good.

The message names the kind, so a guarded row -- which is only as good as the
forecast -- can never read as certainty. A stale reference falls back to locked
rows only, the same degradation dead already has.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The second table

**Files:**
- Modify: `screen_view.py`
- Test: `tests/test_screen_view_locked.py`

**Interfaces:**
- Consumes: `scan_log.LOCKED_PATH`, `screen_rules.yes_ask_of/within_yes_band`, `latest_firing`, `day_of`, `_bracket_label`, `city_of`, `_table`
- Produces: `_LOCKED_COLUMNS`, `_LOCKED_TIPS`, `live_yes_prices(rows, fetch=None) -> dict`,
  `_locked_row(r, live, zones, now=None) -> dict`, `_render_locked(zones) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_view_locked.py
"""The locked table: the YES side of the Screen page."""
from datetime import datetime, timezone

import screen_view

_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)
_ZONES = {"KXLOWTPHX": "America/Phoenix"}


def _row(kind="guarded", price=0.39, margin=4.0):
    return {"ts": "2026-08-10T00:00:00Z", "series": "KXLOWTPHX",
            "variable": "low", "ticker": "KXLOWTPHX-26AUG09-T91",
            "label": "92° or above", "side": "YES", "price": price,
            "reference": 93.0, "margin": margin, "kind": kind,
            "storm": 20, "hours_to_close": 6.7}


def test_a_locked_row_reads_as_a_decision():
    got = screen_view._locked_row(_row(), {"KXLOWTPHX-26AUG09-T91": 0.41},
                                  _ZONES, _NOW)
    assert got["City"] == "Phoenix"
    assert got["Day"] == "Today"
    assert got["Bracket"] == "92° or above"
    assert got["Price"] == "0.39"
    assert got["YES Now"] == "41%"
    assert got["Margin"] == "4.0"
    assert got["Kind"] == "Guarded"
    assert got["Storm"] == "20%"


def test_the_hard_kind_is_named_differently_from_the_soft_one():
    got = screen_view._locked_row(_row(kind="locked"), {}, _ZONES, _NOW)
    assert got["Kind"] == "Locked"


def test_a_row_with_no_live_quote_shows_a_dash_not_a_guess():
    got = screen_view._locked_row(_row(), {}, _ZONES, _NOW)
    assert got["YES Now"] == "—"


def test_rows_the_market_now_agrees_with_are_dropped():
    # Same band as the rules, applied to the live quote.
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(
        rows, {"KXLOWTPHX-26AUG09-T91": 0.95})
    assert visible == [] and dear == 1


def test_rows_the_market_says_we_are_wrong_about_are_dropped():
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(
        rows, {"KXLOWTPHX-26AUG09-T91": 0.04})
    assert visible == [] and cheap == 1


def test_a_row_without_a_live_quote_survives():
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(rows, {})
    assert visible == rows


def test_every_locked_column_has_a_cell():
    got = screen_view._locked_row(_row(), {}, _ZONES, _NOW)
    for column in screen_view._LOCKED_COLUMNS:
        assert column in got


def test_the_locked_columns_are_explained():
    untipped = [c for c in screen_view._LOCKED_COLUMNS
                if c not in screen_view._LOCKED_TIPS]
    assert untipped == ["City", "Var", "Bracket"]


def test_the_locked_table_does_not_inherit_the_fade_tables_meanings():
    # 'Price' there is the YES price of a bracket to FADE; here it is what
    # buying costs. One shared tip map would explain the wrong thing.
    assert "Gap" not in screen_view._LOCKED_COLUMNS
    assert "Str" not in screen_view._LOCKED_COLUMNS


def test_live_yes_prices_fetches_one_ladder_per_series():
    calls = []

    def fetch(series):
        calls.append(series)
        return [{"ticker": "KXLOWTPHX-26AUG09-T91", "yes_ask_dollars": "0.3900"}]

    got = screen_view.live_yes_prices([_row(), _row()], fetch=fetch)
    assert calls == ["KXLOWTPHX"]
    assert got["KXLOWTPHX-26AUG09-T91"] == 0.39
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_screen_view_locked.py -q`
Expected: FAIL — `AttributeError: module 'screen_view' has no attribute '_locked_row'`

- [ ] **Step 3: Write the implementation**

Add to `screen_view.py`, after the consensus block:

```python
# ---- The YES side: brackets the day has already won ------------------------
#
# Its own table and its own tip map. The fade table's Gap and Str measure
# distance from the reference TO the bracket, which is identically zero here --
# the realized extreme is already inside it. Reusing those columns would put a
# meaningless number under a familiar heading.
_LOCKED_COLUMNS = ["City", "Day", "Var", "Bracket", "Price", "YES Now",
                   "Margin", "Kind", "Storm", "Hrs"]

_LOCKED_TIPS = {
    "Day": "Which climate day the bracket is about, in the city's own fixed "
           "standard time. Only 'Today' rows alert.",
    "Price": f"What buying YES cost when the screen last fired, up to "
             f"{int(FIRING_INTERVAL.total_seconds() // 60)} minutes ago. Judge "
             f"the trade on 'YES Now', which is live.",
    "YES Now": "Live cost to buy YES, fetched from Kalshi when this page "
               "loaded. '—' means no live offer.",
    "Margin": "Degrees of protection. For a 'Locked' row, how far past the "
              "strike the realized extreme already settled — it cannot be taken "
              "away. For 'Guarded', how far the forecast still ahead sits from "
              "the only edge that can break the bracket. NOT a probability.",
    "Kind": "'Locked' is physics: the extreme can only move away from this "
            "bracket's open side, so it cannot lose. 'Guarded' is a forecast "
            "bet — the realized extreme is already inside and the remaining "
            "forecast holds it by at least 4°F, but a downdraft can still take "
            "it. Read Storm beside it.",
    "Storm": "Chance of THUNDERSTORMS over the hours that can still move this "
             "extreme. On a 'Guarded' row this is the risk that breaks it — a "
             "convective downdraft is exactly how a low that has already formed "
             "gets undercut before midnight.",
    "Hrs": "Hours until the market closes, which is also the end of its "
           "climate day.",
}


def live_yes_prices(rows: list, fetch=None) -> dict:
    """{ticker: YES ask in dollars} for these rows, priced right now.

    One ladder call per distinct SERIES, reusing the same 60s-cached fetch the
    fade table uses, so showing both tables costs one round of calls, not two."""
    fetch = fetch or _live_markets
    wanted = {r.get("ticker") for r in rows}
    out = {}
    for series in dict.fromkeys(r.get("series") for r in rows):
        if not series:
            continue
        try:
            markets = fetch(series)
        except Exception as e:            # noqa: BLE001 - a page must not crash
            print(f"[screen_view] {series}: live YES price skipped ({e})")
            continue
        for m in markets or []:
            if m.get("ticker") in wanted:
                price = screen_rules.yes_ask_of(m)
                if price is not None:
                    out[m["ticker"]] = price
    return out


def yes_tradeable_now(rows: list, live: dict):
    """(rows worth reviewing, n too expensive, n suspiciously cheap).

    The two counts mean opposite things and are never totalled: above the cap
    the market agrees and there is nothing left to win; below the floor the
    market is saying our REFERENCE is wrong, which is a reason to check the
    station, not to buy."""
    visible, dear, cheap = [], 0, 0
    for r in rows:
        price = live.get(r.get("ticker"))
        price = None if price is None else float(price)
        if price is not None and price > screen_rules.MAX_LIVE_YES_PRICE:
            dear += 1
        elif price is not None and price < screen_rules.MIN_LIVE_YES_PRICE:
            cheap += 1
        else:
            visible.append(r)
    return visible, dear, cheap


def _locked_row(r: dict, live: dict, zones: dict = None, now=None) -> dict:
    price = r.get("price")
    margin = r.get("margin")
    return {
        "City": city_of(r),
        "Day": day_of(r, zones or {}, now),
        "Var": str(r.get("variable") or ""),
        "Bracket": _bracket_label(r),
        "Price": "" if price is None else f"{float(price):.2f}",
        "YES Now": _pct(live.get(r.get("ticker"))),
        "Margin": "—" if margin is None else f"{float(margin):.1f}",
        "Kind": "Locked" if r.get("kind") == "locked" else "Guarded",
        "Storm": storm_of(r),
        "Hrs": r.get("hours_to_close"),
    }


def _render_locked(zones: dict) -> None:
    """The YES table, between the candidates and the consensus board."""
    st.markdown("#### Already Winning — Underpriced YES")
    try:
        rows = latest_firing(scan_log.load_recent(scan_log.LOCKED_PATH, days=3))
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.caption(f"No locked log yet ({e}).")
        return
    live = live_yes_prices(rows) if rows else {}
    rows, dear, cheap = yes_tradeable_now(rows, live)
    if not rows:
        st.caption("Nothing already won and underpriced in the latest firing.")
        return
    st.markdown(_table(_LOCKED_COLUMNS,
                       [_locked_row(r, live, zones) for r in display_rows(rows)],
                       _LOCKED_TIPS),
                unsafe_allow_html=True)
    st.caption("'Locked' cannot lose; 'Guarded' is a forecast bet the Storm "
               "column qualifies. This screen has no settled track record yet — "
               "treat it as observation.")
    # Two captions, never one built by concatenation: the reasons mean opposite
    # things and a single string would have to pick one voice for both.
    if dear:
        st.caption(f"{dear} hidden — the market already agrees, so there is "
                   f"nothing left to win.")
    if cheap:
        st.caption(f"{cheap} hidden — priced under "
                   f"{round(screen_rules.MIN_LIVE_YES_PRICE * 100)}%, which "
                   f"says our reference is wrong, not the price. Worth checking "
                   f"the station rather than buying.")
```

Call it in `render()`, immediately before `_render_board(consensus_doc())`:

```python
    _render_locked(city_timezones())
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass

- [ ] **Step 5: Verify against real data**

```bash
SCAN_GH_REPO=jaredmcelreath-a11y/Weather-Model SCAN_GH_BRANCH=scan-data \
FORECAST_LOG_GH_REPO=jaredmcelreath-a11y/Weather-Model \
  /Users/jared/Library/Python/3.9/bin/streamlit run app.py \
  --server.headless true --server.port 8602 --browser.gatherUsageStats false &
sleep 14
python3 .claude/skills/verify/cdp_shot.py \
  "http://localhost:8602/screen_page" /tmp/locked.png "Already Winning" 8
pkill -f "streamlit run app.py"
```

The locked log will not exist on the branch until the first post-merge pass, so
expect the honest empty caption. Confirm the section renders in the right place
(after the candidates, before the consensus board) and does not disturb either
neighbour.

Then exercise the populated path against live data, which is the real check.
Save this as `/tmp/locked_check.py` and run it:

```python
"""Run the YES rules against live data for a few cities and print the hits."""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

import scan_cities, scan_log, screen, screen_forecast, screen_rules, screen_view
from sources import kalshi
from sources.common import get_json

now = datetime.now(timezone.utc)
for series in ("KXLOWTPHX", "KXHIGHTPHX", "KXLOWTLV", "KXLOWTAUS", "KXHIGHTDAL"):
    point = scan_cities.point_for(series)
    if point is None:
        continue
    resolved = scan_cities.resolve(*point)
    tzname = resolved["timezone"]
    variable = scan_log.variable_of_series(series)
    day = screen_forecast.in_progress_day(now, tzname)
    offset = screen_forecast.lst_offset_hours(tzname)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
        - timedelta(hours=offset)
    station = scan_cities.station_for(resolved["stations_url"])
    temps = [t for _, t in screen.observed_readings(
        screen.fetch_observations(station, start, now), tzname, day)]
    bound = screen_rules.realized_extreme(temps, variable)
    periods = ((get_json(resolved["forecast_hourly"], ttl=900) or {})
               .get("properties") or {}).get("periods") or []
    remaining = screen_forecast.remaining_extreme(periods, day, tzname,
                                                  variable, now)
    print(f"\n{series} {day} realized={bound} remaining={remaining} "
          f"storm={screen_forecast.storm_chance(periods, day, tzname, variable, now)}")
    for market in kalshi.list_series_markets(series, status="open"):
        row = scan_log.build_snapshot_row(market, series, now)
        if row is None or screen_forecast.climate_day_of_ticker(row["ticker"]) != day:
            continue
        row = dict(row, yes_ask=screen_rules.yes_ask_of(market))
        hit = screen_rules.locked_candidate(row, bound, now.isoformat())
        if hit is None:
            hit = screen_rules.guarded_candidate(row, bound, remaining,
                                                 now.isoformat())
        if hit:
            print(f"  HIT {hit['kind']:8} {market.get('yes_sub_title'):<18} "
                  f"YES {hit['price']} margin {hit['margin']}")
```

Confirm every HIT is one you would actually take, and that no `locked` row
appears on a bracket whose extreme could still move away from it — that would
mean the tail-direction test in Task 3 is inverted, which the unit tests should
have caught but this is the check that matters.

- [ ] **Step 6: Commit**

```bash
git add screen_view.py tests/test_screen_view_locked.py
git commit -m "$(cat <<'EOF'
feat(screen): add the underpriced-YES table

Its own columns, because the fade table's Gap and Str are identically zero for a
row whose realized extreme is already inside the bracket. Kind separates physics
from a forecast bet, and the caption says plainly that this screen has no
settled track record yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage.** `locked_candidate` → Task 3; `guarded_candidate` → Task 4; the flat margin bar and `still_open`/`remaining_extreme` → Tasks 1 and 4; the YES band and `yes_ask_of` → Task 2; the separate log and `remaining` in the reference → Task 5; alerts with their own state and the stale fallback → Task 6; the second table with `Kind` labels → Task 7. `screen_score` appears in no task's file list, which is the point.

**The Phoenix regression case appears three times**, at each layer it has to survive: the rule (Task 4), the pass (Task 5), and the alert (Task 6).

**Naming consistency checked:** `locked_candidate(row, bound, now_iso)` and `guarded_candidate(row, bound, remaining, now_iso)` are called with those exact signatures in Tasks 5 and 6. `_yes_candidate` emits `margin`/`reference`/`side`/`kind`, and Tasks 6 and 7 read exactly those keys. `yes_ask_of`/`within_yes_band` are defined in Task 2 and consumed in Tasks 3, 4, 6, 7. `LOCKED_PATH`/`LOCKED_ALERT_STATE_PATH` are defined in Task 5 and consumed in Tasks 6 and 7. The alert's `announced` and `prune`/`record` helpers already exist from the 2026-08-09 overflow fix and are reused unchanged.

**One thing the implementer must watch:** `city_locked` overwrites the row's `yes_ask` with the LIVE quote before calling the rules (`row = dict(row, yes_ask=yes_price)`). Without that line the alert would test a firing-time price that on this path was never set, and every row would fail the band.

**Fixed during this review, worth stating so it does not come back:** the hidden-rows caption originally read `st.caption(A if dear else "" + B)`. Python parses that as `A if dear else ("" + B)`, so whenever `dear` was non-zero the `cheap` explanation vanished silently — the exact class of bug that makes a short table look like a screen that found nothing. It is two separate `st.caption` calls now.
