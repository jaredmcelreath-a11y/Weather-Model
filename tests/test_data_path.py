import os

import config
import paths


def test_kdfw_uses_legacy_bare_path():
    p = paths.data_path("forecast_log.jsonl", "KDFW")
    assert os.path.basename(p) == "forecast_log.jsonl"
    assert os.path.dirname(p) == os.path.dirname(os.path.abspath(config.__file__))
    # default arg is KDFW
    assert paths.data_path("settlements.jsonl") == paths.data_path("settlements.jsonl", "KDFW")


def test_other_station_is_namespaced():
    p = paths.data_path("forecast_log.jsonl", "KAUS")
    assert p.endswith(os.path.join("data", "KAUS", "forecast_log.jsonl"))


def test_github_path_shape():
    assert paths.github_path("settlements.jsonl", "KDFW") == "settlements.jsonl"
    assert paths.github_path("settlements.jsonl", "KAUS") == "data/KAUS/settlements.jsonl"
