"""Display-layer reconciliation: on a convective-downside day the low's lock
badge and the Resolved metric must not claim the low is settled, so they stop
contradicting the "evening storms could set a new low" caption (convective.py).
"""

from market_view import lock_status, displayed_resolved


def _low(**over):
    d = {
        "locked_ratio": 0.0,      # dawn trough observationally in
        "resolved": 1.0,
        "observed_so_far": 79.0,
        "consensus": 79.0,
        "sigma_used": 1.6,
        "peak_locked": True,
        "convective_widened": False,
    }
    d.update(over)
    return d


def test_low_convective_widened_downgrades_lock():
    """Even with the dawn trough in, an open evening-storm tail means the low is
    NOT a prime buy window — the badge drops out of the green success state."""
    level, headline, detail = lock_status(_low(convective_widened=True), "low")
    assert level == "warning"
    assert "storm" in detail.lower() or "storm" in headline.lower()
    assert "prime buy window" not in detail.lower()


def test_low_without_convective_still_locks():
    """Storm-free day is unchanged: dawn trough in -> green Locked / prime window."""
    level, headline, _ = lock_status(_low(convective_widened=False), "low")
    assert level == "success"
    assert headline == "Locked — Dawn Trough Is In"


def test_displayed_resolved_clamped_on_convective():
    assert displayed_resolved(_low(convective_widened=True)) <= 90
    assert displayed_resolved(_low(convective_widened=False)) == 100


def test_high_unaffected():
    """convective_widened is only ever set on the low (model.py gates on the
    variable), so the high path is untouched: dawn-in high still locks green."""
    d = {
        "locked_ratio": 0.0,
        "resolved": 1.0,
        "observed_so_far": 99.0,
        "consensus": 99.0,
        "sigma_used": 1.0,
        "peak_locked": True,
        "convective_widened": False,
    }
    level, headline, _ = lock_status(d, "high")
    assert level == "success"
    assert displayed_resolved(d) == 100
