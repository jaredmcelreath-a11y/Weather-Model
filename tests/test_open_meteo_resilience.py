"""Diagnosis + fix for the 'reduced model set — open-meteo models skipped' warning.

Root cause: Open-Meteo rate-limits the deployed app's shared egress IP on the
busy api.open-meteo.com host. #3 = log WHY a source dropped; #1 = route through
the keyed customer endpoint (dedicated quota) when OPEN_METEO_API_KEY is set.
"""
import requests

import model
from sources import common


# --- #3: drop-reason logging ---

def test_drop_reason_includes_http_status():
    resp = requests.Response()
    resp.status_code = 429
    e = requests.exceptions.HTTPError("429 Too Many Requests", response=resp)
    msg = model._drop_reason("open-meteo models", e)
    assert "open-meteo models" in msg and "429" in msg


def test_drop_reason_without_response_uses_exception_type():
    e = requests.exceptions.ConnectTimeout("timed out")
    msg = model._drop_reason("iem mos", e)
    assert "iem mos" in msg and "ConnectTimeout" in msg


# --- #1: keyed customer-endpoint routing ---

def test_get_open_meteo_uses_customer_host_and_apikey(monkeypatch):
    seen = {}
    monkeypatch.setenv("OPEN_METEO_API_KEY", "SECRET")
    monkeypatch.setattr(common, "get_json",
                        lambda url, params=None, **kw: seen.update(url=url, params=params) or {})
    common.get_open_meteo("https://api.open-meteo.com/v1/forecast", {"latitude": 1})
    assert seen["url"] == "https://customer-api.open-meteo.com/v1/forecast"
    assert seen["params"]["apikey"] == "SECRET"
    assert seen["params"]["latitude"] == 1


def test_get_open_meteo_routes_ensemble_and_historical_hosts(monkeypatch):
    seen = []
    monkeypatch.setenv("OPEN_METEO_API_KEY", "K")
    monkeypatch.setattr(common, "get_json",
                        lambda url, params=None, **kw: seen.append(url) or {})
    common.get_open_meteo("https://ensemble-api.open-meteo.com/v1/ensemble", {})
    common.get_open_meteo("https://historical-forecast-api.open-meteo.com/v1/forecast", {})
    assert seen[0] == "https://customer-ensemble-api.open-meteo.com/v1/ensemble"
    assert seen[1] == "https://customer-historical-forecast-api.open-meteo.com/v1/forecast"


def test_get_open_meteo_without_key_is_unchanged(monkeypatch):
    seen = {}
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    monkeypatch.setattr(common, "get_json",
                        lambda url, params=None, **kw: seen.update(url=url, params=params) or {})
    common.get_open_meteo("https://api.open-meteo.com/v1/forecast", {"latitude": 1})
    assert seen["url"] == "https://api.open-meteo.com/v1/forecast"
    assert "apikey" not in (seen["params"] or {})
