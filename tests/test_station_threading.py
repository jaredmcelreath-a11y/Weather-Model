import config
from sources import nws_observations, nws_cli, nws_forecast, open_meteo_models


def test_obs_url_defaults_to_kdfw():
    assert nws_observations.obs_url() == \
        "https://api.weather.gov/stations/KDFW/observations"


def test_obs_url_for_austin():
    assert nws_observations.obs_url("KAUS") == \
        "https://api.weather.gov/stations/KAUS/observations"


def test_cli_list_url_by_station():
    assert nws_cli.list_url() == \
        "https://api.weather.gov/products/types/CLI/locations/DFW"
    assert nws_cli.list_url("KAUS") == \
        "https://api.weather.gov/products/types/CLI/locations/AUS"


def test_nws_forecast_points_url_by_station():
    assert nws_forecast.points_url() == "https://api.weather.gov/points/32.9,-97.04"
    assert nws_forecast.points_url("KAUS") == "https://api.weather.gov/points/30.1975,-97.6664"


def test_snapshot_tags_station(monkeypatch):
    import model
    seen = {}

    def fake_gather(*a, **k):
        seen["station"] = k.get("station")
        return {}, {"obs": ([], []), "obs_continuous_display": (None, None)}, []

    monkeypatch.setattr(model, "gather_series", fake_gather)
    snap = model.snapshot(station="KAUS")
    assert snap["station"] == "KAUS"
    assert seen["station"] == "KAUS"


def test_open_meteo_params_use_station_latlon(monkeypatch):
    captured = {}

    def fake_get_open_meteo(url, params=None, **kw):
        captured["params"] = params
        return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr(open_meteo_models, "get_open_meteo", fake_get_open_meteo)
    open_meteo_models.fetch(2, station="KAUS")
    assert captured["params"]["latitude"] == 30.1975
    assert captured["params"]["longitude"] == -97.6664
    # KDFW default unchanged
    open_meteo_models.fetch(2)
    assert captured["params"]["latitude"] == 32.90
