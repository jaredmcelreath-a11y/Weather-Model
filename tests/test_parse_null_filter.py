"""Both Open-Meteo parsers drop null temperatures per-series (flaky candidate)."""
from sources import open_meteo_models, open_meteo_ensemble


def test_models_parse_drops_null_temps_per_series():
    data = {"hourly": {
        "time": ["2026-07-18T00:00", "2026-07-18T01:00", "2026-07-18T02:00"],
        "temperature_2m_gfs_seamless": [70.0, None, 72.0],
        "temperature_2m_jma_seamless": [None, None, None],
    }}
    out = open_meteo_models._parse(data)
    gfs_times, gfs_temps = out["det_gfs_seamless"]
    assert gfs_temps == [70.0, 72.0]
    assert len(gfs_times) == 2  # times filtered alongside values
    # An all-null series yields empty lists, not a series full of None.
    assert out["det_jma_seamless"] == ([], [])


def test_ensemble_parse_drops_null_temps_per_series():
    data = {"hourly": {
        "time": ["2026-07-18T00:00", "2026-07-18T01:00"],
        "temperature_2m_member01_ukmo_global_ensemble_20km": [80.0, None],
    }}
    out = open_meteo_ensemble._parse(data)
    times, temps = out["ens_member01_ukmo_global_ensemble_20km"]
    assert temps == [80.0]
    assert len(times) == 1
