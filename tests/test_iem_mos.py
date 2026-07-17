"""IEM MOS/LAMP guidance adapter — parsing and series construction."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE
from sources import iem_mos

_TZ = ZoneInfo(TIMEZONE)


def _row(ftime_utc, tmp):
    return {"ftime_utc": ftime_utc, "tmp": tmp, "station": "KDFW"}


def test_parse_returns_tzaware_local_times_and_float_temps():
    data = {"data": [
        _row("2026-06-23T17:00:00.000", 87),
        _row("2026-06-23T18:00:00.000", 88),
    ]}
    times, temps = iem_mos._parse(data)
    assert temps == [87.0, 88.0]
    assert all(t.tzinfo is not None for t in times)
    # 17:00 UTC == 12:00 local (America/Chicago, CDT)
    assert times[0].astimezone(_TZ).hour == 12


def test_parse_skips_null_temps_and_missing_times():
    data = {"data": [
        _row("2026-06-23T17:00:00.000", None),
        _row(None, 90),
        _row("2026-06-23T19:00:00.000", 86),
    ]}
    times, temps = iem_mos._parse(data)
    assert temps == [86.0]
    assert len(times) == 1


def test_parse_empty_data():
    assert iem_mos._parse({"data": []}) == ([], [])
    assert iem_mos._parse({}) == ([], [])


def test_fetch_builds_one_series_per_model(monkeypatch):
    def fake_get_json(url, params, **kw):
        assert params["station"] == "KDFW"
        return {"data": [_row("2026-06-23T17:00:00.000", 80)]}
    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.fetch()
    assert set(out) == {f"mos_{m.lower()}" for m in iem_mos.MODELS}
    for _label, (times, temps) in out.items():
        assert temps == [80.0]


def test_fetch_skips_a_failing_model(monkeypatch):
    def fake_get_json(url, params, **kw):
        if params["model"] == iem_mos.MODELS[0]:
            raise RuntimeError("network")
        return {"data": [_row("2026-06-23T17:00:00.000", 80)]}
    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.fetch()
    assert f"mos_{iem_mos.MODELS[0].lower()}" not in out
    assert len(out) == len(iem_mos.MODELS) - 1


def test_mos_label_routes_to_guidance_group():
    assert model._group_of("mos_lav") == "guidance"
    assert model._group_of("mos_nbs") == "guidance"


def test_historical_extremes_uses_prior_day_12z_run(monkeypatch):
    calls = []

    def fake_get_json(url, params=None, **kwargs):
        calls.append(params)
        return {"data": [
            _row("2026-06-03T11:00:00.000", 72),   # ~6am CDT low
            _row("2026-06-03T20:00:00.000", 95),   # ~3pm CDT high
        ]}

    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))

    assert set(out[date(2026, 6, 3)]) == {"mos_lav", "mos_nbs"}
    assert out[date(2026, 6, 3)]["mos_nbs"] == (95.0, 72.0)
    # runtime must be the PRIOR day at 12Z, once per model (2 models).
    assert all(p["runtime"] == "2026-06-02T12:00Z" for p in calls)
    assert {p["model"] for p in calls} == {"LAV", "NBS"}


def test_historical_extremes_skips_a_model_with_no_run(monkeypatch):
    def fake_get_json(url, params=None, **kwargs):
        if params["model"] == "LAV":
            raise RuntimeError("no archived run")
        return {"data": [
            _row("2026-06-03T11:00:00.000", 72),
            _row("2026-06-03T20:00:00.000", 95),
        ]}

    monkeypatch.setattr(iem_mos, "get_json", fake_get_json)
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))
    assert set(out[date(2026, 6, 3)]) == {"mos_nbs"}


def test_historical_extremes_omits_day_with_no_data(monkeypatch):
    monkeypatch.setattr(iem_mos, "get_json",
                        lambda url, params=None, **kw: {"data": []})
    out = iem_mos.historical_extremes(date(2026, 6, 3), date(2026, 6, 3))
    assert out == {}
