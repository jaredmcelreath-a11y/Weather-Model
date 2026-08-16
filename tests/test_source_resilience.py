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
    """Once a host is declared dead, further calls to it fail instantly.

    An outage should cost a BOUNDED number of timeouts, not one per call site —
    otherwise the several open-meteo calls in a snapshot each wait out the full
    retry. The bound is _FAILURES_TO_TRIP consecutive failures rather than one,
    so that a single slow response no longer condemns the host; see the breaker
    comment in sources/common.
    """
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_timeout(url, params=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("down")

    monkeypatch.setattr(common._session, "get", always_timeout)

    for _ in range(common._FAILURES_TO_TRIP):
        try:
            common.get_json("https://dead.test/x", ttl=0)
        except requests.exceptions.RequestException:
            pass
    first_round = calls["n"]  # network attempts before the breaker gave up

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


# ---------------------------------------------------------------------------
# Circuit breaker: one slow response is not an outage
# ---------------------------------------------------------------------------
#
# The breaker used to trip on the FIRST exhausted call, which cannot tell a
# single slow response from a dead host. screen.py hits api.weather.gov ~3x per
# city across 40 cities inside the 60s cooldown, so one timeout cost 38 of them
# (observed live 2026-08-16: {'cities': 2, 'errors': 38}). Consecutive failures
# are the discriminator -- a dead host never succeeds in between.

def _timeouts_then_ok(calls, fail_first):
    def get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise requests.exceptions.ReadTimeout("slow")
        return _Resp({"ok": True})
    return get


def test_one_exhausted_call_does_not_trip_the_breaker(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    calls = {"n": 0}
    # 2 attempts (retries=1) fail, then the host answers normally again.
    monkeypatch.setattr(common._session, "get", _timeouts_then_ok(calls, 2))

    try:
        common.get_json("https://blip.test/a", ttl=0)
    except requests.exceptions.RequestException:
        pass
    # The very next call must REACH THE NETWORK rather than be refused.
    assert common.get_json("https://blip.test/b", ttl=0) == {"ok": True}


def test_a_success_between_failures_resets_the_count(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    state = {"fail": True, "n": 0}

    def alternating(url, params=None, timeout=None):
        state["n"] += 1
        if state["fail"]:
            raise requests.exceptions.ReadTimeout("slow")
        return _Resp({"ok": True})

    monkeypatch.setattr(common._session, "get", alternating)
    # Fail, succeed, fail, succeed, fail -- never three in a row, so the host
    # is never declared dead. This is the screen loop's actual shape.
    for _ in range(3):
        state["fail"] = True
        try:
            common.get_json("https://flaky.test/x", ttl=0)
        except requests.exceptions.RequestException:
            pass
        state["fail"] = False
        assert common.get_json("https://flaky.test/y", ttl=0) == {"ok": True}


def test_a_genuinely_dead_host_still_trips_and_fast_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_FAILED_HOSTS", {})
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_timeout(url, params=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("down")

    monkeypatch.setattr(common._session, "get", always_timeout)
    for _ in range(common._FAILURES_TO_TRIP):
        try:
            common.get_json("https://dead.test/x", ttl=0)
        except requests.exceptions.RequestException:
            pass
    before = calls["n"]
    try:
        common.get_json("https://dead.test/y", ttl=0)
    except requests.exceptions.RequestException:
        pass
    assert calls["n"] == before          # refused without touching the network
