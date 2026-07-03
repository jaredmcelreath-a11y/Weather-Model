"""Resilience: one slow/dead upstream must not crash the whole dashboard.

Motivated by a 2026-07-02 outage where api.open-meteo.com hung for minutes and
every Streamlit page threw requests.exceptions.ReadTimeout (a single unguarded
source fetch took down the app). get_json now retries transient network errors,
and gather_series drops a source that still fails rather than propagating.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import model
from config import TIMEZONE
from sources import common
from sources import (open_meteo_ensemble, open_meteo_models, nws_forecast,
                     nws_observations, iem_mos)

_TZ = ZoneInfo(TIMEZONE)
DAY = datetime(2026, 7, 2, tzinfo=_TZ).date()


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# get_json retry behavior
# ---------------------------------------------------------------------------

def test_get_json_retries_transient_timeout(monkeypatch, tmp_path):
    """A read timeout that clears on the retry should still return data."""
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)  # no real backoff
    attempts = {"n": 0}

    def flaky_get(url, params=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise requests.exceptions.ReadTimeout("slow")
        return _Resp({"ok": True})

    monkeypatch.setattr(common._session, "get", flaky_get)
    data = common.get_json("https://example.test/x", {"a": 1}, ttl=0)
    assert data == {"ok": True}
    assert attempts["n"] == 2  # one retry rescued the blip (default retries=1)


def test_get_json_circuit_breaker_fast_fails_a_dead_host(monkeypatch, tmp_path):
    """After a host exhausts its retries, further calls to it fail instantly.

    A single outage should cost one timeout, not one per call site — otherwise
    the several open-meteo calls in a snapshot each wait out the full retry.
    """
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_timeout(url, params=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("down")

    monkeypatch.setattr(common._session, "get", always_timeout)

    for _ in range(2):
        try:
            common.get_json("https://dead.test/x", ttl=0)
        except requests.exceptions.RequestException:
            pass
    first_round = calls["n"]  # network attempts for the first (real) call

    # A subsequent call to the SAME host must not touch the network at all.
    before = calls["n"]
    try:
        common.get_json("https://dead.test/y", ttl=0)
    except requests.exceptions.RequestException:
        pass
    assert calls["n"] == before, "breaker should fast-fail without a network call"

    # A different, healthy host is unaffected by the breaker.
    monkeypatch.setattr(common._session, "get",
                        lambda url, params=None, timeout=None: _Resp({"ok": 1}))
    assert common.get_json("https://alive.test/z", ttl=0) == {"ok": 1}
    assert first_round >= 2  # the first call really did retry before tripping


def test_get_json_raises_after_exhausting_retries(monkeypatch, tmp_path):
    """A sustained outage still raises (so gather_series can drop the source)."""
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    def always_timeout(url, params=None, timeout=None):
        raise requests.exceptions.ReadTimeout("down")

    monkeypatch.setattr(common._session, "get", always_timeout)
    try:
        common.get_json("https://example.test/x", ttl=0)
        assert False, "expected ReadTimeout to propagate"
    except requests.exceptions.ReadTimeout:
        pass


# ---------------------------------------------------------------------------
# gather_series graceful degradation
# ---------------------------------------------------------------------------

def _stub_series(label):
    start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=_TZ)
    times = [start + timedelta(hours=h) for h in range(24)]
    temps = [70.0 + h for h in range(24)]
    return {label: (times, temps)}


def _patch_sources(monkeypatch, failing):
    """Point every forecast source at a stub; `failing` names raise ReadTimeout."""
    def make(label):
        def fn(*a, **k):
            if label in failing:
                raise requests.exceptions.ReadTimeout(f"{label} down")
            return _stub_series(label)
        return fn
    monkeypatch.setattr(open_meteo_ensemble, "fetch", make("ens"))
    monkeypatch.setattr(open_meteo_models, "fetch", make("det"))
    monkeypatch.setattr(nws_forecast, "fetch", make("nws"))
    monkeypatch.setattr(iem_mos, "fetch", make("mos"))
    monkeypatch.setattr(nws_observations, "fetch",
                        lambda *a, **k: _stub_series("obs"))


def test_gather_series_drops_a_timed_out_forecast_source(monkeypatch):
    _patch_sources(monkeypatch, failing={"det"})
    series, obs, dropped = model.gather_series(forecast_days=2)
    # Survivors are present; the dead source is absent, not fatal.
    assert "ens" in series and "nws" in series and "mos" in series
    assert "det" not in series
    # dropped carries a human-readable label for the UI warning.
    assert dropped == ["open-meteo models"]
    assert "obs" in obs


def test_gather_series_clean_when_all_sources_healthy(monkeypatch):
    _patch_sources(monkeypatch, failing=set())
    series, obs, dropped = model.gather_series(forecast_days=2)
    assert dropped == []
    assert {"ens", "det", "nws", "mos"} <= set(series)
