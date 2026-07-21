"""alerts.py — state load/save + pure message builders."""
import alerts


def test_load_state_missing_empty_corrupt(tmp_path):
    p = tmp_path / "s.json"
    assert alerts.load_state(str(p)) == {}          # missing
    p.write_text("")
    assert alerts.load_state(str(p)) == {}          # empty
    p.write_text("{not json")
    assert alerts.load_state(str(p)) == {}          # corrupt
    p.write_text('{"storm": "2026-07-21"}')
    assert alerts.load_state(str(p)) == {"storm": "2026-07-21"}


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    alerts.save_state(p, {"recap": "2026-07-21"})
    assert alerts.load_state(p) == {"recap": "2026-07-21"}


def test_storm_body_with_upstream_warning():
    storm = {"level": "active", "sigma": 3.0,
             "upstream": {"active": True, "county": "Tarrant", "direction": "NW"}}
    body = alerts.storm_body(storm)
    assert "Tarrant Co (NW)" in body
    assert "±3°F" in body


def test_storm_body_without_upstream():
    storm = {"level": "active", "sigma": 2.0,
             "upstream": {"active": False, "county": None, "direction": None}}
    body = alerts.storm_body(storm)
    assert "approach" in body.lower()
    assert "±2°F" in body


def test_front_body_uses_projection_then_consensus():
    low = {"consensus": 80.0, "front_guard": {"projection": 77.0}}
    assert "≈77°F" in alerts.front_body(low)
    assert "≈80°F" in alerts.front_body({"consensus": 80.0})  # no front_guard


def test_recap_body_yesterday_and_today():
    setup = {"high": {"consensus": 101.0, "locked": False},
             "low": {"observed": 80.0, "consensus": 80.0, "locked": True}}
    yesterday = {"high": {"settled": 100, "model": 99, "exact": False},
                 "low": {"settled": 80, "model": 80, "exact": True}}
    body = alerts.recap_body(setup, yesterday)
    assert "Yesterday:" in body
    assert "High 100 (model 99, Miss +1)" in body
    assert "Low 80 (model 80, Exact" in body
    assert "Today: Low ~80 (Locked), High ~101" in body


def test_recap_body_today_only_when_no_yesterday():
    setup = {"high": {"consensus": 101.0, "locked": False},
             "low": {"observed": None, "consensus": 79.0, "locked": False}}
    body = alerts.recap_body(setup, None)
    assert "Yesterday" not in body
    assert "Today: Low ~79 (Developing), High ~101" in body


def test_recap_body_empty_without_setup():
    assert alerts.recap_body(None, None) == ""
