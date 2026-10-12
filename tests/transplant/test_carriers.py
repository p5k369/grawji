"""Tests for choosing the carrier that fits a donor's dynamic range."""

import pytest

from grawji.transplant.carriers import select_carrier


def test_select_carrier_prefers_the_exact_dr():
    """A matching carrier needs no compensation at all."""
    choice = select_carrier(400, {100: "c100", 400: "c400"})
    assert choice.carrier == "c400"
    assert choice.dynamic_range == 400
    assert choice.exposure_scale == 1.0
    assert choice.ev_offset == 0.0


def test_select_carrier_scales_into_a_higher_carrier():
    """A lower-DR donor is raw-scaled into the single DR400 carrier."""
    choice = select_carrier(100, {400: "c400"})
    assert choice.carrier == "c400"
    assert choice.exposure_scale == 0.25
    assert choice.ev_offset == 0.0
    assert select_carrier(200, {400: "c400"}).exposure_scale == 0.5


def test_select_carrier_prefers_scaling_over_the_ev_fallback():
    """With both directions available, the higher carrier wins."""
    choice = select_carrier(200, {100: "c100", 400: "c400"})
    assert choice.carrier == "c400"
    assert choice.exposure_scale == 0.5
    assert choice.ev_offset == 0.0


def test_select_carrier_falls_back_to_ev_on_a_lower_carrier():
    """Only a lower carrier available means the EV compensation."""
    choice = select_carrier(400, {100: "c100"})
    assert choice.carrier == "c100"
    assert choice.exposure_scale == 1.0
    assert choice.ev_offset == 2.0


def test_select_carrier_rejects_unknown_dr_and_empty_set():
    """Non-DR-class values and an empty carrier set are errors."""
    with pytest.raises(ValueError, match="unknown dynamic range"):
        select_carrier(250, {100: "c100"})
    with pytest.raises(ValueError, match="unknown dynamic range"):
        select_carrier(100, {250: "c250"})
    with pytest.raises(ValueError, match="no carrier"):
        select_carrier(100, {})
