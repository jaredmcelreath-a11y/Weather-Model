"""The Screen page: brackets worth two minutes of attention.

Reads scan_candidates.jsonl and shows the newest firing. Display tables are
hand-rolled HTML because canvas-rendered st.dataframe cannot center cells --
the same reason every other table in this app is.
"""
from __future__ import annotations

import html

import streamlit as st

import scan_log


def latest_firing(rows: list) -> list:
    """Only the candidates from the most recent firing."""
    if not rows:
        return []
    newest = max(r.get("ts") or "" for r in rows)
    return [r for r in rows if (r.get("ts") or "") == newest]


def display_rows(rows: list) -> list:
    """Ranked by price x gap: how much the market pays, times how wrong it looks.
    A missing gap sorts last rather than raising."""
    def rank(r):
        price, gap = r.get("price"), r.get("gap")
        if price is None or gap is None:
            return -1.0
        return float(price) * float(gap)
    return sorted(rows, key=rank, reverse=True)


def _bracket_label(row: dict) -> str:
    floor, cap = row.get("floor"), row.get("cap")
    if floor is not None and cap is not None:
        return f"{floor}-{cap}"
    if cap is not None:
        return f"<{cap}"
    if floor is not None:
        return f">{floor}"
    return "?"


def render() -> None:
    st.subheader("Screen — mispriced brackets")
    st.caption(
        "Candidates for review, not signals. The NWS forecast is public, so a "
        "gap usually means the market knows something — 'dead' rows are the "
        "hard ones: realized temperature already ruled them out."
    )
    try:
        rows = latest_firing(scan_log.load(scan_log.CANDIDATES_PATH))
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.info(f"No candidate log yet ({e}).")
        return
    if not rows:
        st.info("No candidates in the latest firing.")
        return

    head = ("<tr><th>City</th><th>Var</th><th>Bracket</th><th>Price</th>"
            "<th>Ref</th><th>Gap</th><th>Kind</th><th>Hrs</th></tr>")
    body = []
    for r in display_rows(rows):
        body.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('series') or ''))}</td>"
            f"<td>{html.escape(str(r.get('variable') or ''))}</td>"
            f"<td>{html.escape(_bracket_label(r))}</td>"
            f"<td>{r.get('price')}</td>"
            f"<td>{r.get('forecast')}</td>"
            f"<td>{r.get('gap')}</td>"
            f"<td>{html.escape(str(r.get('kind') or ''))}</td>"
            f"<td>{r.get('hours_to_close')}</td>"
            "</tr>")
    st.markdown(
        "<table class='screen-table'>" + head + "".join(body) + "</table>",
        unsafe_allow_html=True)
