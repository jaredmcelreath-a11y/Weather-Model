"""Per-station data-path routing. KDFW keeps its legacy bare paths (zero
migration); every other station is namespaced under data/<STATION>/."""
from __future__ import annotations

import os

import config

_ROOT = os.path.dirname(os.path.abspath(config.__file__))


def data_path(name: str, station: str = config.DEFAULT_STATION) -> str:
    """Absolute on-disk path for data file `name` for `station`.

    KDFW returns the legacy bare path (<repo>/<name>); any other station is
    namespaced under <repo>/data/<STATION>/<name>.
    """
    if station == config.DEFAULT_STATION:
        return os.path.join(_ROOT, name)
    return os.path.join(_ROOT, "data", station, name)


def github_path(name: str, station: str = config.DEFAULT_STATION) -> str:
    """Path used on the GitHub data branch (forward slashes). Mirrors
    data_path: bare for KDFW, data/<STATION>/<name> otherwise."""
    if station == config.DEFAULT_STATION:
        return name
    return f"data/{station}/{name}"
