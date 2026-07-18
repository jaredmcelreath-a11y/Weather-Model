"""backtest.run threads a deterministic model-set override to the archive fetch.

Uses a sentinel raised from the (stubbed) archive fetch so the assertion runs
right after the model list is captured — before run()'s scoring loop, which
would divide by zero on the empty stub series."""
import pytest

import config
import backtest


class _Stop(Exception):
    pass


def test_run_passes_det_models_to_fetch_historical(monkeypatch):
    seen = {}

    def fake_hist(start, end, ttl=24 * 3600, models=None):
        seen["models"] = models
        raise _Stop  # captured the list; stop before the scoring loop

    monkeypatch.setattr(backtest.station_history, "fetch_actual",
                        lambda start, end: {})
    monkeypatch.setattr(backtest.open_meteo_models, "fetch_historical", fake_hist)

    with pytest.raises(_Stop):
        backtest.run(days=5, det_models=config.CANDIDATE_DETERMINISTIC_MODELS)
    assert seen["models"] == config.CANDIDATE_DETERMINISTIC_MODELS

    with pytest.raises(_Stop):
        backtest.run(days=5)  # production default => None
    assert seen["models"] is None
