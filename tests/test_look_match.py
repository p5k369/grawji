"""Tests for the search for the recipe closest to an edit."""

from dataclasses import replace

import numpy as np
import pytest

from grawji import look_match
from grawji.camera.capabilities import Capabilities
from grawji.edit_align import Alignment
from grawji.look_match import LookTarget, SearchCancelledError, SearchHooks
from grawji.recipe import Recipe
from tests.image_support import scene

FRAME = (160, 112)
SIMS = ("Provia", "Velvia", "ClassicChrome", "Acros")
CAPS = Capabilities(film_simulations=SIMS, has_color_chrome=True)


def fake_render(recipe: Recipe) -> np.ndarray:
    """A stand-in camera: each control bends the picture its own way."""
    base = scene(FRAME).astype(np.float64) / 255
    linear = base**2.2 * 2 ** float(recipe.exposure)
    linear[..., 0] *= 1 + 0.05 * recipe.wb_shift_r
    linear[..., 2] *= 1 + 0.05 * recipe.wb_shift_b
    value = np.clip(linear, 0, 1) ** (1 / 2.2)
    value += 0.12 * recipe.shadows * (1 - value) ** 4
    value += 0.12 * recipe.highlights * value**4
    gray = value.mean(axis=-1, keepdims=True)
    saturation = {"Velvia": 1.4, "ClassicChrome": 0.7, "Acros": 0.0}.get(
        recipe.film_simulation, 1.0
    )
    if recipe.film_simulation != "Acros":
        saturation *= 1 + 0.08 * recipe.color
        if recipe.color_chrome == "Strong":
            saturation *= 1.15
    value = gray + (value - gray) * saturation
    return (np.clip(value, 0, 1) * 255).round().astype(np.uint8)


def target_for(edit_of: Recipe) -> LookTarget:
    """The fake render of a recipe, cropped, as the edit to match."""
    crop = np.eye(3)
    crop[0, 0] = crop[1, 1] = 1.5
    crop[:2, 2] = (-30.0, -20.0)
    size = (120, 80)
    edit = Alignment(crop, 1.0).warp(fake_render(edit_of), size)
    return LookTarget(edit, Alignment(crop, 1.0), FRAME)


def test_the_hidden_recipe_is_found() -> None:
    """The search recovers the recipe the edit was made with."""
    truth = Recipe(
        film_simulation="ClassicChrome",
        exposure=1 / 3,
        wb_shift_r=3,
        shadows=1.0,
        color=-1,
    )
    match = look_match.search(Recipe(), CAPS, fake_render, target_for(truth))
    found = match.recipe
    assert found.film_simulation == "ClassicChrome"
    assert found.exposure == pytest.approx(1 / 3)
    assert (found.wb_shift_r, found.shadows, found.color) == (3, 1.0, -1)
    assert match.distance < 0.5 < match.start_distance


def test_a_b_and_w_edit_finds_the_b_and_w_sim() -> None:
    """A B&W edit lands on the B&W film simulation."""
    truth = Recipe(film_simulation="Acros", highlights=2.0)
    match = look_match.search(Recipe(), CAPS, fake_render, target_for(truth))
    assert match.recipe.film_simulation == "Acros"
    assert match.recipe.highlights == 2.0


def test_grain_renders_off_and_comes_back() -> None:
    """Grain is off while rendering and the start's grain is kept."""
    seen: list[str] = []

    def render(recipe: Recipe) -> np.ndarray:
        seen.append(recipe.grain)
        return fake_render(recipe)

    start = Recipe(grain="Strong", grain_size="Large")
    match = look_match.search(start, CAPS, render, target_for(Recipe()))
    assert set(seen) == {"Off"}
    assert (match.recipe.grain, match.recipe.grain_size) == ("Strong", "Large")


def test_only_the_bodys_controls_are_tried() -> None:
    """Controls the body lacks are never rendered."""
    seen: list[Recipe] = []

    def render(recipe: Recipe) -> np.ndarray:
        seen.append(recipe)
        return fake_render(recipe)

    truth = Recipe(film_simulation="Velvia", shadows=1.0)
    look_match.search(Recipe(), CAPS, render, target_for(truth))
    assert {r.film_simulation for r in seen} <= set(SIMS)
    assert all(r.clarity == 0 for r in seen)
    assert all(r.color_chrome_blue == "Off" for r in seen)
    assert all(float(r.shadows).is_integer() for r in seen)
    assert all(float(r.highlights).is_integer() for r in seen)


def test_half_steps_are_tried_where_the_body_honors_them() -> None:
    """Half tone steps are tried on bodies that honor them."""
    seen: list[float] = []

    def render(recipe: Recipe) -> np.ndarray:
        seen.append(recipe.shadows)
        return fake_render(recipe)

    caps = replace(CAPS, tone_half_step=True)
    look_match.search(Recipe(), caps, render, target_for(Recipe(shadows=1.5)))
    assert 1.5 in seen


def test_a_cancelled_search_stops() -> None:
    """Cancelling stops the search before the next render."""
    renders = 0

    def render(recipe: Recipe) -> np.ndarray:
        nonlocal renders
        renders += 1
        return fake_render(recipe)

    with pytest.raises(SearchCancelledError):
        look_match.search(
            Recipe(),
            CAPS,
            render,
            target_for(Recipe(film_simulation="Velvia")),
            SearchHooks(cancelled=lambda: renders >= 3),
        )
    assert renders == 3


def test_progress_hears_each_better_recipe() -> None:
    """Progress hears every better recipe, ending with the answer."""
    heard: list[tuple[Recipe, int]] = []
    match = look_match.search(
        Recipe(),
        CAPS,
        fake_render,
        target_for(Recipe(film_simulation="Velvia", wb_shift_b=-2)),
        SearchHooks(
            progress=lambda recipe, renders: heard.append((recipe, renders))
        ),
    )
    assert len(heard) > 1
    assert heard[-1][0] == match.recipe
    counts = [renders for _recipe, renders in heard]
    assert counts == sorted(counts)


def test_a_render_of_another_size_is_refused() -> None:
    """A render that does not match the aligned frame is refused."""
    target = target_for(Recipe())
    with pytest.raises(ValueError, match="size"):
        target.distance(np.zeros((10, 10, 3), np.uint8))


def test_overlap_is_the_share_the_frame_covers() -> None:
    """A crop inside the frame is covered completely."""
    assert target_for(Recipe()).overlap == pytest.approx(1.0)


def test_lab_of_white_and_black() -> None:
    """White and black land on the ends of the Lab lightness axis."""
    values = look_match.lab(np.array([[255, 255, 255], [0, 0, 0]]))
    np.testing.assert_allclose(values, [[100, 0, 0], [0, 0, 0]], atol=0.01)


def test_stages_are_announced_in_order() -> None:
    """Each stage is announced once, in the order the search runs them."""
    stages: list[str] = []
    look_match.search(
        Recipe(),
        CAPS,
        fake_render,
        target_for(Recipe(film_simulation="Velvia")),
        SearchHooks(stage=stages.append),
    )
    assert stages == [
        look_match.STAGE_FILM,
        look_match.STAGE_LOOK,
        look_match.STAGE_FINE,
        look_match.STAGE_RECHECK,
    ]


def test_a_far_white_balance_does_not_hide_the_color_sim() -> None:
    """A color edit finds a color sim even from a strongly tinted start."""
    start = Recipe(film_simulation="Acros", wb_shift_r=-9, wb_shift_b=9)
    truth = Recipe(film_simulation="Velvia", wb_shift_r=4, wb_shift_b=-4)
    match = look_match.search(start, CAPS, fake_render, target_for(truth))
    assert match.recipe.film_simulation == "Velvia"
    assert match.distance < 0.5


def test_the_tuned_recipe_is_checked_against_every_sim() -> None:
    """After tuning, each film simulation is rendered once more."""
    seen: list[Recipe] = []

    def render(recipe: Recipe) -> np.ndarray:
        seen.append(recipe)
        return fake_render(recipe)

    truth = Recipe(film_simulation="Velvia", shadows=1.0, wb_shift_r=2)
    match = look_match.search(Recipe(), CAPS, render, target_for(truth))
    tuned = replace(match.recipe, film_simulation="Provia")
    assert any(
        replace(r, film_simulation="Provia") == tuned
        and r.film_simulation != match.recipe.film_simulation
        for r in seen
    )
