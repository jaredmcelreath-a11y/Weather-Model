"""Deterministic-models fallback: when api.open-meteo.com throttles the app's
shared IP, read the copy the (unthrottled) GitHub Action published to the data
branch instead of dropping the models from the consensus.
"""
import json

import requests

from sources import open_meteo_models as omm

# A canned raw Open-Meteo response (same shape _parse consumes).
_RAW = {"hourly": {
    "time": ["2026-07-24T00:00", "2026-07-24T01:00"],
    "temperature_2m_gfs_seamless": [80.0, 79.0],
    "temperature_2m_ecmwf_ifs025": [81.0, 80.0],
}}


def test_fetch_falls_back_to_published_when_throttled(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("429-ish")
    monkeypatch.setattr(omm, "_fetch_live_raw", boom)
    monkeypatch.setattr(omm, "_load_published_raw", lambda: _RAW)
    out = omm.fetch(forecast_days=3)
    assert set(out.keys()) == {"det_gfs_seamless", "det_ecmwf_ifs025"}
    assert out["det_gfs_seamless"][1] == [80.0, 79.0]


def test_fetch_reraises_when_live_and_published_both_fail(monkeypatch):
    monkeypatch.setattr(omm, "_fetch_live_raw",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.Timeout()))
    def no_pub():
        raise FileNotFoundError("no published copy")
    monkeypatch.setattr(omm, "_load_published_raw", no_pub)
    try:
        omm.fetch(forecast_days=3)
        assert False, "expected the original network error to propagate"
    except requests.exceptions.RequestException:
        pass


def test_custom_model_set_does_not_use_fallback(monkeypatch):
    # The published copy is the PRODUCTION model set; a shadow/candidate override
    # must not silently get production data — it should just raise.
    monkeypatch.setattr(omm, "_fetch_live_raw",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    called = {"pub": False}
    monkeypatch.setattr(omm, "_load_published_raw",
                        lambda: called.__setitem__("pub", True) or _RAW)
    try:
        omm.fetch(forecast_days=3, models=["gfs_seamless"])
        assert False, "expected raise for a custom model set"
    except requests.exceptions.RequestException:
        pass
    assert called["pub"] is False


def test_write_published_dumps_raw_response(tmp_path, monkeypatch):
    monkeypatch.setattr(omm, "_fetch_live_raw", lambda *a, **k: _RAW)
    path = tmp_path / "det_models.json"
    omm.write_published(str(path))
    assert json.loads(path.read_text()) == _RAW
