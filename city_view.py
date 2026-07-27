"""In-page city control for the two-city dashboard.

The pure selection logic (codes_for / resolve_selection / display_name) is
separated from the Streamlit widget so it unit-tests without a running app. The
control is a Title-Case `st.segmented_control` with a tooltip, responsive by
default — consistent with the dark-serif dashboard.
"""
from __future__ import annotations

import streamlit as st

import config

SELECTIONS_2 = ["Dallas", "Austin"]
SELECTIONS_3 = ["Dallas", "Austin", "Both"]
_LABEL_TO_CODE = {config.station(c).name: c for c in config.STATION_CODES}


def display_name(code: str) -> str:
    """Display name for a station code, e.g. 'KDFW' -> 'Dallas'."""
    return config.station(code).name


def codes_for(selection: str) -> list[str]:
    """Station code(s) a selection maps to. 'Both' -> every station."""
    if selection == "Both":
        return list(config.STATION_CODES)
    return [_LABEL_TO_CODE[selection]]


def resolve_selection(state: dict, page_key: str, arity: int) -> str:
    """Default selection for a page. 2-way pages follow the sticky single-city
    (default Dallas); 3-way pages default to Both but remember their own pick."""
    if arity == 3:
        return state.get(f"city_{page_key}", "Both")
    return state.get("city", "Dallas")


def city_control(page_key: str, arity: int = 2) -> str:
    """Render the toggle; return a station code (2-way) or the raw selection
    (3-way: one of Dallas/Austin/Both). The sticky single-city follows the user
    between pages."""
    options = SELECTIONS_3 if arity == 3 else SELECTIONS_2
    # Deep-link support: ?city=Austin seeds the sticky single-city once (mirrors
    # the theme's ?theme= handling), so a city is shareable/screenshottable by URL.
    try:
        qp = st.query_params.get("city")
    except Exception:
        qp = None
    if qp in ("Dallas", "Austin") and "city" not in st.session_state:
        st.session_state["city"] = qp
    default = resolve_selection(st.session_state, page_key, arity)
    choice = st.segmented_control(
        "City", options, default=default, key=f"citysel_{page_key}",
        help="Switch which city this page shows. Your pick follows you between pages.")
    if choice is None:
        choice = default
    if choice in ("Dallas", "Austin"):
        st.session_state["city"] = choice           # update the sticky single-city
    if arity == 3:
        st.session_state[f"city_{page_key}"] = choice
        return choice
    return codes_for(choice)[0]


def city_sections(page_key: str, arity: int = 3) -> tuple[str, list[str]]:
    """Render the control and return `(selection, codes)` for a page that loops
    stations. For 3-way pages `selection` is one of Dallas/Austin/Both and the
    page draws a `display_name` subheader per code only when selection == "Both".
    For 2-way pages `city_control` already returns a code, so codes == [sel]."""
    sel = city_control(page_key, arity)
    if arity == 2:
        return sel, [sel]                 # sel is already a code for 2-way
    return sel, codes_for(sel)
