import config
from sources import nws_observations, nws_cli


def test_obs_url_defaults_to_kdfw():
    assert nws_observations.obs_url() == \
        "https://api.weather.gov/stations/KDFW/observations"


def test_obs_url_for_austin():
    assert nws_observations.obs_url("KAUS") == \
        "https://api.weather.gov/stations/KAUS/observations"


def test_cli_list_url_by_station():
    assert nws_cli.list_url() == \
        "https://api.weather.gov/products/types/CLI/locations/DFW"
    assert nws_cli.list_url("KAUS") == \
        "https://api.weather.gov/products/types/CLI/locations/AUS"
