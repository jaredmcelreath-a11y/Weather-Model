"""The ~2-minute raw-METAR feed behind the live 'Current Temp' reading.

The 5-minute MADIS feed lands ~20 min late and in whole degrees Celsius, which
cannot represent 101F at all (38C = 100.4, 39C = 102.2). The routine :53 METAR
carries a Txxxxxxxx remark in TENTHS of a degree C and is published about two
minutes after the observation, so it is both fresher and finer.
"""
from datetime import datetime, timezone

from sources import metar_tgftp

# The real KAUS report for 2026-08-16 21:53Z -- the one that moved the market
# to the 102-103 bracket while the 5-minute feed still read 100.
_KAUS = ("2026/08/16 21:53\n"
         "KAUS 162153Z 17010KT 10SM CLR 39/15 A3003 RMK AO2 SLP154 "
         "T03890150 $\n")


def test_reads_the_t_group_in_tenths_not_the_whole_degree_body():
    stamp, temp = metar_tgftp.parse(_KAUS)
    # 38.9C, not the body's 39C -- the whole point of the feed.
    assert temp == 102.02
    assert stamp == datetime(2026, 8, 16, 21, 53, tzinfo=timezone.utc)


def test_a_negative_t_group_reads_below_zero():
    # The leading digit of each half is a SIGN, not part of the value: a winter
    # low read as +2.6 instead of -2.6 would be a 5F error in the wrong season.
    text = ("2026/01/12 12:53\n"
            "KDFW 121253Z 36010KT 10SM CLR M03/M09 A3025 RMK AO2 T10261089\n")
    _stamp, temp = metar_tgftp.parse(text)
    assert temp == 27.32          # -2.6C


def test_falls_back_to_the_body_when_there_is_no_t_group():
    # Some reports omit the remark. Whole-degree C is worse than tenths but far
    # better than showing nothing.
    text = ("2026/08/16 21:53\n"
            "KAUS 162153Z 17010KT 10SM CLR 39/15 A3003 RMK AO2 SLP154\n")
    _stamp, temp = metar_tgftp.parse(text)
    assert temp == 102.2          # 39C


def test_a_negative_body_temperature_reads_below_zero():
    text = ("2026/01/12 12:53\n"
            "KDFW 121253Z 36010KT 10SM CLR M03/M09 A3025 RMK AO2\n")
    _stamp, temp = metar_tgftp.parse(text)
    assert temp == 26.6           # -3C


def test_unreadable_text_is_none_rather_than_an_exception():
    # A dead or reshaped upstream must degrade to the existing 5-minute feed,
    # never take the dashboard down.
    assert metar_tgftp.parse("") is None
    assert metar_tgftp.parse("<html>404</html>") is None
    assert metar_tgftp.parse("2026/08/16 21:53\n") is None


def test_the_timestamp_is_utc_so_it_can_be_compared_with_the_5_minute_feed():
    stamp, _temp = metar_tgftp.parse(_KAUS)
    assert stamp.utcoffset().total_seconds() == 0


def test_latest_returns_the_reading_in_the_station_timezone():
    # snapshot compares this against the 5-minute feed's last reading, which is
    # station-local, so a naive or UTC value would compare wrongly.
    got = metar_tgftp.latest(station="KAUS", fetch=lambda url: _KAUS)
    stamp, temp = got
    assert temp == 102.02
    assert stamp.utcoffset().total_seconds() == -5 * 3600   # CDT in August
    assert (stamp.hour, stamp.minute) == (16, 53)


def test_latest_asks_for_the_station_id_not_the_config_code():
    urls = []

    def spy(url):
        urls.append(url)
        return _KAUS

    metar_tgftp.latest(station="KAUS", fetch=spy)
    assert urls == ["https://tgftp.nws.noaa.gov/data/observations/metar/"
                    "stations/KAUS.TXT"]


def test_latest_swallows_a_dead_upstream():
    def boom(url):
        raise RuntimeError("tgftp down")

    assert metar_tgftp.latest(station="KAUS", fetch=boom) is None
