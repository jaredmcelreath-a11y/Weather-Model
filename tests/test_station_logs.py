import os

import betting_log
import calibration
import consensus_log
import forecast_log
import paths


def test_logs_route_paths_by_station():
    # Namespaced (absent) files for KAUS -> [].
    assert forecast_log.load(station="KAUS") == []
    assert consensus_log.load(station="KAUS") == []
    assert betting_log.load(station="KAUS") == []
    # KDFW still reads its legacy bare files.
    assert isinstance(forecast_log.load(station="KDFW"), list)
    assert isinstance(consensus_log.load(station="KDFW"), list)


def test_calibration_cache_path_by_station():
    # KDFW keeps the bare anchor; KAUS namespaces under data/KAUS/.
    assert calibration._cache_path("KDFW") == calibration._PATH
    assert calibration._cache_path("KAUS") == paths.data_path("calibration.json", "KAUS")
    assert calibration._cache_path("KAUS").endswith(
        os.path.join("data", "KAUS", "calibration.json"))


def test_scoring_score_accepts_station():
    # No KAUS forecast log yet -> zeroed structure, never raises.
    out = scoring_score_kaus()
    assert out["n_settled"] == 0


def scoring_score_kaus():
    import scoring
    return scoring.score(station="KAUS")
