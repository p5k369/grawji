"""Tests for the navigator's pure rotation mapping."""

from __future__ import annotations

import pytest

from grawji.navigator_geometry import POINT_ROTATIONS, RECT_ROTATIONS

ROTATIONS = [90, 180, 270]
POINTS = [(0.0, 0.0), (1.0, 1.0), (0.25, 0.75), (0.5, 0.5), (0.1, 0.9)]


@pytest.mark.parametrize("rot", ROTATIONS)
def test_full_rect_stays_full(rot):
    """The whole visible image maps to the whole thumbnail, any rotation."""
    assert RECT_ROTATIONS[rot](0.0, 0.0, 1.0, 1.0) == pytest.approx(
        (0.0, 0.0, 1.0, 1.0)
    )


@pytest.mark.parametrize("rot", ROTATIONS)
@pytest.mark.parametrize(("a", "b"), POINTS)
def test_point_is_inverse_of_rect(rot, a, b):
    """A thumbnail point mapped into preview space and back is unchanged."""
    px, py = POINT_ROTATIONS[rot](a, b)
    back = RECT_ROTATIONS[rot](px, py, 0.0, 0.0)
    assert back[0] == pytest.approx(a)
    assert back[1] == pytest.approx(b)


@pytest.mark.parametrize("rot", ROTATIONS)
def test_quarter_turns_swap_width_and_height(rot):
    """90 and 270 swap the axes; 180 keeps them."""
    _, _, w, h = RECT_ROTATIONS[rot](0.1, 0.2, 0.3, 0.4)
    if rot == 180:
        assert (w, h) == pytest.approx((0.3, 0.4))
    else:
        assert (w, h) == pytest.approx((0.4, 0.3))


def test_rect_90_maps_left_strip_to_bottom():
    """Preview left half (full height) -> thumbnail bottom half (full width)."""
    assert RECT_ROTATIONS[90](0.0, 0.0, 0.5, 1.0) == pytest.approx(
        (0.0, 0.5, 1.0, 0.5)
    )
