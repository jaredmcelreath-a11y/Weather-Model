"""calibration.get() freshness/robustness: the scheduled Action restores
calibration.json from the data branch (mtime = 'just now' every run), so
freshness must travel with the file's internal `computed` timestamp; a failed
restore leaves an empty file, and a failed recompute should serve the last
good copy rather than nothing."""

import json
from datetime import datetime, timedelta

import calibration


def _write(path, payload):
    with open(path, "w") as fh:
        json.dump(payload, fh)


def _stamp(hours_ago):
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _no_recompute(monkeypatch):
    def boom():
        raise AssertionError("must not recompute")
    monkeypatch.setattr(calibration, "compute_and_save", boom)


def test_fresh_by_computed_timestamp_skips_recompute(tmp_path, monkeypatch):
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(2), "bias": {}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=True)["bias"] == {}


def test_stale_computed_recomputes_despite_fresh_mtime(tmp_path, monkeypatch):
    # The Action-restore scenario: file just written to disk (fresh mtime) but
    # its content says it was computed 30h ago -> must recompute.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30)})
    monkeypatch.setattr(calibration, "compute_and_save", lambda: {"computed": "new"})
    assert calibration.get(refresh=True) == {"computed": "new"}


def test_missing_computed_falls_back_to_mtime(tmp_path, monkeypatch):
    # Pre-upgrade file without a `computed` field, mtime just now -> fresh.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"bias": {"low": 0.1}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=True)["bias"] == {"low": 0.1}


def test_corrupt_file_recomputes(tmp_path, monkeypatch):
    # A failed `git show ... > calibration.json || true` leaves an EMPTY file;
    # get() must treat it as absent, not crash on json.load.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    open(p, "w").close()
    monkeypatch.setattr(calibration, "compute_and_save", lambda: {"computed": "new"})
    assert calibration.get(refresh=True) == {"computed": "new"}


def test_recompute_failure_serves_stale_cache(tmp_path, monkeypatch):
    # Stale copy + dead upstream: a 2-day-old settlement offset beats none.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30), "settlement_offset": {"high": 0.9}})
    def boom():
        raise RuntimeError("IEM down")
    monkeypatch.setattr(calibration, "compute_and_save", boom)
    got = calibration.get(refresh=True)
    assert got["settlement_offset"] == {"high": 0.9}


def test_nothing_usable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "_PATH", str(tmp_path / "missing.json"))
    def boom():
        raise RuntimeError("IEM down")
    monkeypatch.setattr(calibration, "compute_and_save", boom)
    assert calibration.get(refresh=True) is None


def test_refresh_false_returns_stale_cache_without_recompute(tmp_path, monkeypatch):
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30), "bias": {}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=False)["computed"]  # stale copy returned as-is
