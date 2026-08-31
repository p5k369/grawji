"""Tests for the auto-level suggestions."""

import math

import pytest

from grawji import level
from grawji.crop import FULL_RECT, level_delta, reframe_point

WIDTH = 400
HEIGHT = 300


def edge_rows(tilt_deg: float, *, vertical: bool = False) -> list[list[int]]:
    """A soft step edge tilted tilt_deg away from an axis."""
    slope = math.tan(math.radians(tilt_deg))
    rows = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            if vertical:
                distance = x - (WIDTH / 2 + slope * y)
            else:
                distance = y - (HEIGHT / 2 + slope * x)
            shade = min(1.0, max(0.0, 0.5 + distance / 2.0))
            row.append(round(765 * shade))
        rows.append(row)
    return rows


def two_family_rows() -> list[list[int]]:
    """A strong edge tilted 3 degrees plus a weaker level edge."""
    slope = math.tan(math.radians(3.0))
    rows = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            strong = y - (HEIGHT * 0.3 + slope * x)
            weak = y - HEIGHT * 0.72
            value = 600 * min(1.0, max(0.0, 0.5 + strong / 2.0))
            value += 165 * min(1.0, max(0.0, 0.5 + weak / 2.0))
            row.append(round(value))
        rows.append(row)
    return rows


def test_horizontal_tilt_positive():
    """A horizon dropping to the right suggests the countering angle."""
    delta = level.suggest_delta(edge_rows(3.0))
    expected = level_delta(1.0, math.tan(math.radians(3.0)))
    assert delta is not None
    assert abs(delta - expected) < 0.1


def test_horizontal_tilt_negative():
    """A horizon rising to the right suggests the opposite sign."""
    delta = level.suggest_delta(edge_rows(-2.4))
    expected = level_delta(1.0, math.tan(math.radians(-2.4)))
    assert delta is not None
    assert abs(delta - expected) < 0.1


def test_already_level():
    """A level horizon suggests near zero."""
    delta = level.suggest_delta(edge_rows(0.0))
    assert delta is not None
    assert abs(delta) < 0.1


def test_vertical_structure():
    """Tilted verticals level via the vertical axis, like level_delta."""
    delta = level.suggest_delta(edge_rows(2.0, vertical=True))
    slope = math.tan(math.radians(2.0))
    expected = level_delta(slope, 1.0)
    assert delta is not None
    assert abs(delta - expected) < 0.1


def test_flat_image_has_no_suggestion():
    """No edges means no suggestion, not a zero."""
    rows = [[400] * WIDTH for _ in range(HEIGHT)]
    assert level.suggest_delta(rows) is None
    assert level.suggest_deltas(rows) == []


def test_strong_diagonal_is_ignored():
    """A 30-degree line is intentional, outside the leveling window."""
    assert level.suggest_delta(edge_rows(30.0)) is None


def test_tiny_input_has_no_suggestion():
    """Degenerate sizes return None instead of raising."""
    assert level.suggest_delta([]) is None
    assert level.suggest_delta([[0, 0], [0, 0]]) is None


@pytest.mark.parametrize("tilt", [0.5, 1.0, 5.0, 10.0])
def test_precision_across_the_window(tilt):
    """Position-fitted segments stay within a tenth of a degree."""
    delta = level.suggest_delta(edge_rows(tilt))
    assert delta is not None
    assert abs(delta + tilt) < 0.1


def test_single_edge_yields_one_candidate():
    """One line means one suggestion, no shoulder ghosts."""
    assert len(level.suggest_deltas(edge_rows(3.0))) == 1


def test_two_families_ranked():
    """A dominant tilted line and a weaker level one both surface."""
    deltas = level.suggest_deltas(two_family_rows())
    assert len(deltas) == 2
    assert abs(deltas[0] + 3.0) < 0.1
    assert abs(deltas[1]) < 0.1


def test_candidates_carry_segments_along_the_edge():
    """The winning candidate's marking follows the detected line."""
    candidates = level.suggest_candidates(edge_rows(3.0))
    assert len(candidates) == 1
    best = candidates[0]
    assert best.segments
    x0, y0, x1, y1 = best.segments[0]
    assert abs(x1 - x0) > 0.8
    assert abs((y0 + y1) / 2 - 0.5) < 0.05
    for value in (x0, y0, x1, y1):
        assert -0.1 <= value <= 1.1


def test_marked_lines_level_out_after_applying():
    """Applying the suggestion makes the marked segments level."""
    candidates = level.suggest_candidates(edge_rows(3.0))
    best = candidates[0]
    new_angle = 0.0 + best.delta
    x0, y0, x1, y1 = best.segments[0]
    ax, ay = reframe_point((x0, y0), WIDTH, HEIGHT, 0.0, FULL_RECT, new_angle)
    bx, by = reframe_point((x1, y1), WIDTH, HEIGHT, 0.0, FULL_RECT, new_angle)
    rise = abs(by - ay) * HEIGHT
    run = abs(bx - ax) * WIDTH
    assert run > WIDTH * 0.5
    assert rise / run < math.tan(math.radians(0.15))
