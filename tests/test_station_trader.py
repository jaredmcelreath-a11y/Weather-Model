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


def test_real_deps_positions_are_station_isolated(monkeypatch):
    import trader
    from sources import kalshi_portfolio
    monkeypatch.setattr(kalshi_portfolio, "positions", lambda: [
        {"ticker": "KXHIGHTDAL-26JUL27-B99"}, {"ticker": "KXHIGHAUS-26JUL27-T96"}])
    aus = trader._real_deps("KAUS").positions()
    assert [p["ticker"] for p in aus] == ["KXHIGHAUS-26JUL27-T96"]
    dfw = trader._real_deps("KDFW").positions()
    assert [p["ticker"] for p in dfw] == ["KXHIGHTDAL-26JUL27-B99"]


def test_main_runs_every_station(monkeypatch):
    import config
    import trader
    seen = []
    monkeypatch.setattr(trader, "_real_deps", lambda code: code)
    monkeypatch.setattr(trader, "run_once",
                        lambda now=None, *, deps, station: seen.append(station) or {})
    trader.main()
    assert seen == config.STATION_CODES


def test_kaus_run_once_no_ops_at_default_kill_switch():
    import trader
    import trade_params
    from datetime import datetime
    from types import SimpleNamespace
    placed = []
    deps = SimpleNamespace(
        load_state=lambda: trade_params.DEFAULT_PARAMS.copy(),
        load_runtime=lambda: {}, save_runtime=lambda r: None,
        snapshot=lambda: {}, balance=lambda: 100.0, positions=lambda: [],
        fetch_contracts=lambda v, d: [], fetch_orderbook=lambda t: {},
        implied_forecast=lambda v, d: None,
        place_order=lambda **k: placed.append(k), append_log=lambda r: None,
        notify=lambda *a, **k: True)
    out = trader.run_once(now=datetime(2026, 7, 27, 12, 0), deps=deps, station="KAUS")
    assert out == {"halted": "kill_switch"} and placed == []
