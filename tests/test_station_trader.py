from sources import kalshi, kalshi_portfolio


def test_station_of_ticker():
    assert kalshi.station_of_ticker("KXHIGHAUS-26JUL27-T96") == "KAUS"
    assert kalshi.station_of_ticker("KXLOWTAUS-26JUL27-T77") == "KAUS"
    assert kalshi.station_of_ticker("KXHIGHTDAL-26JUL27-B99") == "KDFW"
    assert kalshi.station_of_ticker("KXLOWTDAL-26JUL27-B79") == "KDFW"
    assert kalshi.station_of_ticker("KXNADA-1") is None


def test_variable_of_ticker_both_cities():
    assert kalshi.variable_of_ticker("KXHIGHAUS-26JUL27-T96") == "high"
    assert kalshi.variable_of_ticker("KXLOWTAUS-26JUL27-T77") == "low"
    # kalshi_portfolio.variable_of now delegates and handles Austin too
    assert kalshi_portfolio.variable_of("KXLOWTAUS-26JUL27-T77") == "low"
    assert kalshi_portfolio.variable_of("KXHIGHTDAL-26JUL27-B99") == "high"  # unchanged
