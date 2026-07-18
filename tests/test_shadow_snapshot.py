"""snapshot(include_candidate=True) attaches an isolated candidate block."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _series_for(models_marker):
    # A single flat deterministic series so day_high_low returns a real number.
    now = datetime.now(_TZ)
    times = [now.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in range(24)]
    temps = [80.0 + models_marker] * 24
    return {"det_probe": (times, temps)}


def test_include_candidate_attaches_block_and_uses_candidate_models(monkeypatch):
    seen = {"det_models": []}

    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None):
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


def test_default_snapshot_has_no_candidate_block(monkeypatch):
    def fake_gather(forecast_days=2, continuous_obs=False, now=None,
                    det_models=None, ens_models=None):
        return _series_for(0), {"obs": ([], [])}, []
    monkeypatch.setattr(model, "gather_series", fake_gather)

    snap = model.snapshot()
    assert "candidate" not in snap
