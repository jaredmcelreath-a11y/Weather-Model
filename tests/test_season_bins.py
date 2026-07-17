"""Season readiness: self-describing bin labels + the tail abstain guard.

See docs/superpowers/specs/2026-07-17-season-readiness-design.md
"""
import model


def test_bin_temp_parses_legacy_tail_labels():
    # A row logged under the old 60..110 range must keep its original meaning
    # even after the range widens — bin_temp reads the label, not the config.
    assert model.bin_temp("<= 60") == 60
    assert model.bin_temp(">= 110") == 110


def test_bin_temp_parses_new_tail_labels():
    assert model.bin_temp("<= -10") == -10
    assert model.bin_temp(">= 115") == 115


def test_bin_temp_parses_interior_label():
    assert model.bin_temp("90") == 90


def test_bin_temp_ignores_config_range():
    # The whole point: changing the config must not change what a label means.
    original = (model.BIN_LOW, model.BIN_HIGH)
    try:
        model.BIN_LOW, model.BIN_HIGH = -99, 999
        assert model.bin_temp("<= 60") == 60
        assert model.bin_temp(">= 110") == 110
    finally:
        model.BIN_LOW, model.BIN_HIGH = original
