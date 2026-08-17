"""The unsettled-markets table: which ladders still have something to decide."""
from __future__ import annotations

from datetime import datetime, timezone

import screen_view


def _doc(**series) -> dict:
    return {"generated": "2026-08-16T18:00:00Z", "cities": series}


def _city(tzname, day, price, label="90° to 91°", nxt=("92° to 93°", 0.24)):
    return {"station": "K", "timezone": tzname, "days": {},
            "leader": {day: {"ticker": "T", "label": label, "price": price,
                             "next_label": nxt[0], "next_price": nxt[1]}}}


NOW = datetime(2026, 8, 16, 18, tzinfo=timezone.utc)   # 13:00 LST in Chicago


def test_a_ladder_under_ninety_cents_is_listed():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.55))
    rows = screen_view.unsettled_rows(doc, NOW)
    assert len(rows) == 1
    assert rows[0]["Var"] == "High"
    assert rows[0]["Leader"] == "90° to 91°"
    assert rows[0]["Price"] == "55%"
    assert rows[0]["Runner-up"] == "92° to 93°"
    assert rows[0]["Next"] == "24%"


def test_a_ladder_at_or_over_ninety_cents_is_not():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.94))
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_yesterdays_leader_is_dropped_not_shown_as_today():
    # THE TRAP: the reference is published per firing and survives a climate-day
    # boundary. A leader keyed to a day that is no longer running must vanish,
    # not headline a table titled "still live today".
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-15", 0.55))
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_a_city_with_no_timezone_is_skipped():
    # merge_reference carries identity forward without measurements; a city with
    # no zone is one screen.py could not resolve, and screen_alert skips it too.
    doc = _doc(KXHIGHCHI={"leader": {"2026-08-16": {"price": 0.5}}})
    assert screen_view.unsettled_rows(doc, NOW) == []


def test_rows_are_sorted_by_how_undecided_the_ladder_is():
    doc = _doc(KXHIGHCHI=_city("America/Chicago", "2026-08-16", 0.80),
               KXLOWTCHI=_city("America/Chicago", "2026-08-16", 0.35),
               KXHIGHTATL=_city("America/New_York", "2026-08-16", 0.60))
    rows = screen_view.unsettled_rows(doc, NOW)
    assert [r["Price"] for r in rows] == ["35%", "60%", "80%"]


def test_the_caption_names_the_denominator_and_the_firing_time():
    # "12 rows" alone cannot be read: 12 of 14 is a quiet day, 12 of 40 is not.
    got = screen_view.unsettled_caption(_doc(), shown=12, total=40)
    assert "12" in got and "40" in got


def test_the_caption_survives_a_document_with_no_stamp():
    got = screen_view.unsettled_caption({"cities": {}}, shown=0, total=0)
    assert isinstance(got, str) and got
