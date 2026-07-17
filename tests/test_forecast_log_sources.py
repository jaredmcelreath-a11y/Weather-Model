"""forecast_log._source_means — MOS models logged per-model, not collapsed."""
import forecast_log


def test_source_means_splits_mos_per_model():
    per_source = {
        "guidance": {"mos_lav": (95.0, 72.0), "mos_nbs": (96.0, 73.0)},
        "ensemble": {"ens_a": (90.0, 70.0), "ens_b": (92.0, 72.0)},
    }
    out = forecast_log._source_means(per_source, "high")
    assert out["mos_lav"] == 95.0
    assert out["mos_nbs"] == 96.0
    assert out["ensemble"] == 91.0            # non-MOS group still collapses to mean


def test_source_means_low_variable_and_missing_values():
    per_source = {"guidance": {"mos_nbs": (96.0, 73.0), "mos_lav": (None, None)}}
    out = forecast_log._source_means(per_source, "low")
    assert out["mos_nbs"] == 73.0
    assert "mos_lav" not in out               # no usable value -> omitted
