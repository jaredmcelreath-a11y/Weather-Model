"""The Action's calibration guard: without a settlement offset the snapshot is
hourly-basis numbers, and logging them as basis="cli" silently poisons the
scoring cohort — so scheduled_log must skip ALL model logging (but still record
settlements, which need no calibration)."""

import betting_log
import calibration
import consensus_log
import forecast_log
import model
import scheduled_log
import settlements
from sources import kalshi


def test_main_skips_model_logging_without_calibration(monkeypatch, capsys):
    monkeypatch.setattr(calibration, "get", lambda refresh=True: None)
    def boom(*a, **k):
        raise AssertionError("model.snapshot must not run without calibration")
    monkeypatch.setattr(model, "snapshot", boom)
    called = {}
    monkeypatch.setattr(settlements, "record", lambda: called.setdefault("rec", True))
    monkeypatch.setattr(settlements, "load", lambda path=None: [])
    scheduled_log.main()
    assert called.get("rec") is True
    assert "skipping model logging" in capsys.readouterr().out


def test_main_logs_when_calibration_present(monkeypatch):
    calib = {"settlement_offset": {"high": 0.9, "low": -0.4},
             "computed": "2026-07-13T10:00:00"}
    snap = {"updated": "2026-07-13T10:00:00",
            "today": {"day": "2026-07-13"}, "tomorrow": {"day": "2026-07-14"}}
    seen = []
    monkeypatch.setattr(calibration, "get", lambda refresh=True: calib)
    monkeypatch.setattr(model, "snapshot",
                        lambda c, settle_offset=None, continuous_obs=False,
                        include_candidate=False: snap)
    monkeypatch.setattr(kalshi, "implied_block", lambda t, tm: {})
    monkeypatch.setattr(forecast_log, "record",
                        lambda s, path=None, basis="hourly": seen.append(("forecast", basis)))
    monkeypatch.setattr(consensus_log, "record",
                        lambda s, path=None, basis="hourly": seen.append(("consensus", basis)))
    monkeypatch.setattr(betting_log, "current_slot", lambda now, **k: None)
    monkeypatch.setattr(settlements, "record", lambda: seen.append(("settlements",)))
    monkeypatch.setattr(settlements, "load", lambda path=None: [])
    monkeypatch.setattr(forecast_log, "load", lambda path=None: [])
    scheduled_log.main()
    assert ("forecast", "cli") in seen
    assert ("consensus", "cli") in seen
    assert ("settlements",) in seen
