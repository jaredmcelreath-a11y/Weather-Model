"""snapshot(include_candidate=True) attaches an isolated candidate block."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

# A FIXED instant, never datetime.now(). `snapshot` predicts today from the hours
# still AHEAD of now, and the synthetic series below spans one calendar day
# (00:00-23:00). On the real clock that left nothing forward-looking once the
# hour passed 23:00, so today's high came back None: this test passed at 22:00
# and failed at 23:00, i.e. only on late-evening runs. Freezing the clock makes
# it deterministic at every hour.
_NOW = datetime(2026, 7, 15, 14, 0, tzinfo=_TZ)


class _FrozenDatetime(datetime):
    """`datetime` with `now()` pinned to _NOW; every other behavior inherited."""

    @classmethod
    def now(cls, tz=None):
        return _NOW if tz is None else _NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    """snapshot() reads the clock itself (`now = datetime.now(TZ)`), so the only
    way to pin it without changing the production signature is the module's
    `datetime` name."""
    monkeypatch.setattr(model, "datetime", _FrozenDatetime)


def _series_for(models_marker):
    # A single flat deterministic series so day_high_low returns a real number,
    # spanning the frozen day so hours remain ahead of _NOW.
    times = [_NOW.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in range(24)]
    temps = [80.0 + models_marker] * 24
    return {"det_probe": (times, temps)}


def test_include_candidate_attaches_block_and_uses_candidate_models(monkeypatch):
    seen = {"det_models": []}

    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None, station=None):
        seen["det_models"].append(det_models)
        marker = 0 if det_models is None else 1
        return _series_for(marker), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot(include_candidate=True)
    # Production block present and unchanged in shape.
    assert "consensus" in snap["today"]["high"]
    # Candidate block present.
    assert "candidate" in snap
    assert "consensus" in snap["candidate"]["today"]["high"]
    # Two gather calls: one production (None), one candidate (candidate list).
    assert None in seen["det_models"]
    assert config.CANDIDATE_DETERMINISTIC_MODELS in seen["det_models"]


def test_the_frozen_clock_is_actually_in_effect(monkeypatch):
    """Guards the fixture itself: if the patch ever stops taking, the tests above
    quietly go back to reading the wall clock and to failing after 23:00."""
    captured = {}

    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None, station=None):
        captured["now"] = now
        return _series_for(0), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot()
    assert captured["now"] == _NOW
    assert snap["today"]["day"] == _NOW.date().isoformat()


def test_default_snapshot_has_no_candidate_block(monkeypatch):
    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None, station=None):
        return _series_for(0), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot()
    assert "candidate" not in snap
