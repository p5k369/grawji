"""Tests for the texture comparison behind the recipe-from-edit search."""

from __future__ import annotations

import numpy as np
import pytest

from grawji.edit_align import Alignment
from grawji.imaging.blur import gaussian
from grawji.imaging.pixbufs import area_resize
from grawji.recipe import Recipe
from grawji.texture_match import WINDOW, TextureTarget, band_energies
from tests.image_support import scene

FRAME = (640, 448)
# A web-sized edit of a frame this big still holds a few windows.
LARGE_FRAME = (1600, 1120)
# The edit is the frame without this margin, at the frame's own scale.
MARGIN = (32, 24)
_GRAIN = {"Off": 0.0, "Weak": 0.04, "Strong": 0.08}


def crop_of(frame: tuple[int, int]) -> tuple[int, int, int, int]:
    """The crop of a frame the edit shows: x, y, width and height."""
    return (*MARGIN, frame[0] - 2 * MARGIN[0], frame[1] - 2 * MARGIN[1])


CROP = crop_of(FRAME)


def engine(
    recipe: Recipe, seed: int = 7, frame: tuple[int, int] = FRAME
) -> np.ndarray:
    """A stand-in camera whose texture controls do what the real ones do."""
    rng = np.random.default_rng(seed)
    base = gaussian(scene(frame, seed=3).astype(np.float64) / 255, 0.8)
    noise = (
        rng.normal(size=base.shape) * 0.03 * (1 - recipe.noise_reduction / 5)
    )
    image = base + noise
    image += 0.15 * recipe.sharpness * (image - gaussian(image, 1.0))
    grain = rng.normal(size=base.shape[:2]) * _GRAIN[recipe.grain]
    if recipe.grain_size == "Large":
        grain = gaussian(grain, 1.5) * 3.0
    image += grain[..., None]
    return (np.clip(image, 0, 1) * 255).round().astype(np.uint8)


def crop_alignment(scale: float = 1.0) -> Alignment:
    """Where the edit sits: the crop, with the edit scaled by this much."""
    matrix = np.eye(3)
    matrix[0, 0] = matrix[1, 1] = scale
    matrix[0, 2] = -MARGIN[0] * scale
    matrix[1, 2] = -MARGIN[1] * scale
    return Alignment(matrix, 1.0)


def edit_of(
    recipe: Recipe, scale: float = 1.0, frame: tuple[int, int] = FRAME
) -> np.ndarray:
    """The crop of the engine's render, the edit the tests match."""
    x, y, width, height = crop_of(frame)
    rendered = engine(recipe, seed=11, frame=frame)
    if scale == 1.0:
        return np.ascontiguousarray(rendered[y : y + height, x : x + width])
    return crop_alignment(scale).warp(
        rendered, (round(width * scale), round(height * scale))
    )


def target_for(
    recipe: Recipe, scale: float = 1.0, frame: tuple[int, int] = FRAME
) -> TextureTarget:
    """A target made as the controller makes one, at the aligned sizes."""
    edit = edit_of(recipe, scale, frame)
    return TextureTarget(
        edit, crop_alignment(scale), (edit.shape[1], edit.shape[0]), frame
    )


def test_band_energies_tell_noise_from_blur() -> None:
    """Noise raises the fine band, a blurred picture has little of it."""
    rng = np.random.default_rng(1)
    flat = np.full((WINDOW, WINDOW), 0.5)
    noisy = flat + rng.normal(size=flat.shape) * 0.05
    assert band_energies(flat) == pytest.approx((0.0, 0.0))
    fine, coarse = band_energies(noisy)
    assert fine > coarse > 0


def test_the_same_texture_scores_near_zero_with_other_grain() -> None:
    """Grain and noise random on every render do not count as a gap."""
    truth = Recipe(sharpness=1, noise_reduction=-2, grain="Strong")
    target = target_for(truth)
    assert target.compare(engine(truth, seed=5)) < 0.03
    assert len(target.windows) >= 4


def test_every_wrong_texture_control_scores_worse() -> None:
    """Moving any one of the texture controls away from the truth shows."""
    truth = Recipe(sharpness=1, noise_reduction=-2, grain="Weak")
    target = target_for(truth)
    own = target.compare(engine(truth, seed=5))
    for wrong in (
        Recipe(sharpness=3, noise_reduction=-2, grain="Weak"),
        Recipe(sharpness=-1, noise_reduction=-2, grain="Weak"),
        Recipe(sharpness=1, noise_reduction=2, grain="Weak"),
        Recipe(sharpness=1, noise_reduction=-2, grain="Off"),
        Recipe(sharpness=1, noise_reduction=-2, grain="Strong"),
        Recipe(
            sharpness=1, noise_reduction=-2, grain="Weak", grain_size="Large"
        ),
    ):
        assert target.compare(engine(wrong, seed=5)) > own + 0.01, wrong


def test_windows_cover_busy_and_flat_parts_inside_the_frame() -> None:
    """Windows lie inside the covered edit, some busy and some flat."""
    target = target_for(Recipe())
    target.compare(engine(Recipe()))
    windows = target.windows
    assert windows
    _x, _y, width, height = CROP
    for window in windows:
        assert 0 <= window.x <= width - WINDOW
        assert 0 <= window.y <= height - WINDOW
    fines = sorted(window.fine for window in windows)
    assert fines[-1] > fines[0]


def test_a_small_export_is_compared_at_its_own_scale() -> None:
    """A web-sized edit shrinks the render first and still compares."""
    truth = Recipe(grain="Strong", grain_size="Large")
    target = target_for(truth, scale=0.4, frame=LARGE_FRAME)
    own = target.compare(engine(truth, seed=5, frame=LARGE_FRAME))
    assert np.isfinite(own)
    assert target.windows
    assert target.compare(engine(Recipe(), seed=5, frame=LARGE_FRAME)) > own


def test_an_enlarged_crop_is_compared_at_the_renders_scale() -> None:
    """An edit with more pixels than the frame comes down to the frame."""
    truth = Recipe(sharpness=2)
    target = target_for(truth, scale=2.0)
    own = target.compare(engine(truth, seed=5))
    assert np.isfinite(own)
    assert target.windows


def test_a_render_of_another_size_is_refused() -> None:
    """Every render must be the size of the first."""
    target = target_for(Recipe())
    target.compare(engine(Recipe()))
    smaller = area_resize(engine(Recipe()), 320, 224)
    with pytest.raises(ValueError, match="size"):
        target.compare(smaller)


def test_an_edit_too_small_for_a_window_compares_as_equal() -> None:
    """Without a window to look at, nothing can tell textures apart."""
    edit = edit_of(Recipe())[:100, :100]
    matrix = np.eye(3)
    matrix[0, 2], matrix[1, 2] = -CROP[0], -CROP[1]
    target = TextureTarget(edit, Alignment(matrix, 1.0), (100, 100), FRAME)
    assert target.compare(engine(Recipe(sharpness=4))) == 0.0
    assert target.windows == []
