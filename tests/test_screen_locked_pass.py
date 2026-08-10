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
    assert "storm" in written[scan_log.LOCKED_PATH][0]


def test_the_reference_publishes_the_forecast_still_ahead():
    # screen_alert cannot recompute forecasts; it re-folds what this publishes.
    written, published = {}, []
    screen.screen_pass(_NOW, _deps(written, published))
    assert published[0]["cities"]["KXLOWTPHX"]["remaining"]["2026-08-09"] == 96.0


def test_the_pass_reports_how_many_locked_rows_it_wrote():
    written, published = {}, []
    got = screen.screen_pass(_NOW, _deps(written, published))
    assert got["locked"] == 1
