import sys
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    sys.modules.setdefault("streamlit", MagicMock())

import city_view


def test_codes_for():
    assert city_view.codes_for("Dallas") == ["KDFW"]
    assert city_view.codes_for("Austin") == ["KAUS"]
    assert city_view.codes_for("Both") == ["KDFW", "KAUS"]


def test_display_name():
    assert city_view.display_name("KDFW") == "Dallas"
    assert city_view.display_name("KAUS") == "Austin"


def test_resolve_2way_defaults_to_sticky_dallas():
    state = {}
    assert city_view.resolve_selection(state, "forecast", 2) == "Dallas"
    # a prior Austin pick sticks across pages
    state["city"] = "Austin"
    assert city_view.resolve_selection(state, "hourly", 2) == "Austin"


def test_resolve_3way_defaults_both_but_remembers_own_pick():
    state = {"city": "Austin"}          # sticky single-city doesn't force 3-way
    assert city_view.resolve_selection(state, "edge", 3) == "Both"
    state["city_edge"] = "Dallas"       # the page's own remembered pick wins
    assert city_view.resolve_selection(state, "edge", 3) == "Dallas"
