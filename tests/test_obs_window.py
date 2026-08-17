"""The windowed observation fetch: one page when it fits, paged when it does not."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sources import nws_observations


def _props(stamp: str, temp=20.0) -> dict:
    return {"timestamp": stamp, "temperature": {"value": temp}}


def _page(stamps: list) -> dict:
    return {"features": [{"properties": _props(s)} for s in stamps]}


class _Recorder:
    """A get_json stand-in that hands back canned pages and records the params."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, url, params=None, ttl=None):
        self.calls.append(dict(params or {}))
        return self.pages.pop(0) if self.pages else {"features": []}


START = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
END = datetime(2026, 8, 17, 0, tzinfo=timezone.utc)


def test_a_short_page_costs_exactly_one_request():
    # Under the limit means the feed gave us everything in the window; asking
    # again would be a wasted call against a host that trips a 60s cooldown
    # after three failures.
    fetch = _Recorder([_page(["2026-08-16T23:25:00+00:00",
                              "2026-08-16T23:20:00+00:00"])])
    got = nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 1
    assert [r["timestamp"] for r in got] == ["2026-08-16T23:25:00+00:00",
                                             "2026-08-16T23:20:00+00:00"]


def test_a_full_page_is_paged_and_the_boundary_row_is_not_duplicated():
    # THE TRAP: the endpoint truncates at `limit` silently. A full page means
    # "possibly more", so we re-ask ending at the oldest row we have -- and that
    # row comes back again, which would double-count without the dedup.
    #
    # One-minute spacing, deliberately: 500 rows five minutes apart span 41.7h,
    # which overruns this 36h window, and the "reached the far edge" branch
    # would end paging before the dedup branch was ever reached.
    first = [(END - timedelta(minutes=i)).isoformat()
             for i in range(nws_observations._WINDOW_LIMIT)]
    oldest = first[-1]
    second = [oldest, (END - timedelta(minutes=500)).isoformat()]
    fetch = _Recorder([_page(first), _page(second)])
    got = nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 2
    # The cursor is the oldest row we already hold, sent in the form the API
    # wants: it states offsets as 'Z', not '+00:00'.
    assert fetch.calls[1]["end"] == oldest.replace("+00:00", "Z")
    stamps = [r["timestamp"] for r in got]
    assert len(stamps) == len(set(stamps))            # no duplicate boundary row
    assert len(stamps) == nws_observations._WINDOW_LIMIT + 1


def test_paging_stops_once_the_window_is_covered():
    # A full page whose oldest row is already at or before `start` has reached
    # the far edge of the window -- there is nothing older to ask for.
    stamps = [(START + timedelta(minutes=5 * i)).isoformat()
              for i in range(nws_observations._WINDOW_LIMIT)][::-1]
    fetch = _Recorder([_page(stamps)])
    nws_observations.window_for_id("KLAS", START, END, fetch=fetch)
    assert len(fetch.calls) == 1


def test_a_dead_feed_returns_no_rows_rather_than_raising():
    # A page must not crash because one station is down; the caller shows an
    # empty table with a notice.
    def boom(url, params=None, ttl=None):
        raise RuntimeError("upstream down")

    assert nws_observations.window_for_id("KLAS", START, END, fetch=boom) == []
