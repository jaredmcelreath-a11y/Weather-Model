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
