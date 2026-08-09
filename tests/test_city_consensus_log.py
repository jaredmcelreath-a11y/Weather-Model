"""The hourly forecast log that later answers 'is the consensus better?'."""
from datetime import datetime, timezone

import city_consensus

_DOC = {
    "generated": "2026-08-07T20:00:00Z",
    "cities": {"DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
        "2026-08-07": {
            "high": {"nws": 95.0, "nws_folded": 96.0, "cons": 92.1,
                     "cons_folded": 96.0, "spread": 1.6, "n": 5,
                     "models": {"gfs_seamless": 92.0}},
            "low": {"nws": 63.0, "nws_folded": 61.0, "cons": None,
                    "cons_folded": None, "spread": None, "n": 0, "models": {}},
        }}}},
}


def test_the_log_fires_once_an_hour_not_once_a_pass():
    # Dispatches land at :00 and :30; 80 rows every pass would be 3,840/day into
    # a file append_many rewrites whole each time.
    assert city_consensus.should_log(datetime(2026, 8, 7, 20, 1, tzinfo=timezone.utc))
    assert not city_consensus.should_log(datetime(2026, 8, 7, 20, 31, tzinfo=timezone.utc))


def test_a_row_carries_the_unfolded_forecast_not_the_folded_one():
    # The scorer grades what the forecast SAID, not what had already happened.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["nws"] == 95.0 and high["cons"] == 92.1
    assert "nws_folded" not in high and "cons_folded" not in high


def test_a_row_identifies_its_city_day_and_variable():
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["city"] == "DEN"
    assert high["day"] == "2026-08-07"
    assert high["ts"] == "2026-08-07T20:00:00Z"


def test_per_model_values_are_kept():
    # 60 bytes, and the only way to later discover ECMWF alone wins in Denver.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["models"] == {"gfs_seamless": 92.0}


def test_a_variable_with_no_consensus_is_not_logged():
    # Nothing to score, and a row of Nones would dilute any later hit rate.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    assert [r["variable"] for r in rows] == ["high"]


def test_run_logs_on_the_hour_and_stays_quiet_off_it():
    _REFERENCE = {"cities": {"KXHIGHDEN": {"timezone": "America/Denver",
                                           "days": {}, "realized": {}},
                             "KXLOWTDEN": {"timezone": "America/Denver",
                                           "days": {}, "realized": {}}}}
    appended = []
    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=lambda coords: [{"hourly": {"time": []}}],
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: appended.append(rows) or len(rows),
    )
    off_hour = datetime(2026, 8, 7, 20, 45, tzinfo=timezone.utc)
    assert city_consensus.run(off_hour, deps)["logged"] == 0
    assert appended == []
