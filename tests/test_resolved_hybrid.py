"""The two extra Resolved formulations for the live three-way comparison."""
import model


def test_pre_obs_is_zero():
    # Pre-observation: locked_ratio == 1.0, collapse == 0.0.
    assert model._resolved_variants(1.0, 0.0) == (0.0, 0.0)


def test_orig_is_one_minus_locked_ratio():
    orig, _ = model._resolved_variants(0.3, 0.4)
    assert orig == 0.7


def test_hybrid_hits_one_when_fully_converged():
    # locked_ratio -> 0 means the ensemble has fully collapsed.
    _, hybrid = model._resolved_variants(0.0, 0.0)
    assert hybrid == 1.0


def test_hybrid_hits_one_when_fully_ruled_out():
    # collapse -> 1 means observations have ruled out all other mass.
    _, hybrid = model._resolved_variants(0.5, 1.0)
    assert hybrid == 1.0


def test_hybrid_beats_collapse_alone_at_peak_near_mean():
    # Peak landing at the forecast mean -> collapse ~0.5, but the ensemble is
    # half-converged, so the hybrid still resolves past 0.5.
    _, hybrid = model._resolved_variants(0.5, 0.5)
    assert hybrid == 0.75
    assert hybrid > 0.5
