"""Calibration-history log: a flattened row per recompute, for the drift view."""
import calibration_history as ch

_CALIB = {
    "computed": "2026-07-18T09:00:00",
    "n_days": 45,
    "bias": {"deterministic": {"high": -0.9, "low": -0.1}},
    "sigma": {"high": 1.6, "low": 1.5},
    "settlement_offset": {"high": 0.89, "low": -0.33},
    "cooling": {"low_offset": 0.2},
    "bias_correction": {"by_lead": {"24": {"high": -0.64, "low": 0.46}},
                        "warm_low": {"threshold": 76, "bias": -0.19}},
}


def test_flatten_extracts_tracked_scalars():
    row = ch.flatten(_CALIB)
    assert row["computed"] == "2026-07-18T09:00:00"
    assert row["n_days"] == 45
    assert row["bias_high"] == -0.9 and row["bias_low"] == -0.1
    assert row["sigma_high"] == 1.6 and row["sigma_low"] == 1.5
    assert row["settle_high"] == 0.89 and row["settle_low"] == -0.33
    assert row["cooling_low"] == 0.2
    assert row["corr_lead24_high"] == -0.64 and row["corr_lead24_low"] == 0.46
    assert row["corr_warm_low"] == -0.19


def test_flatten_missing_pieces_are_none():
    row = ch.flatten({"computed": "x"})
    assert row["bias_high"] is None and row["corr_warm_low"] is None
    assert row["computed"] == "x"


def test_record_appends_new_rows(tmp_path):
    p = str(tmp_path / "h.jsonl")
    ch.record(_CALIB, path=p)
    ch.record(dict(_CALIB, computed="2026-07-19T09:00:00"), path=p)
    assert [r["computed"] for r in ch.load(p)] == \
        ["2026-07-18T09:00:00", "2026-07-19T09:00:00"]


def test_record_dedupes_on_computed_stamp(tmp_path):
    p = str(tmp_path / "h.jsonl")
    ch.record(_CALIB, path=p)
    ch.record(_CALIB, path=p)              # same computed stamp -> no duplicate
    assert len(ch.load(p)) == 1


def test_load_missing_is_empty(tmp_path):
    assert ch.load(str(tmp_path / "none.jsonl")) == []
