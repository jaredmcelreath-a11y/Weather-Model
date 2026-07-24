import trade_log


def test_build_record_has_ts_and_kind():
    r = trade_log.build_record("entry", ticker="A", side="yes", count=1)
    assert r["kind"] == "entry" and r["ticker"] == "A"
    assert "ts" in r
