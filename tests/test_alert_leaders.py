"""screen_alert publishes the ladder leaders, on its own 5-minute cadence."""
from __future__ import annotations

from datetime import datetime, timezone

import screen_alert

NOW = datetime(2026, 8, 16, 18, tzinfo=timezone.utc)   # 13:00 LST in Chicago


def _market(ticker, ask, sub="90° to 91°"):
    return {"ticker": ticker, "yes_ask_dollars": ask, "yes_bid_dollars": ask,
            "yes_sub_title": sub, "strike_type": "between",
            "floor_strike": 90, "cap_strike": 91,
            "close_time": "2026-08-17T05:59:00Z", "volume_fp": "10"}


def _deps(markets, written):
    return screen_alert.Deps(
        read_reference=lambda: {
            "generated": "2026-08-16T17:30:00Z",
            "cities": {"KXHIGHCHI": {"station": None,
                                     "timezone": "America/Chicago",
                                     "days": {}}}},
        read_state=lambda: {},
        write_state=lambda obj: None,
        list_markets=lambda series: markets,
        fetch_obs=lambda station, start, end: [],
        notify=lambda *a, **k: True,
        sleep=lambda s: None,
        read_locked_state=lambda: {},
        write_locked_state=lambda obj: None,
        write_leaders=lambda obj: written.append(obj),
    )


def test_the_alert_pass_publishes_a_leader_per_series():
    # The whole point of moving this off screen.py: these ladders are already
    # fetched here every 5 minutes, so the leader costs no extra request and is
    # six times fresher than the 30-minute firing.
    written = []
    screen_alert.check(NOW, _deps([_market("KXHIGHCHI-26AUG16-B90.5", "0.5500"),
                                   _market("KXHIGHCHI-26AUG16-B92.5", "0.2400",
                                           "92° to 93°")], written))
    assert len(written) == 1
    doc = written[0]
    assert doc["generated"].startswith("2026-08-16T18:00")
    city = doc["cities"]["KXHIGHCHI"]
    assert city["timezone"] == "America/Chicago"
    leader = city["leader"]["2026-08-16"]
    assert leader["price"] == 0.55
    assert leader["next_price"] == 0.24


def test_the_published_shape_is_what_the_page_already_reads():
    # unsettled_rows walks cities[series]['leader'][day] and needs a timezone,
    # exactly as it did against the reference. Keeping the shape identical means
    # the page and its tests did not have to change when the writer moved.
    written = []
    screen_alert.check(NOW, _deps([_market("KXHIGHCHI-26AUG16-B90.5", "0.5500")],
                                  written))
    import screen_view
    rows = screen_view.unsettled_rows(written[0], NOW)
    assert len(rows) == 1
    assert rows[0]["City"] == "Chicago"
    assert rows[0]["Price"] == "55%"


def test_a_series_with_no_quoted_ladder_is_simply_absent():
    written = []
    screen_alert.check(NOW, _deps([], written))
    assert written[0]["cities"]["KXHIGHCHI"].get("leader") in (None, {})


def test_a_failed_publish_does_not_cost_the_pass_its_pushes():
    # The alert's job is notifications; this document is a passenger. A contents
    # API failure here must not take the pass down -- the same rule screen.py
    # applies to its own reference publish.
    def boom(obj):
        raise RuntimeError("403 rate limited")

    deps = _deps([_market("KXHIGHCHI-26AUG16-B90.5", "0.5500")], [])
    deps.write_leaders = boom
    got = screen_alert.check(NOW, deps)          # must not raise
    assert got["cities"] == 1
