"""Market adapters — everything that differs between the Robinhood (ForecastEx)
and Kalshi dashboard pages, so market_view.render_page can stay market-agnostic.

Each adapter bundles: the live contract fetch (cached), the model→contract price
mapping, the on-screen wording, and the page's safe-hold defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import streamlit as st

import model
from sources import kalshi, robinhood


@dataclass(frozen=True)
class MarketAdapter:
    name: str                       # "Robinhood" / "Kalshi"
    exchange: str                   # "ForecastEx" / "Kalshi"
    fetch: Callable                 # (variable, day_iso) -> list[contract dict]
    model_prob: Callable            # (probs, contract) -> model YES probability
    heading: Callable               # (variable) -> markdown heading for the table
    no_market_msg: str              # caption when no contracts are live
    settle_footer: str              # page footer caveat
    safe_hold_default: float        # safe-hold slider default (fraction)
    safe_hold_min: float            # safe-hold slider minimum (fraction)
    basis: str = "hourly"           # forecast_log/consensus_log basis ("hourly"/"cli")
    basis_note: str | None = None   # caption shown under the market heading
    accuracy_note: str | None = None  # caption shown in the model-accuracy expander


@st.cache_data(ttl=30, show_spinner=False)
def _rh_fetch(variable, day_iso):
    """Live Robinhood (ForecastEx) 'Greater/Lower than T°' ladder."""
    return robinhood.fetch_ladder(variable, date.fromisoformat(day_iso))


@st.cache_data(ttl=30, show_spinner=False)
def _kx_fetch(variable, day_iso):
    """Live Kalshi 2°F range-bucket contracts for Dallas."""
    return kalshi.fetch_contracts(variable, date.fromisoformat(day_iso))


ROBINHOOD = MarketAdapter(
    name="Robinhood",
    exchange="ForecastEx",
    fetch=_rh_fetch,
    model_prob=lambda probs, c: model.prob_for_contract(probs, c["kind"], c["strike"]),
    heading=lambda var: (
        "**Live Robinhood market vs model** — "
        f"“{'Greater than' if var == 'high' else 'Lower than'} T°” contracts"),
    no_market_msg="No live Robinhood market for this day yet.",
    settle_footer=(
        "⚠️ Live prices scraped from Robinhood’s public event pages "
        "(ForecastEx). High = “Greater than T”, Low = “Lower than T”, "
        "settled on the whole-degree KDFW value (Weather Underground). "
        "Not financial advice."),
    safe_hold_default=0.80,
    safe_hold_min=0.60,
)

KALSHI = MarketAdapter(
    name="Kalshi",
    exchange="Kalshi",
    fetch=_kx_fetch,
    model_prob=lambda probs, c: model.prob_for_strike(
        probs, c["strike_type"], c["floor"], c["cap"]),
    heading=lambda var: "**Live Kalshi market vs model** — 2°F range buckets",
    no_market_msg="No live Kalshi market for this day yet.",
    settle_footer=(
        "⚠️ Live prices from Kalshi’s public API. Contracts are 2°F "
        "range buckets, settled on the NWS Climatological Report (CLIDFW) — which "
        "can occasionally differ by a degree from Weather Underground. "
        "Not financial advice."),
    safe_hold_default=0.55,
    safe_hold_min=0.50,
    basis="cli",
    basis_note=("Values on the NWS CLI settlement basis (continuous ASOS daily "
                "max/min) — what Kalshi resolves on, ~+0.9°F vs the hourly basis "
                "on highs."),
    accuracy_note=("📐 Accuracy scored on the NWS CLI settlement basis "
                   "(what Kalshi resolves on)."),
)
