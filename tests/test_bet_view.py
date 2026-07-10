"""Import smoke for the My Bets page + a pure check on the equity chart helper
(the Streamlit render itself needs live credentials, so it's verified manually)."""

from datetime import date


def test_bet_view_imports():
    import bet_view  # must import without side effects / missing names
    assert hasattr(bet_view, "render") and hasattr(bet_view, "equity_chart")


def test_model_cell_handles_missing_edge():
    import bet_view
    # No model read at all.
    assert bet_view._model_cell({"model_prob": None}) == "—"
    # model_prob present but edge is None (entry was None — e.g. a resolved side
    # with zero matching BUY fills). Must not raise, and shows probability only.
    assert bet_view._model_cell(
        {"model_prob": 0.62, "edge": None, "agree": None}) == "62%"
    # Both present — full cell with edge and with/against.
    cell = bet_view._model_cell({"model_prob": 0.62, "edge": 0.19, "agree": True})
    assert "62%" in cell and "+19" in cell and "with" in cell


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
