"""The station audit's title parser and its Kalshi pacing.

Only the pure parts. The rest of the script is network I/O by design — it is
the audit of last resort, so it deliberately asks the two upstreams rather than
trusting anything this repo has logged.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import verify_city_stations as vcs   # noqa: E402


def test_the_three_wordings_kalshi_uses():
    # Kalshi words a settled bracket three ways and the audit has to read all
    # of them. A wording it cannot parse scores the city ZERO days, which the
    # report prints as "UNVERIFIED" -- the one outcome that looks like a data
    # problem rather than a wrong station, and so gets ignored.
    assert vcs._target("Will the high in Chicago be 86-87?") == (86, 87)
    assert vcs._target("Will the low in Denver be >71?") == (72, 999)
    assert vcs._target("Will the low in Boston be <64?") == (-999, 63)


def test_a_tail_excludes_its_own_strike():
    # ">71" pays on 72 and above, not on 71. Off by one here silently forgives
    # a station that is a degree out, which is exactly the error being hunted.
    lo, hi = vcs._target("be >71")
    assert lo == 72
    lo, hi = vcs._target("be <64")
    assert hi == 63


def test_an_unreadable_title_returns_none_rather_than_a_guess():
    assert vcs._target("Will it rain in Chicago?") is None
    assert vcs._target("") is None


def test_the_kalshi_loop_is_paced():
    # 40 series x 2 statuses is 80 back-to-back calls. The scanner measured 51
    # unpaced series losing 21 to HTTP 429; this script's single retry would not
    # survive that, and a rate-limited run reports "UNVERIFIED" for real cities.
    assert vcs.REQUEST_SPACING_S >= 0.5
