"""Order-book fetch + ask-ladder reconstruction for Kalshi.

The fixture mirrors the LIVE /orderbook shape verified against
KXHIGHTDAL-26JUL14-T88: the payload is under `orderbook_fp` with `yes_dollars`
/ `no_dollars` arrays of [price_dollars_str, size_str] (prices already in
dollars, sizes possibly fractional), sorted ascending by price. Each array is
the resting BIDS on that side, so buying YES matches the NO bids
(yes_ask = 1 - no_bid) and vice-versa.
"""
from sources import kalshi

# Resting BIDS in dollars. yes-bids: buy YES at 0.53/0.54; no-bids: buy NO at 0.40/0.44.
FIXTURE = {"orderbook_fp": {
    "yes_dollars": [["0.5300", "200"], ["0.5400", "100"]],
    "no_dollars": [["0.4000", "300"], ["0.4400", "150"]],
}}


def test_fetch_orderbook_normalizes_to_float_sides():
    ob = kalshi.fetch_orderbook("KXHIGHTDAL-X", fetch=lambda t: FIXTURE)
    assert ob == {"yes": [[0.53, 200.0], [0.54, 100.0]],
                  "no": [[0.40, 300.0], [0.44, 150.0]]}


def test_ask_ladder_for_yes_from_no_bids_ascending():
    # Buying YES matches NO bids: no-bid 0.44 -> yes ask 0.56; no-bid 0.40 -> 0.60.
    ob = kalshi.fetch_orderbook("KXHIGHTDAL-X", fetch=lambda t: FIXTURE)
    ladder = kalshi.ask_ladder(ob, "yes")
    assert ladder == [(0.56, 150), (0.60, 300)]


def test_ask_ladder_for_no_from_yes_bids_ascending():
    # Buying NO matches YES bids: yes-bid 0.54 -> no ask 0.46; yes-bid 0.53 -> 0.47.
    ob = kalshi.fetch_orderbook("KXHIGHTDAL-X", fetch=lambda t: FIXTURE)
    ladder = kalshi.ask_ladder(ob, "no")
    assert ladder == [(0.46, 100), (0.47, 200)]


def test_ask_ladder_empty_book():
    assert kalshi.ask_ladder({"yes": [], "no": []}, "yes") == []


def test_fetch_orderbook_handles_empty_side():
    ob = kalshi.fetch_orderbook(
        "X", fetch=lambda t: {"orderbook_fp": {"no_dollars": [["0.9900", "50"]]}})
    assert ob == {"yes": [], "no": [[0.99, 50.0]]}
