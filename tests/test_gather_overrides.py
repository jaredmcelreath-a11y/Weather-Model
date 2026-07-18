"""gather_series passes model-list overrides; default path is production."""
import config
import model
from sources import open_meteo_models, open_meteo_ensemble


def test_fetch_defaults_to_production_models(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["models"] = params["models"]
        return {"hourly": {"time": ["2026-07-18T00:00"],
                           "temperature_2m_gfs_seamless": [70.0]}}
    monkeypatch.setattr(open_meteo_models, "get_json", fake_get_json)
    open_meteo_models.fetch()
    assert seen["models"] == ",".join(config.DETERMINISTIC_MODELS)


def test_fetch_uses_override_models(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["models"] = params["models"]
        return {"hourly": {"time": ["2026-07-18T00:00"],
                           "temperature_2m_ukmo_seamless": [71.0]}}
    monkeypatch.setattr(open_meteo_models, "get_json", fake_get_json)
    open_meteo_models.fetch(models=config.CANDIDATE_DETERMINISTIC_MODELS)
    assert seen["models"] == ",".join(config.CANDIDATE_DETERMINISTIC_MODELS)


def test_gather_series_routes_overrides(monkeypatch):
    calls = {}

    def fake_det(forecast_days=2, models=None):
        calls["det"] = models
        return {}

    def fake_ens(forecast_days=2, models=None):
        calls["ens"] = models
        return {}
    monkeypatch.setattr(model.open_meteo_models, "fetch", fake_det)
    monkeypatch.setattr(model.open_meteo_ensemble, "fetch", fake_ens)
    monkeypatch.setattr(model.nws_forecast, "fetch", lambda: {})
    monkeypatch.setattr(model.iem_mos, "fetch", lambda forecast_days=2: {})
    monkeypatch.setattr(model.nws_observations, "fetch",
                        lambda continuous=True, now=None: {"obs": ([], [])})

    model.gather_series(det_models=config.CANDIDATE_DETERMINISTIC_MODELS,
                        ens_models=config.CANDIDATE_ENSEMBLE_MODELS)
    assert calls["det"] == config.CANDIDATE_DETERMINISTIC_MODELS
    assert calls["ens"] == config.CANDIDATE_ENSEMBLE_MODELS

    model.gather_series()  # production defaults => None passed through
    assert calls["det"] is None
    assert calls["ens"] is None
