from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
import settlement
from config import TIMEZONE
from sources import nws_observations

_TZ = ZoneInfo(TIMEZONE)


def test_fetch_accepts_an_explicit_window_start(monkeypatch):
    seen = {}

    def fake_get_json(url, params, ttl=None):
        seen["start"] = params["start"]
        seen["limit"] = params["limit"]
        return {"features": []}

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    monkeypatch.setattr(nws_observations, "_iem_fallback", lambda s, n: ([], []))

    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    start = datetime(2026, 7, 19, 1, 0, tzinfo=_TZ)
    nws_observations.fetch(now=now, start=start)
    assert seen["start"] == start.isoformat()


def test_fetch_defaults_to_clock_midnight(monkeypatch):
    seen = {}

    def fake_get_json(url, params, ttl=None):
        seen["start"] = params["start"]
        return {"features": []}

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    monkeypatch.setattr(nws_observations, "_iem_fallback", lambda s, n: ([], []))

    now = datetime(2026, 7, 20, 15, 0, tzinfo=_TZ)
    nws_observations.fetch(now=now)
    assert seen["start"] == datetime(2026, 7, 20, 0, 0, tzinfo=_TZ).isoformat()


def test_cli_daily_fetches_a_range(monkeypatch):
    seen = {}

    def fake_fetch_actual_cli(start, end, ttl=None):
        seen["range"] = (start, end)
        return {start: (99.0, 79.0)}

    monkeypatch.setattr(model, "fetch_actual_cli", fake_fetch_actual_cli)
    model._fetch_cli_daily(date(2026, 7, 19), date(2026, 7, 20))
    assert seen["range"] == (date(2026, 7, 19), date(2026, 7, 20))


def test_cli_daily_single_day_unchanged(monkeypatch):
    seen = {}

    def fake_fetch_actual_cli(start, end, ttl=None):
        seen["range"] = (start, end)
        return {}

    monkeypatch.setattr(model, "fetch_actual_cli", fake_fetch_actual_cli)
    model._fetch_cli_daily(date(2026, 7, 20))
    assert seen["range"] == (date(2026, 7, 20), date(2026, 7, 20))


def test_gather_series_extends_the_window_in_the_final_hour(monkeypatch):
    seen = {}

    def fake_obs_fetch(limit=500, continuous=False, now=None, start=None):
        seen["start"] = start
        seen["limit"] = limit
        return {"obs": ([], []), "obs_continuous": (None, None)}

    monkeypatch.setattr(model.nws_observations, "fetch", fake_obs_fetch)
    monkeypatch.setattr(model, "_fetch_cli_daily", lambda d, t=None: {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    model.gather_series(now=now, continuous_obs=True)
    # Window starts at the PRIOR climate day's start (01:00 CDT July 19).
    assert seen["start"] == settlement.local_day_bounds(date(2026, 7, 19))[0]
    # 500 is the API's hard maximum (limit=501 returns 400), and it covers the
    # extended ~25h window with room to spare — never raise it to compensate.
    assert seen["limit"] == 500


def test_gather_series_normal_window_unchanged(monkeypatch):
    seen = {}

    def fake_obs_fetch(limit=500, continuous=False, now=None, start=None):
        seen["start"] = start
        seen["limit"] = limit
        return {"obs": ([], []), "obs_continuous": (None, None)}

    monkeypatch.setattr(model.nws_observations, "fetch", fake_obs_fetch)
    monkeypatch.setattr(model, "_fetch_cli_daily", lambda d, t=None: {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    model.gather_series(now=datetime(2026, 7, 20, 15, 0, tzinfo=_TZ), continuous_obs=True)
    assert seen["start"] is None      # default clock-midnight path
    assert seen["limit"] == 500


def test_gather_series_cli_daily_covers_both_days_in_the_final_hour(monkeypatch):
    seen = {}
    monkeypatch.setattr(model.nws_observations, "fetch",
                        lambda **k: {"obs": ([], []), "obs_continuous": (None, None)})
    monkeypatch.setattr(model, "_fetch_cli_daily",
                        lambda d, t=None: seen.update(range=(d, t)) or {})
    for src in ("open_meteo_ensemble", "open_meteo_models", "nws_forecast", "iem_mos"):
        monkeypatch.setattr(getattr(model, src), "fetch", lambda *a, **k: {})

    model.gather_series(now=datetime(2026, 7, 20, 0, 30, tzinfo=_TZ), continuous_obs=True)
    assert seen["range"] == (date(2026, 7, 19), date(2026, 7, 20))
