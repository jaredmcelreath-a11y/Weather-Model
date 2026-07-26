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


def test_trade_state_paths_by_station():
    import trade_state
    assert trade_state._path("trade_state.json", "KDFW") == "trade_state.json"
    assert trade_state._path("trade_state.json", "KAUS") == "trade_state.KAUS.json"
    assert trade_state._path("trade_log.jsonl", "KAUS") == "trade_log.KAUS.jsonl"


def test_load_state_defaults_ship_safe_for_absent_station():
    import trade_state

    class _T:  # transport that has no file for anyone
        def get(self, path):
            return None

        def put(self, path, text, sha):
            raise AssertionError("no write expected")

    p = trade_state.load_state(transport=_T(), station="KAUS")
    assert p["kill_switch"] is True and p["mode"] == "shadow"   # ships DISABLED
