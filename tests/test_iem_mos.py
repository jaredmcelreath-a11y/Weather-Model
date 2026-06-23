"""IEM MOS/LAMP guidance adapter — parsing and series construction."""
from datetime import datetime, timezone
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
