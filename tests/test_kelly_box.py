"""The pure pick-selection behind the Kelly sizing box (no Streamlit).

`market_view` imports streamlit at module load. When streamlit isn't installed
(local dev) we stub it so the pure `_kelly_pick` logic is still testable; when it
IS installed (CI) this guard is a no-op and the real module is used.
"""
import sys

try:
    import streamlit  # noqa: F401
except ModuleNotFoundError:
    from unittest.mock import MagicMock
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import market_view


class _Adapter:
    # model_prob returns the YES prob stashed on each synthetic contract.
    def model_prob(self, probs, c):
        return c["p"]


def test_kelly_pick_selects_highest_edge_contract():
    contracts = [
        {"label": "88-89", "p": 0.55, "yes_ask": 0.54, "no_ask": 0.48},  # +0.01
        {"label": "90-91", "p": 0.70, "yes_ask": 0.55, "no_ask": 0.42},  # +0.15
    ]
    pick = market_view._kelly_pick(contracts, probs={}, adapter=_Adapter())
    assert pick is not None
    contract, side, q = pick
    assert contract["label"] == "90-91"
    assert side == "yes"
    assert q == 0.70


def test_kelly_pick_none_when_no_edge():
    contracts = [{"label": "88-89", "p": 0.50, "yes_ask": 0.55, "no_ask": 0.55}]
    assert market_view._kelly_pick(contracts, probs={}, adapter=_Adapter()) is None
