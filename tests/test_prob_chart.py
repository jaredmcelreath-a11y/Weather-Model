"""Regression test for the probability bar chart's x-axis ordering.

Bug: when a bin crossed into triple digits (e.g. 100°F), the bar rendered on
the wrong side of the chart, producing a U/jagged shape. Root cause: the bins
are string labels and Vega-Lite sorts a nominal axis lexicographically, so
"100" sorts before "99". The fix pins the axis sort to the numeric order that
prob_table already establishes."""

import pandas as pd

from market_view import prob_bar_chart


def _x_order(chart):
    return chart.to_dict()["encoding"]["x"]["sort"]


def test_triple_digit_bin_stays_on_the_right():
    # Numeric order, exactly as prob_table emits it: 100 after 99.
    df = pd.DataFrame(
        {"prob %": [1.0, 5.0, 15.0, 30.0, 25.0, 12.0, 4.0]},
        index=["95", "96", "97", "98", "99", "100", "101"],
    )
    df.index.name = "bin"

    order = _x_order(prob_bar_chart(df, "high"))

    assert order == ["95", "96", "97", "98", "99", "100", "101"]
    # The lexicographic bug would put "100"/"101" before "99".
    assert order.index("100") > order.index("99")


def test_capped_tail_labels_keep_their_ends():
    df = pd.DataFrame(
        {"prob %": [3.0, 20.0, 40.0, 25.0, 12.0]},
        index=["<=98", "99", "100", "101", ">=102"],
    )
    df.index.name = "bin"

    order = _x_order(prob_bar_chart(df, "high"))

    assert order[0] == "<=98"
    assert order[-1] == ">=102"
