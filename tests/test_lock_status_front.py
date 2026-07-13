"""Display-layer reconciliation for the front guard: when forecast members
project an evening undercut of the locked low, the badge and Resolved metric
must stop claiming the low is settled (mirrors test_lock_status_convective)."""

from market_view import lock_status, displayed_resolved


def _low(**over):
    d = {
        "locked_ratio": 0.3,
        "resolved": 1.0,          # the low's time window closed at 9am
        "observed_so_far": 78.0,
        "consensus": 76.0,        # members project a colder evening
        "sigma_used": 2.1,
        "peak_locked": True,
        "convective_widened": False,
        "front_widened": True,
    }
    d.update(over)
    return d


def test_front_widened_downgrades_lock():
    level, headline, detail = lock_status(_low(), "low")
    assert level == "warning"
    assert "front" in (headline + detail).lower()
    assert "prime buy window" not in detail.lower()


def test_no_front_still_green():
    level, headline, _ = lock_status(_low(front_widened=False, consensus=78.0,
                                          locked_ratio=0.0, sigma_used=0.7), "low")
    assert level == "success"
    assert headline == "Locked — Dawn Trough Is In"


def test_displayed_resolved_capped_on_front():
    assert displayed_resolved(_low()) <= 90
    assert displayed_resolved(_low(front_widened=False)) == 100


def test_convective_badge_still_wins_when_both():
    # A stormy front day can set both flags; either warning is fine, but the
    # level must be warning and the box must not read as settled.
    level, _, detail = lock_status(_low(convective_widened=True), "low")
    assert level == "warning"
    assert "prime buy window" not in detail.lower()
