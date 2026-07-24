from datetime import datetime
from zoneinfo import ZoneInfo

import trade_params as tp


def test_defaults_are_safe():
    d = tp.DEFAULT_PARAMS
    assert d["kill_switch"] is True          # engaged = trading disabled
    assert d["mode"] == "shadow"
    assert d["max_price"] == 0.94
    assert d["min_resolved"] == 0.70
    assert d["agreement_tol"] == 1.0
    assert d["per_market_cap"] == 0.50
    assert d["max_open_per_variable"] == 1


def test_merge_fills_defaults_and_drops_unknown():
    merged = tp.merge_params({"max_price": 0.80, "bogus": 1})
    assert merged["max_price"] == 0.80       # override kept
    assert merged["min_resolved"] == 0.70    # default filled
    assert "bogus" not in merged             # unknown dropped


def test_merge_coerces_types():
    merged = tp.merge_params({"kill_switch": "false", "per_market_cap": "0.5"})
    assert merged["kill_switch"] is False
    assert merged["per_market_cap"] == 0.5


def test_merge_none_is_all_defaults():
    assert tp.merge_params(None) == tp.DEFAULT_PARAMS


def test_market_window_true_inside_false_outside():
    p = tp.merge_params({"market_open": "06:00", "market_close": "20:00"})
    ct = ZoneInfo("America/Chicago")
    assert tp.within_market_window(datetime(2026, 7, 24, 12, 0, tzinfo=ct), p) is True
    assert tp.within_market_window(datetime(2026, 7, 24, 5, 0, tzinfo=ct), p) is False
    assert tp.within_market_window(datetime(2026, 7, 24, 21, 0, tzinfo=ct), p) is False


def test_market_window_converts_utc():
    p = tp.merge_params({})
    utc = ZoneInfo("UTC")
    # 12:00 UTC == 07:00 CDT (inside default 06:00-20:00)
    assert tp.within_market_window(datetime(2026, 7, 24, 12, 0, tzinfo=utc), p) is True
