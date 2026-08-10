"""Building the published consensus document from a reference plus one fetch."""
from datetime import date, datetime, timezone

import city_consensus

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)   # 13:00 LST in Denver

_REFERENCE = {
    "generated": "2026-08-07T19:55:00Z",
    "cities": {
        "KXHIGHDEN": {"station": "KDEN", "timezone": "America/Denver",
                      "days": {"2026-08-07": 95.0, "2026-08-08": 97.0},
                      "realized": {"2026-08-07": 96.0}},
        "KXLOWTDEN": {"station": "KDEN", "timezone": "America/Denver",
                      "days": {"2026-08-07": 63.0, "2026-08-08": 64.0},
                      "realized": {"2026-08-07": 61.0}},
    },
}


def _ts(y, m, d, hour):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def _payload(highs):
    """One location's hourly block: a noon reading per model on Aug 7."""
    hourly = {"time": [_ts(2026, 8, 7, 19)]}
    for model, value in highs.items():
        hourly[f"temperature_2m_{model}"] = [value]
    return {"hourly": hourly}


_RAW = [_payload({"gfs_seamless": 92.0, "ecmwf_ifs025": 91.5,
                  "icon_seamless": 92.8, "gem_seamless": 92.9,
                  "gfs_hrrr": 91.3})]


def test_cities_come_from_the_reference_one_row_per_city():
    got = city_consensus.cities_from_reference(_REFERENCE)
    assert [c["code"] for c in got] == ["DEN"]          # two series, one city
    city = got[0]
    assert city["name"] == "Denver"
    assert city["series"] == {"high": "KXHIGHDEN", "low": "KXLOWTDEN"}
    assert city["realized"]["2026-08-07"] == {"high": 96.0, "low": 61.0}


def test_a_city_with_no_timezone_is_skipped():
    reference = {"cities": {"KXLOWTDEN": {"station": "KDEN", "days": {}}}}
    assert city_consensus.cities_from_reference(reference) == []


def test_target_days_are_today_and_tomorrow_in_local_standard_time():
    assert city_consensus.target_days(_NOW, "America/Denver") == [
        date(2026, 8, 7), date(2026, 8, 8)]


def test_the_document_carries_both_folded_and_unfolded():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    high = doc["cities"]["DEN"]["days"]["2026-08-07"]["high"]
    assert high["cons"] == 92.1                 # the models' own number
    assert high["cons_folded"] == 96.0          # 96 already happened today
    assert high["nws"] == 95.0
    assert high["nws_folded"] == 96.0
    assert high["spread"] == 1.6 and high["n"] == 5
    assert high["models"]["ecmwf_ifs025"] == 91.5


def test_a_low_folds_downward_not_upward():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    low = doc["cities"]["DEN"]["days"]["2026-08-07"]["low"]
    assert low["nws_folded"] == 61.0            # realized 61 beats forecast 63


def test_tomorrow_has_no_realized_so_folding_is_a_no_op():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    tomorrow = doc["cities"]["DEN"]["days"]["2026-08-08"]
    # No model reading falls on Aug 8 in this payload, so there is no consensus
    # -- but the NWS side is still published, unfolded and folded alike.
    assert tomorrow["high"]["nws"] == 97.0
    assert tomorrow["high"]["nws_folded"] == 97.0
    assert tomorrow["high"]["cons"] is None


def test_the_document_is_stamped_so_the_page_can_age_it():
    doc = city_consensus.build(_REFERENCE, _RAW,
                               city_consensus.cities_from_reference(_REFERENCE),
                               _NOW)
    assert doc["generated"] == "2026-08-07T20:00:00Z"


def test_run_writes_the_document():
    written = {}
    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=lambda coords: _RAW,
        write_doc=lambda path, obj: written.update({path: obj}),
        append_rows=lambda path, rows: len(rows),
    )
    got = city_consensus.run(_NOW, deps)
    assert got["cities"] == 1
    assert city_consensus.CONSENSUS_PATH in written


def test_run_without_a_reference_does_nothing_and_never_raises():
    deps = city_consensus.Deps(
        read_reference=lambda: {},
        fetch=lambda coords: (_ for _ in ()).throw(AssertionError("no fetch")),
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: 0,
    )
    assert city_consensus.run(_NOW, deps)["cities"] == 0


def test_a_failed_fetch_costs_the_pass_nothing_but_the_document():
    def boom(coords):
        raise RuntimeError("429")

    deps = city_consensus.Deps(
        read_reference=lambda: _REFERENCE,
        fetch=boom,
        write_doc=lambda path, obj: None,
        append_rows=lambda path, rows: 0,
    )
    assert city_consensus.run(_NOW, deps)["cities"] == 0
