"""The accuracy panel's exclusion note: visible only when the correction
estimators actually dropped flagged records, so a changed correction is
explainable instead of a silent mystery."""

from config import CALIBRATION_WINDOW_DAYS
from market_view import exclusion_note


def test_note_hidden_when_nothing_excluded():
    assert exclusion_note(0) is None


def test_note_names_count_and_window():
    note = exclusion_note(3)
    assert "3" in note and str(CALIBRATION_WINDOW_DAYS) in note
    assert "storm/front-flagged" in note
