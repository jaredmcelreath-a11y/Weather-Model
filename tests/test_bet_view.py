"""Import smoke for the My Bets page + a pure check on the equity chart helper
(the Streamlit render itself needs live credentials, so it's verified manually)."""

import sys
from datetime import date
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

try:
    import cryptography  # noqa: F401
except ImportError:
    for _m in ("cryptography", "cryptography.hazmat", "cryptography.hazmat.primitives",
               "cryptography.hazmat.primitives.asymmetric"):
        sys.modules.setdefault(_m, MagicMock())


def test_bet_view_imports():
    import bet_view  # must import without side effects / missing names
    assert hasattr(bet_view, "render") and hasattr(bet_view, "equity_chart")


def test_model_cell_is_probability_only():
    import bet_view
    # No model read at all.
    assert bet_view._model_cell({"model_prob": None}) == "—"
    # Probability only now — edge/with-against moved out to tighten the column,
    # and it must not depend on edge/agree being present.
    assert bet_view._model_cell(
        {"model_prob": 0.62, "edge": None, "agree": None}) == "62%"
    assert bet_view._model_cell(
        {"model_prob": 0.62, "edge": 0.19, "agree": True}) == "62%"


def test_pct_gain_cell():
    import bet_view
    # Closed/settled: realized pnl ÷ staked.
    assert bet_view._pct_gain_cell(
        {"status": "settled", "pnl": 3.0, "staked": 6.0}) == "+50.0%"
    assert bet_view._pct_gain_cell(
        {"status": "closed", "pnl": -1.5, "staked": 6.0}) == "-25.0%"
    # Open: marked to market from current_value, shown live in terracotta with a ~.
    open_cell = bet_view._pct_gain_cell(
        {"status": "open", "entry": 0.50, "current_value": 0.60,
         "qty": 10.0, "staked": 5.0})
    assert "~" in open_cell and "+20.0%" in open_cell and "C97B5E" in open_cell
    # No staked / no mark → em dash, never a crash.
    assert bet_view._pct_gain_cell({"status": "settled", "pnl": 3.0, "staked": 0}) == "—"
    assert bet_view._pct_gain_cell(
        {"status": "open", "entry": None, "current_value": None,
         "qty": 1.0, "staked": 5.0}) == "—"


def test_equity_chart_encodes_date_and_total():
    import bet_view
    curve = [{"date": date(2026, 6, 23), "total": 5.8},
             {"date": date(2026, 6, 24), "total": 0.8}]
    spec = bet_view.equity_chart(curve, color="#7FD3A2").to_dict()
    # equity_chart layers a zero-baseline rule, the balance line, tappable dots, and a
    # pinned tap-to-read label. The date/total encoding lives on the line + dots layers
    # (the rule and the pixel-anchored text label don't carry an x field).
    dated = [L for L in spec["layer"]
             if L.get("encoding", {}).get("x", {}).get("field") == "date"]
    assert dated, "expected a layer encoding date on x"
    assert dated[0]["encoding"]["y"]["field"] == "total"
    # a click selection drives the tap-to-pin readout (mobile: no hover needed)
    assert any(isinstance(p.get("select"), dict) and p["select"].get("on") == "click"
               for p in spec.get("params", []))


def test_equity_chart_ships_datetimes_not_bare_date_strings():
    # Bare "2026-07-18" strings in a temporal encoding parse as UTC midnight in
    # the browser and render a day early for US viewers; naive datetimes
    # ("...T00:00:00") parse as local midnight and stay on the right day.
    import bet_view
    curve = [{"date": "2026-07-17", "total": 100.0},
             {"date": "2026-07-18", "total": 104.5}]
    spec = bet_view.equity_chart(curve, "#8bc34a").to_dict()
    checked = 0
    for ds in spec["datasets"].values():
        for row in ds:
            if "date" in row:
                assert "T" in str(row["date"]), row
                checked += 1
    assert checked, "expected at least one dated row in the shipped datasets"
