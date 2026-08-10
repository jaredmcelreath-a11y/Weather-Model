"""The live NO-price band — shared by the Screen page and the alert loop."""
import screen_rules
import screen_view


def test_no_ask_prefers_kalshis_own_no_ask():
    assert screen_rules.no_ask_of({"no_ask_dollars": "0.3500"}) == 0.35


def test_no_ask_falls_back_to_the_inverted_yes_bid():
    # Buying NO sells against the resting YES bid, so NO ask = 1 - yes bid.
    assert screen_rules.no_ask_of({"yes_bid_dollars": "0.8800"}) == 0.12


def test_no_ask_is_none_when_unquoted():
    assert screen_rules.no_ask_of({}) is None


def test_band_edges_are_inclusive():
    assert screen_rules.within_band(0.20) is True
    assert screen_rules.within_band(0.90) is True
    assert screen_rules.within_band(0.19) is False
    assert screen_rules.within_band(0.91) is False


def test_an_unquoted_row_survives_the_band():
    # Matches the page: an absent quote is thin liquidity, not a verdict about
    # the fade, so it must not hide the row.
    assert screen_rules.within_band(None) is True


def test_screen_view_shares_the_one_definition():
    assert screen_view.MIN_LIVE_NO_PRICE is screen_rules.MIN_LIVE_NO_PRICE
    assert screen_view.MAX_LIVE_NO_PRICE is screen_rules.MAX_LIVE_NO_PRICE
    assert screen_view.no_ask_of is screen_rules.no_ask_of


# ---- The mirror band, for the side that BUYS -------------------------------

def test_yes_ask_is_what_buying_yes_actually_costs():
    assert screen_rules.yes_ask_of({"yes_ask_dollars": "0.3900"}) == 0.39


def test_yes_ask_falls_back_to_the_no_bid_inverted():
    # Buying YES sells against the resting NO bid, so YES ask = 1 - no bid.
    assert screen_rules.yes_ask_of({"no_bid_dollars": "0.6100"}) == 0.39


def test_yes_ask_of_an_unquoted_market_is_none():
    assert screen_rules.yes_ask_of({}) is None


def test_the_yes_band_rejects_what_the_market_already_agrees_with():
    # Above the cap there is under 11% left to win.
    assert screen_rules.within_yes_band(0.90) is True
    assert screen_rules.within_yes_band(0.91) is False


def test_the_yes_band_rejects_a_price_that_says_our_reference_is_wrong():
    # A supposedly locked bracket at 5c is far likelier to mean a bad station
    # reading than free money -- exactly when this screen must not shout.
    assert screen_rules.within_yes_band(0.20) is True
    assert screen_rules.within_yes_band(0.05) is False


def test_an_unquoted_yes_row_survives_the_band():
    # An absent quote is thin liquidity, not evidence about the bracket.
    assert screen_rules.within_yes_band(None) is True


def test_phoenix_sits_inside_the_yes_band():
    assert screen_rules.within_yes_band(0.39) is True
