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
