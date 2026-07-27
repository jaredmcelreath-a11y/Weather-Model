"""displayed_resolved lives in model (pure, no Streamlit)."""
import model


def _d(resolved, conv=False, front=False):
    return {"resolved": resolved, "convective_widened": conv, "front_widened": front}


def test_full_window_is_100():
    assert model.displayed_resolved(_d(1.0)) == 100


def test_capped_on_convective_or_front():
    assert model.displayed_resolved(_d(1.0, conv=True)) == model.CONVECTIVE_RESOLVED_CAP
    assert model.displayed_resolved(_d(1.0, front=True)) == model.CONVECTIVE_RESOLVED_CAP
    assert model.CONVECTIVE_RESOLVED_CAP == 90


def test_partial_uncapped():
    assert model.displayed_resolved(_d(0.72)) == 72


def test_default_is_current_and_unchanged():
    d = {"resolved": 0.72}
    assert model.displayed_resolved(d) == 72
    assert model.displayed_resolved(d, "current") == 72


def test_hybrid_reads_hybrid_field_and_is_capped():
    d = {"resolved_hybrid": 1.0, "convective_widened": True}
    assert model.displayed_resolved(d, "hybrid") == model.CONVECTIVE_RESOLVED_CAP


def test_low_forming_no_longer_caps_the_card():
    # The dawn-low-forming 50% card cap was removed 2026-07-26; the card shows
    # the true % (the "still forming" badge + sigma floor still hedge).
    d = {"resolved_hybrid": 1.0, "low_forming": True}
    assert model.displayed_resolved(d, "hybrid") == 100
    assert model.displayed_resolved({"resolved": 0.9, "low_forming": True}) == 90


def test_original_reads_orig_field_and_is_uncapped():
    d = {"resolved_orig": 1.0, "convective_widened": True, "low_forming": True}
    assert model.displayed_resolved(d, "original") == 100
