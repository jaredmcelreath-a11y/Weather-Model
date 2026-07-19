"""Chart z-order + palette: the Shadow line draws BENEATH the production
consensus line (it was painting over it), and the Hourly page's Temp/Feels
chart follows the Charcoal palette like the Lab/Accuracy charts."""
import json
import sys
from datetime import datetime
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import pandas as pd

import hourly_view
import market_view


def _hist():
    times = pd.date_range("2026-07-19 08:00", periods=3, freq="30min")
    return pd.DataFrame({"time": times,
                         "Consensus": [96.0, 96.2, 96.1],
                         "Shadow": [95.5, 95.6, 95.7]}).set_index("time")


def test_shadow_line_draws_beneath_consensus():
    # Both lines hug each other, so whichever draws last wins visually — and
    # Shadow (cream) was painting over the red/blue consensus. The shadow line
    # must be its own bottom layer, with every other series drawn above it.
    spec = market_view.consensus_chart(_hist(), "high").to_dict()
    line_layers = [L for L in spec["layer"] if L.get("mark", {}).get("type") == "line"]
    assert len(line_layers) == 2
    assert "'Shadow'" in json.dumps(line_layers[0].get("transform", []))
    assert "!==" in json.dumps(line_layers[1].get("transform", []))


def test_hourly_chart_series_colors_apply_when_given():
    rows = [{"time": datetime(2026, 7, 19, h), "temp": 90.0 + h,
             "feels": 94.0 + h} for h in (8, 9, 10)]
    spec = hourly_view._temp_chart(
        rows, series_colors=["#A6D2BC", "#EDE6D3"]).to_dict()
    scale = spec["encoding"]["color"].get("scale")
    assert scale == {"domain": ["Temp", "Feels"],
                     "range": ["#A6D2BC", "#EDE6D3"]}
    plain = hourly_view._temp_chart(rows).to_dict()
    assert not plain["encoding"]["color"].get("scale")
