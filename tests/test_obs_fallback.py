"""Obs-feed outage resilience: IEM METAR fallback when the NWS feed goes stale.

Live 2026-07-18 04:00 CDT: the NWS observations endpoint transiently served no
usable in-window readings, so the model silently treated 3h of observed warm
overnight temps as "no obs yet today" and reverted the low consensus to the
pure forecast (76.8 vs the obs-anchored 78.7) for one capture. The obs series
is the nowcasting engine — a transient upstream gap must not erase what the
station already reported. IEM's ASOS archive carries the very same METARs over
an independent pipeline, so a stale NWS response falls back to it; a healthy
NWS response never consults IEM.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sources.nws_observations as nws
from sources import station_history
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
NOW = datetime(2026, 7, 18, 4, 0, tzinfo=_TZ)


def _feat(ts: datetime, temp_c: float) -> dict:
    return {"properties": {"timestamp": ts.isoformat(),
                           "temperature": {"value": temp_c}}}


def _iem(hours=(0, 1, 2, 3)):
    times = [datetime(2026, 7, 18, h, 53, tzinfo=_TZ) for h in hours]
    return times, [81.0 - 0.5 * i for i in range(len(times))]


def test_empty_nws_falls_back_to_iem(monkeypatch):
    monkeypatch.setattr(nws, "get_json", lambda *a, **k: {"features": []})
    monkeypatch.setattr(station_history, "_fetch_series",
                        lambda s, e, ttl=None: _iem())
    out = nws.fetch(continuous=True, now=NOW)
    times, temps = out["obs"]
    assert temps == [81.0, 80.5, 80.0, 79.5]
    assert out["obs_continuous"][1] == [81.0, 80.5, 80.0, 79.5]


def test_stale_nws_falls_back_to_iem(monkeypatch):
    # NWS answered, but its latest reading is >90 min old (00:53 at a 04:00
    # capture) — stale counts as down.
    monkeypatch.setattr(nws, "get_json", lambda *a, **k: {
        "features": [_feat(datetime(2026, 7, 18, 0, 53, tzinfo=_TZ), 27.0)]})
    monkeypatch.setattr(station_history, "_fetch_series",
                        lambda s, e, ttl=None: _iem())
    out = nws.fetch(now=NOW)
    assert out["obs"][1] == [81.0, 80.5, 80.0, 79.5]


def test_fresh_nws_never_consults_iem(monkeypatch):
    feats = [_feat(NOW - timedelta(minutes=m), 27.0) for m in (67, 37, 7)]
    monkeypatch.setattr(nws, "get_json", lambda *a, **k: {"features": feats})

    def _boom(*a, **k):
        raise AssertionError("IEM consulted on a healthy NWS feed")
    monkeypatch.setattr(station_history, "_fetch_series", _boom)
    out = nws.fetch(now=NOW)
    assert len(out["obs"][1]) >= 1


def test_both_feeds_dead_returns_empty_not_crash(monkeypatch):
    monkeypatch.setattr(nws, "get_json", lambda *a, **k: {"features": []})

    def _boom(*a, **k):
        raise RuntimeError("IEM down too")
    monkeypatch.setattr(station_history, "_fetch_series", _boom)
    out = nws.fetch(now=NOW)
    assert out["obs"] == ([], [])


def test_iem_empty_keeps_nws_result(monkeypatch):
    # Fallback consulted but IEM has nothing either: keep the (stale) NWS
    # readings rather than throwing data away.
    monkeypatch.setattr(nws, "get_json", lambda *a, **k: {
        "features": [_feat(datetime(2026, 7, 18, 0, 53, tzinfo=_TZ), 27.0)]})
    monkeypatch.setattr(station_history, "_fetch_series",
                        lambda s, e, ttl=None: ([], []))
    out = nws.fetch(now=NOW)
    assert len(out["obs"][1]) == 1
