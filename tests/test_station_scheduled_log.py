import config
import scheduled_log


def test_main_runs_every_station(monkeypatch):
    seen = []
    monkeypatch.setattr(scheduled_log, "run_station", lambda code: seen.append(code))
    scheduled_log.main()
    assert seen == config.STATION_CODES  # every station attempted, in order


def test_one_station_failure_does_not_block_the_other(monkeypatch):
    seen = []

    def flaky(code):
        seen.append(code)
        if code == "KDFW":
            raise RuntimeError("KDFW pipeline down")

    monkeypatch.setattr(scheduled_log, "run_station", flaky)
    scheduled_log.main()  # must not raise
    assert "KAUS" in seen and "KDFW" in seen


def test_run_station_threads_station_everywhere(monkeypatch):
    calls = {"cal": [], "settle": [], "snap_station": []}
    monkeypatch.setattr(scheduled_log.calibration, "get",
                        lambda refresh=True, station=None: calls["cal"].append(station)
                        or {"settlement_offset": {"high": 0.9}})

    def fake_snapshot(calib=None, settle_offset=None, continuous_obs=False,
                      include_candidate=False, station=None):
        calls["snap_station"].append(station)
        return {"station": station, "today": {"day": "2026-07-26"},
                "tomorrow": {"day": "2026-07-27"}}

    monkeypatch.setattr(scheduled_log.model, "snapshot", fake_snapshot)
    monkeypatch.setattr(scheduled_log, "_attach_market", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log, "_publish_det_models", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log.alerts, "maybe_fire_events", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log.forecast_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log.consensus_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log.betting_log, "current_slot", lambda now: None)
    monkeypatch.setattr(scheduled_log.settlements, "record",
                        lambda today=None, path=None, station=None: calls["settle"].append(station))
    monkeypatch.setattr(scheduled_log.settlements, "load", lambda station=None: [])

    scheduled_log.run_station("KAUS")
    assert calls["cal"] == ["KAUS"]
    assert calls["settle"] == ["KAUS"]
    assert "KAUS" in calls["snap_station"]


def test_alerts_are_station_tagged_for_non_default(monkeypatch, tmp_path):
    """Austin's recap gets a name-prefixed title and its own state file;
    Dallas (default) titles stay unprefixed (byte-identical)."""
    import alerts
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sent = []
    monkeypatch.setattr(alerts.notify, "send_ntfy",
                        lambda title, body: sent.append(title) or True)
    monkeypatch.setattr(alerts, "event_state_path",
                        lambda station=config.DEFAULT_STATION: str(tmp_path / f"ev_{station}.json"))
    monkeypatch.setattr(alerts, "_build_recap_body", lambda snap: "digest")
    now = datetime(2026, 7, 26, 13, 0, tzinfo=ZoneInfo("America/Chicago"))

    alerts.maybe_fire_events({}, now, station="KAUS")
    alerts.maybe_fire_events({}, now, station="KDFW")
    assert "Austin: Morning Recap" in sent
    assert "Morning Recap" in sent          # Dallas unprefixed
