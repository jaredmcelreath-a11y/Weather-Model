"""Import smoke for the My Bets page + a pure check on the equity chart helper
(the Streamlit render itself needs live credentials, so it's verified manually)."""

from datetime import date


def test_bet_view_imports():
    import bet_view  # must import without side effects / missing names
    assert hasattr(bet_view, "render") and hasattr(bet_view, "equity_chart")


def test_equity_chart_encodes_date_and_total():
    import bet_view
    curve = [{"date": date(2026, 6, 23), "total": 5.8},
             {"date": date(2026, 6, 24), "total": 0.8}]
    spec = bet_view.equity_chart(curve, color="#7FD3A2").to_dict()
    # equity_chart layers a zero-baseline rule under the P&L line, so per
    # Vega-Lite the x/y encodings live on the individual layers, not hoisted
    # to the top level. x is the date field, y is the cumulative total field
    # on the line layer (the last layer, drawn on top of the zero rule).
    line_encoding = spec["layer"][-1]["encoding"]
    assert line_encoding["x"]["field"] == "date"
    assert line_encoding["y"]["field"] == "total"
