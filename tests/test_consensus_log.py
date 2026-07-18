"""Regression test for the consensus-history sampling cadence.

Bug: the chart showed doubled gaps between dots even though the logging Action
runs on a fixed cadence. Root cause: MIN_INTERVAL_MIN equalled the cron cadence,
so any run arriving even seconds under the interval after the last sample (normal
GitHub Actions startup jitter) was throttled out — and dropping one reset the
clock, locking the series into a doubled rhythm. The throttle must sit comfortably
below the cron cadence so every scheduled run is recorded. The cron now runs
every 10 min and MIN_INTERVAL_MIN is 7, so a run arriving ~3 min early is kept."""

from datetime import datetime, timedelta

import consensus_log


def _snap(when: datetime) -> dict:
    return {
        "updated": when.isoformat(timespec="seconds"),
        "current": {"temp": 88.0},
        "today": {"day": when.date().isoformat(),
                  "high": {"consensus": 96.0}, "low": {"consensus": 80.0}},
    }


def _high_today_count(path) -> int:
    rows = consensus_log.load(str(path))
    return sum(1 for r in rows if r["variable"] == "high")


def test_ten_minute_runs_are_all_recorded(tmp_path):
    # Six consecutive cron runs ~10 min apart, with realistic ±sub-minute jitter
    # that shaves a little off some intervals (down toward ~9 min) — exactly what
    # dropped every other point in production when the throttle sat at the cadence.
    path = tmp_path / "consensus_history.jsonl"
    base = datetime(2026, 6, 29, 11, 1, 0)
    offsets = [0, 9.8, 20.1, 29.6, 40.2, 49.5]  # minutes from base; deltas ~9.3-10.6
    for off in offsets:
        consensus_log.record(_snap(base + timedelta(minutes=off)), path=str(path))

    # Every scheduled run should produce a point — no doubled-spacing collapse.
    assert _high_today_count(path) == len(offsets)


def test_throttle_still_blocks_subminute_refreshes(tmp_path):
    # An always-open local dashboard refreshing every minute must not flood it.
    path = tmp_path / "consensus_history.jsonl"
    base = datetime(2026, 6, 29, 11, 1, 0)
    for off in (0, 1, 2, 3):
        consensus_log.record(_snap(base + timedelta(minutes=off)), path=str(path))

    assert _high_today_count(path) == 1
