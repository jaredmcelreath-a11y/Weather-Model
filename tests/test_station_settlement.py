from datetime import date

import settlement
import settlements


def test_settlement_bounds_station_tz():
    # KDFW default unchanged: fixed LST (UTC−6).
    s, e = settlement.local_day_bounds(date(2026, 7, 1))
    assert s.utcoffset().total_seconds() == -6 * 3600
    # KAUS shares the climate tz today, but the call must accept a station.
    s2, _ = settlement.local_day_bounds(date(2026, 7, 1), station="KAUS")
    assert s2.utcoffset().total_seconds() == -6 * 3600


def test_climate_day_of_accepts_station():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    moment = datetime(2026, 7, 20, 0, 30, tzinfo=ZoneInfo("America/Chicago"))
    assert settlement.climate_day_of(moment, station="KAUS") == date(2026, 7, 19)


def test_settlements_path_by_station():
    # KAUS reads its namespaced (absent) file -> [].
    assert settlements.load(station="KAUS") == []
    # KDFW still reads the legacy bare file.
    assert isinstance(settlements.load(station="KDFW"), list)
    # as_map likewise routes by station.
    assert settlements.as_map("cli", station="KAUS") == {}
