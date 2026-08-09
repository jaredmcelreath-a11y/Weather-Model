"""screen_alert.check — dedupe, state hygiene, the push, and the stale guard."""
from datetime import date, datetime, timezone

import screen_alert

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)

_REFERENCE = {
    "generated": "2026-08-07T18:20:00Z",
    "cities": {"KXLOWTDEN": {"station": "KDEN", "timezone": "America/Denver",
                             "days": {"2026-08-07": 61.0}}},
}


def _market(ticker="KXLOWTDEN-26AUG07-T71"):
    return {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.35",
            "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
            "strike_type": "greater", "volume_fp": "100",
            "close_time": "2026-08-08T05:59:00Z"}


def _obs(temp_c=16.1):
    return [{"properties": {"timestamp": "2026-08-07T12:00:00+00:00",
                            "temperature": {"value": temp_c}}},
            {"properties": {"timestamp": "2026-08-07T13:00:00+00:00",
                            "temperature": {"value": temp_c}}}]


class Harness:
    def __init__(self, reference=None, state=None, obs=None, ok=True):
        self.reference = _REFERENCE if reference is None else reference
        self.state = state or {}
        self.obs = obs or []
        self.ok = ok
        self.sent = []
        self.written = []

    def deps(self):
        return screen_alert.Deps(
            read_reference=lambda: self.reference,
            read_state=lambda: dict(self.state),
            write_state=lambda obj: self.written.append(obj),
            list_markets=lambda series: [_market()],
            fetch_obs=lambda station, start, end: self.obs,
            notify=lambda title, body: self.sent.append((title, body)) or self.ok,
            sleep=lambda s: None,
        )


def test_a_new_row_pushes_once_and_records_it():
    h = Harness()
    got = screen_alert.check(_NOW, h.deps())
    assert got["new"] == 1
    title, body = h.sent[0]
    assert title == "1 new screen row"
    assert "Denver" in body
    assert h.written[0]["2026-08-07"] == ["KXLOWTDEN-26AUG07-T71"]


def test_an_already_pushed_ticker_stays_quiet():
    h = Harness(state={"2026-08-07": ["KXLOWTDEN-26AUG07-T71"]})
    got = screen_alert.check(_NOW, h.deps())
    assert got["new"] == 0
    assert h.sent == []
    # A quiet check must not write: it would be a commit every five minutes.
    assert h.written == []


def test_state_is_not_advanced_when_the_push_fails():
    # Otherwise a failed ntfy POST loses the row forever.
    h = Harness(ok=False)
    screen_alert.check(_NOW, h.deps())
    assert h.written == []


def test_a_stale_reference_silences_the_forecast_screen():
    stale = dict(_REFERENCE, generated="2026-08-07T15:00:00Z")   # 210 min
    h = Harness(reference=stale)
    assert screen_alert.check(_NOW, h.deps())["new"] == 0
    assert h.sent == []


def test_a_stale_reference_still_allows_a_dead_row():
    stale = dict(_REFERENCE, generated="2026-08-07T15:00:00Z")
    h = Harness(reference=stale, obs=_obs())      # 16.1C = 61F, twice
    assert screen_alert.check(_NOW, h.deps())["new"] == 1
    assert "DEAD" in h.sent[0][1]


def test_a_missing_reference_checks_nothing_and_never_raises():
    h = Harness(reference={})
    assert screen_alert.check(_NOW, h.deps())["new"] == 0


def test_unseen_ignores_duplicate_tickers_in_one_pass():
    rows = [{"ticker": "KXLOWTDEN-26AUG07-T71"},
            {"ticker": "KXLOWTDEN-26AUG07-T71"}]
    assert len(screen_alert.unseen(rows, {})) == 1


def test_record_then_unseen_suppresses_a_re_entry():
    # A bracket oscillating around the 20% floor must not become a stream.
    rows = [{"ticker": "KXLOWTDEN-26AUG07-T71"}]
    state = screen_alert.record({}, rows)
    assert screen_alert.unseen(rows, state) == []


def test_prune_keeps_two_days():
    state = {"2026-08-04": ["a"], "2026-08-05": ["b"],
             "2026-08-06": ["c"], "2026-08-07": ["d"]}
    kept = screen_alert.prune(state, date(2026, 8, 7))
    assert sorted(kept) == ["2026-08-05", "2026-08-06", "2026-08-07"]


def test_prune_drops_an_unparseable_key():
    assert screen_alert.prune({"junk": ["a"]}, date(2026, 8, 7)) == {}


def test_alert_title_is_singular_for_one():
    assert screen_alert.alert_title(1) == "1 new screen row"
    assert screen_alert.alert_title(3) == "3 new screen rows"


def test_alert_body_lines_read_as_a_decision():
    forecast_row = {"series": "KXLOWTDEN", "variable": "low",
                    "label": "72° or above", "no_price": 0.35,
                    "forecast": 61.0, "gap": 11.0, "kind": "forecast"}
    dead_row = {"series": "KXHIGHMIA", "variable": "high",
                "label": "91° to 92°", "no_price": 0.22,
                "forecast": 94.0, "gap": 2.0, "kind": "dead"}
    body = screen_alert.alert_body([forecast_row, dead_row]).splitlines()
    assert body[0] == "Denver low 72° or above · NO 35% · Ref 61 (11° gap)"
    assert body[1] == "Miami high 91° to 92° · NO 22% · DEAD (max 94 already)"


def test_alert_body_caps_its_length():
    rows = [{"series": "KXLOWTDEN", "variable": "low", "label": f"{i}",
             "no_price": 0.5, "forecast": 61.0, "gap": 9.0, "kind": "forecast"}
            for i in range(14)]
    lines = screen_alert.alert_body(rows).splitlines()
    assert len(lines) == 11
    assert lines[-1] == "…and 4 more in the next push"


def test_alert_body_shows_an_unquoted_row_honestly():
    row = {"series": "KXLOWTDEN", "variable": "low", "label": "72° or above",
           "no_price": None, "forecast": 61.0, "gap": 11.0, "kind": "forecast"}
    assert "NO — ·" in screen_alert.alert_body([row])


def test_main_rejects_an_unknown_command():
    assert screen_alert.main([]) == 2


# ---- The overflow must not be recorded as delivered ------------------------
#
# check() used to mark every fresh ticker as pushed while alert_body named only
# the first MAX_BODY_LINES, so anything past the cap was silently marked
# delivered and never named again. Harmless while same-day batches stay small
# (measured max 6), fatal the moment the scope widens.

def _many(n):
    return [{"series": "KXLOWTDEN", "variable": "low", "label": f"{i}",
             "ticker": f"KXLOWTDEN-26AUG07-T{i}", "no_price": 0.5,
             "forecast": 61.0, "gap": 9.0, "kind": "forecast"} for i in range(n)]


def test_announced_is_what_the_body_names():
    rows = _many(14)
    named = screen_alert.announced(rows)
    assert len(named) == screen_alert.MAX_BODY_LINES
    assert named == rows[:screen_alert.MAX_BODY_LINES]


def test_announced_returns_everything_under_the_cap():
    rows = _many(3)
    assert screen_alert.announced(rows) == rows


def test_the_overflow_line_promises_the_next_push_not_a_silent_drop():
    lines = screen_alert.alert_body(_many(14)).splitlines()
    assert len(lines) == screen_alert.MAX_BODY_LINES + 1
    assert lines[-1] == "…and 4 more in the next push"


class _Overflow(Harness):
    """A city whose whole same-day ladder fires at once."""

    def deps(self):
        d = super().deps()
        d.list_markets = lambda series: [
            dict(_market(), ticker=f"KXLOWTDEN-26AUG07-T{71 + i}",
                 floor_strike=71 + i, yes_sub_title=f"{72 + i}° or above")
            for i in range(14)]
        return d


def test_only_the_named_rows_are_recorded_as_pushed():
    h = _Overflow()
    got = screen_alert.check(_NOW, h.deps())
    assert got["new"] == 14                       # all 14 are genuinely new
    assert got["pushed"] == screen_alert.MAX_BODY_LINES
    title, _ = h.sent[0]
    assert title == "10 new screen rows"          # the title counts what it names
    assert h.written[0]["2026-08-07"] == sorted(
        f"KXLOWTDEN-26AUG07-T{71 + i}" for i in range(screen_alert.MAX_BODY_LINES))


def test_the_overflow_is_pushed_by_the_following_pass():
    h = _Overflow()
    screen_alert.check(_NOW, h.deps())
    h.state = h.written[0]                        # the next pass reads it back
    screen_alert.check(_NOW, h.deps())
    assert h.sent[1][0] == "4 new screen rows"
    assert len(h.written[1]["2026-08-07"]) == 14
