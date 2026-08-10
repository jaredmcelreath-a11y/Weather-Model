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
