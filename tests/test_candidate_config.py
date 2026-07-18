"""Candidate model lists are supersets of production and add real models."""
import config


def test_candidate_det_is_superset_of_production():
    prod = set(config.DETERMINISTIC_MODELS)
    cand = set(config.CANDIDATE_DETERMINISTIC_MODELS)
    assert prod <= cand
    assert len(cand) > len(prod)  # at least one new model was added


def test_candidate_ens_is_superset_of_production():
    prod = set(config.ENSEMBLE_MODELS)
    cand = set(config.CANDIDATE_ENSEMBLE_MODELS)
    assert prod <= cand  # ensemble candidates may equal prod if none survive probe


def test_production_lists_unchanged():
    # Guards against accidental edits to the live model set.
    assert config.DETERMINISTIC_MODELS == [
        "gfs_seamless", "ecmwf_ifs025", "icon_seamless",
        "gem_seamless", "gfs_hrrr",
    ]
    assert config.ENSEMBLE_MODELS == [
        "gfs_seamless", "icon_seamless", "ecmwf_ifs025", "gem_global_ensemble",
    ]
