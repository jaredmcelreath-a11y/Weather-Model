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


def test_a_row_carries_both_forms_because_neither_alone_is_scorable():
    # Unfolded is the honest forecast for a day that has not started. For a day
    # in progress it is NOT: NWS hourly carries about one past hour, so its
    # "low" late in the day is the coldest hour STILL TO COME, while the models
    # cover the whole day. Measured live at Atlanta on 2026-08-09 at 18:00 EDT:
    # NWS low 78.0 against a model consensus of 71.4. Scoring those against each
    # other would hand the consensus a 6.6F win that is an artifact of the NWS
    # feed's window. The folded pair is the like-for-like comparison there.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    high = [r for r in rows if r["variable"] == "high"][0]
    assert high["nws"] == 95.0 and high["cons"] == 92.1
    assert high["nws_folded"] == 96.0 and high["cons_folded"] == 96.0


def test_a_row_says_whether_its_day_was_already_running():
    # Which of the two pairs above the scorer may use.
    rows = city_consensus.log_rows(_DOC, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    assert rows[0]["in_progress"] is True


def test_tomorrows_row_is_not_in_progress():
    doc = {"generated": "2026-08-07T20:00:00Z", "cities": {"DEN": {
        "name": "Denver", "timezone": "America/Denver", "days": {
            "2026-08-08": {"high": {"nws": 97.0, "nws_folded": 97.0,
                                    "cons": 90.0, "cons_folded": 90.0,
                                    "spread": 1.0, "n": 5, "models": {}}}}}}}
    rows = city_consensus.log_rows(doc, datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc))
    assert rows[0]["in_progress"] is False


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
