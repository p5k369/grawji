"""Tests for finding an edited export in the camera's frame."""

import math

import numpy as np
import pytest

from grawji import edit_align
from grawji.edit_align import Alignment
from tests.image_support import scene

FRAME = (480, 320)
EDIT = (300, 200)
# The recovered placement may be off by this many edit pixels.
TOLERANCE = 3.0


def crop_matrix(
    center: tuple[float, float], scale: float, angle: float
) -> np.ndarray:
    """Frame pixels to edit pixels for a straightened, scaled crop."""
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    to_frame = np.array(
        [
            [scale * cos, -scale * sin, 0.0],
            [scale * sin, scale * cos, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    edit_center = np.array([(EDIT[0] - 1) / 2, (EDIT[1] - 1) / 2])
    to_frame[:2, 2] = np.array(center) - to_frame[:2, :2] @ edit_center
    return np.linalg.inv(to_frame)


def placement_error(truth: np.ndarray, found: Alignment) -> float:
    """How far apart the two placements put the edit's corners."""
    corners = np.array(
        [[0, EDIT[0] - 1, 0, EDIT[0] - 1], [0, 0, EDIT[1] - 1, EDIT[1] - 1]]
    )
    points = np.vstack([corners, np.ones(4)])
    in_frame = np.linalg.inv(truth) @ points
    return float(np.hypot(*(found.matrix @ in_frame - points)[:2]).max())


def graded(edit: np.ndarray) -> np.ndarray:
    """A strong tone and color edit: B&W with a steep curve."""
    gray = edit.astype(np.float64) @ [0.3, 0.6, 0.1] / 255
    curve = np.clip((gray - 0.2) * 1.6, 0, 1) ** 0.8
    return np.repeat((curve * 255).astype(np.uint8)[..., None], 3, axis=-1)


def test_a_straightened_crop_is_found() -> None:
    """A crop turned by a few degrees and scaled is placed exactly."""
    frame = scene(FRAME)
    truth = crop_matrix((250.0, 150.0), 1.2, 3.0)
    edit = Alignment(truth, 1.0).warp(frame, EDIT)
    found = edit_align.align(frame, edit)
    assert found is not None
    assert placement_error(truth, found) < TOLERANCE


def test_a_graded_quarter_turned_crop_is_found() -> None:
    """A B&W, steeply graded portrait crop of a landscape frame is placed."""
    frame = scene(FRAME, 2)
    truth = crop_matrix((240.0, 170.0), 0.9, 90.0)
    edit = graded(Alignment(truth, 1.0).warp(frame, EDIT))
    found = edit_align.align(frame, edit)
    assert found is not None
    assert placement_error(truth, found) < TOLERANCE


def test_a_framing_border_is_looked_past() -> None:
    """A flat border around the edit does not throw the placement off."""
    frame = scene(FRAME, 3)
    truth = crop_matrix((240.0, 160.0), 1.4, 0.0)
    inner = Alignment(truth, 1.0).warp(frame, EDIT)
    pad = 30
    edit = np.full((EDIT[1] + 2 * pad, EDIT[0] + 2 * pad, 3), 250, np.uint8)
    edit[pad:-pad, pad:-pad] = inner
    found = edit_align.align(frame, edit)
    assert found is not None
    shift = np.eye(3)
    shift[:2, 2] = pad
    assert placement_error(shift @ truth, found) < TOLERANCE


def test_another_shot_is_not_aligned() -> None:
    """An edit of a different shot finds no placement."""
    edit = Alignment(crop_matrix((240.0, 160.0), 1.2, 0.0), 1.0).warp(
        scene(FRAME, 4), EDIT
    )
    assert edit_align.align(scene(FRAME, 5), edit) is None


def test_coverage_marks_what_the_frame_shows() -> None:
    """Only edit pixels inside the frame count as covered."""
    # Half the edit lies left of the frame.
    shift = np.eye(3)
    shift[0, 2] = EDIT[0] / 2
    covered = Alignment(shift, 1.0).coverage(FRAME, EDIT)
    assert covered[:, : EDIT[0] // 2 - 1].sum() == 0
    assert covered[:, EDIT[0] // 2 + 1 :].all()


@pytest.mark.parametrize(
    ("sides", "expected"),
    [((0, 0, 0, 0), (0, 0, 0, 0)), ((5, 7, 11, 3), (5, 7, 11, 3))],
)
def test_border_measures_each_side(
    sides: tuple[int, int, int, int], expected: tuple[int, int, int, int]
) -> None:
    """Each side of a flat border is measured on its own."""
    top, bottom, left, right = sides
    inner = scene(FRAME)[:100, :120]
    image = np.full((100 + top + bottom, 120 + left + right, 3), 255, np.uint8)
    image[top : top + 100, left : left + 120] = inner
    assert edit_align.border(image) == expected


def test_an_uncropped_export_is_found() -> None:
    """An edit showing the whole frame, a hair wider, is still placed."""
    frame = scene(FRAME, 6)
    truth = crop_matrix((239.5, 159.5), 1.61, 0.0)
    edit = Alignment(truth, 1.0).warp(frame, EDIT)
    found = edit_align.align(frame, edit)
    assert found is not None
    assert placement_error(truth, found) < TOLERANCE
