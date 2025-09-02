"""Tests for Phase 3 configuration additions (soft limits + smoothing)."""

import pytest

from src.config import GlobalSettings


def test_allocation_strategy_soft_allowed():
    gs = GlobalSettings(allocation_strategy="soft")
    assert gs.allocation_strategy == "soft"


def test_phase3_fields_defaults_and_ranges():
    gs = GlobalSettings()

    # Defaults exist
    assert isinstance(gs.borrow_threshold_ratio, float)
    assert isinstance(gs.max_borrow_fraction, float)
    assert isinstance(gs.smoothing_alpha, float)
    assert isinstance(gs.min_effective_delta, float)

    # In-range by default
    assert 0.5 <= gs.borrow_threshold_ratio <= 1.0
    assert 0.0 <= gs.max_borrow_fraction <= 1.0
    assert 0.0 <= gs.smoothing_alpha <= 1.0
    assert 0.0 <= gs.min_effective_delta <= 1.0


@pytest.mark.parametrize(
    "field, value",
    [
        ("borrow_threshold_ratio", 0.4),  # below min 0.5
        ("borrow_threshold_ratio", 1.5),  # above max 1.0
        ("max_borrow_fraction", -0.1),
        ("max_borrow_fraction", 1.1),
        ("smoothing_alpha", -0.01),
        ("smoothing_alpha", 1.01),
        ("min_effective_delta", -0.01),
        ("min_effective_delta", 1.5),
    ],
)
def test_phase3_field_validation(field, value):
    kwargs = {field: value}
    with pytest.raises(ValueError):
        GlobalSettings(**kwargs)
