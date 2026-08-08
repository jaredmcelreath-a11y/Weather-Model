"""screen_alert's selection: same-day only, band-filtered, dead beats forecast."""
from datetime import date, datetime, timezone

import screen_alert

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)
_DAY = date(2026, 8, 7)


def _market(ticker, yes_ask="0.35", no_ask=None):
    """A Denver low bracket paying at 72°F and above ('greater', floor 71)."""
    m = {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": yes_ask,
         "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
         "strike_type": "greater", "volume_fp": "100",
         "close_time": "2026-08-08T05:59:00Z"}
    if no_ask is not None:
        m["no_ask_dollars"] = no_ask
    return m


def _call(markets, realized=(), forecast_extreme=None):
    return screen_alert.city_candidates(
        "KXLOWTDEN", _DAY, markets, list(realized), _NOW, forecast_extreme)


def test_a_forecast_gap_becomes_a_candidate():
    got = _call([_market("KXLOWTDEN-26AUG07-T71")], forecast_extreme=61.0)
    assert [c["kind"] for c in got] == ["forecast"]
    assert got[0]["ticker"] == "KXLOWTDEN-26AUG07-T71"


def test_tomorrows_bracket_is_never_a_candidate():
    # The whole point: brackets closing the next day are not interesting.
    assert _call([_market("KXLOWTDEN-26AUG08-T71")], forecast_extreme=61.0) == []


def test_realized_temperature_makes_it_dead():
    # Two readings of 61 clear MIN_OBS_SUPPORT, so the settled low can only fall
    # further and a 72-and-above bracket is already lost.
    got = _call([_market("KXLOWTDEN-26AUG07-T71")], realized=[61.0, 61.0])
    assert [c["kind"] for c in got] == ["dead"]


def test_dead_wins_when_both_screens_would_fire():
    got = _call([_market("KXLOWTDEN-26AUG07-T71")],
                realized=[61.0, 61.0], forecast_extreme=61.0)
    assert [c["kind"] for c in got] == ["dead"]


def test_no_forecast_extreme_leaves_only_the_dead_screen():
    # This is the stale-reference path: dead needs observations alone.
    assert _call([_market("KXLOWTDEN-26AUG07-T71")], forecast_extreme=None) == []
    got = _call([_market("KXLOWTDEN-26AUG07-T71")],
                realized=[61.0, 61.0], forecast_extreme=None)
    assert [c["kind"] for c in got] == ["dead"]


def test_the_live_no_band_is_applied():
    cheap = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.19")],
                  forecast_extreme=61.0)
    dear = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.91")],
                 forecast_extreme=61.0)
    inside = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.20")],
                   forecast_extreme=61.0)
    assert cheap == [] and dear == []
    assert [c["no_price"] for c in inside] == [0.20]


def test_an_unquoted_market_is_dropped_before_the_band():
    # build_snapshot_row returns None without any quote; nothing to screen.
    assert _call([{"ticker": "KXLOWTDEN-26AUG07-T71"}], forecast_extreme=61.0) == []


def test_in_progress_day_uses_fixed_standard_time():
    # 05:30Z on Aug 8 is 23:30 Mountain STANDARD time on Aug 7, so the Denver
    # climate day still running is the 7th.
    now = datetime(2026, 8, 8, 5, 30, tzinfo=timezone.utc)
    assert screen_alert.in_progress_day(now, "America/Denver") == date(2026, 8, 7)


def test_day_window_starts_at_local_standard_midnight():
    start, end = screen_alert.day_window(_DAY, "America/Denver")
    assert start.isoformat() == "2026-08-07T07:00:00+00:00"   # 00:00 MST
    assert (end - start).total_seconds() == 24 * 3600


def test_reference_age_and_freshness():
    ref = {"generated": "2026-08-07T18:00:00Z"}
    assert screen_alert.reference_age_minutes(ref, _NOW) == 30.0
    assert screen_alert.forecast_is_usable(ref, _NOW) is True
    stale = {"generated": "2026-08-07T16:00:00Z"}      # 150 min
    assert screen_alert.forecast_is_usable(stale, _NOW) is False
    assert screen_alert.forecast_is_usable({}, _NOW) is False
    assert screen_alert.reference_age_minutes({"generated": "nonsense"}, _NOW) is None
