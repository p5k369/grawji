"""Tests for the search for the recipe closest to an edit."""

from dataclasses import replace

import numpy as np
import pytest

from grawji import look_match
from grawji.camera.capabilities import Capabilities
from grawji.edit_align import Alignment
from grawji.imaging.blur import gaussian
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
    assert match.distance < 0.5


def test_a_b_and_w_edit_finds_the_b_and_w_sim() -> None:
    """A B&W edit lands on the B&W film simulation."""
    truth = Recipe(film_simulation="Acros", highlights=2.0)
    match = look_match.search(Recipe(), CAPS, fake_render, target_for(truth))
    assert match.recipe.film_simulation == "Acros"
    assert match.recipe.highlights == 2.0


def test_grain_renders_off_and_the_texture_stays_neutral() -> None:
    """Without a texture search the texture controls come back neutral."""
    seen: list[str] = []

    def render(recipe: Recipe) -> np.ndarray:
        seen.append(recipe.grain)
        return fake_render(recipe)

    start = Recipe(
        grain="Strong",
        grain_size="Large",
        sharpness=-3,
        noise_reduction=-4,
        smooth_skin="Strong",
        color_space="AdobeRGB",
    )
    match = look_match.search(start, CAPS, render, target_for(Recipe()))
    assert set(seen) == {"Off"}
    found = match.recipe
    assert (found.grain, found.grain_size) == ("Off", "Small")
    assert (found.sharpness, found.noise_reduction) == (0, 0)
    assert found.smooth_skin == "Off"
    assert found.color_space == "AdobeRGB"


class TextureEngine:
    """A full-size camera whose texture is read straight off the render."""

    def __init__(self) -> None:
        """Count the renders."""
        self.rendered: list[Recipe] = []

    def render(self, recipe: Recipe) -> np.ndarray:
        """A one-pixel render carrying the texture controls."""
        self.rendered.append(recipe)
        grain = {"Off": 0, "Weak": 1, "Strong": 2}[recipe.grain]
        size = 1 if recipe.grain_size == "Large" else 0
        return np.array(
            [[[recipe.sharpness + 4, recipe.noise_reduction + 4, 0]]],
            dtype=np.uint8,
        ) + np.array([[[0, 0, grain * 2 + size]]], dtype=np.uint8)


class TextureTruth:
    """Scores a one-pixel render against the wanted texture."""

    def __init__(self, sharpness: int, nr: int, grain: int) -> None:
        """Remember the wanted values as the engine encodes them."""
        self.wanted = np.array([sharpness + 4, nr + 4, grain], np.float64)

    def compare(self, render: np.ndarray) -> float:
        """The city-block distance to the wanted texture, scaled down."""
        return (
            float(np.abs(render[0, 0].astype(np.float64) - self.wanted).sum())
            * 0.1
        )


def test_the_texture_stage_settles_the_texture_controls_last() -> None:
    """With a texture search, sharpness, NR and grain are found at the end."""
    truth = Recipe(film_simulation="ClassicChrome", shadows=1.0, color=-1)
    engine = TextureEngine()
    stages: list[str] = []
    progress: list[tuple[Recipe, int]] = []
    hooks = SearchHooks(
        stage=stages.append, progress=lambda r, n: progress.append((r, n))
    )
    caps = replace(CAPS, has_grain_size=True)
    # Sharpness +2, NR -3, grain Strong and Large (2 * 2 + 1).
    texture = look_match.TextureSearch(engine.render, TextureTruth(2, -3, 5))
    match = look_match.search(
        Recipe(), caps, fake_render, target_for(truth), hooks, texture
    )
    found = match.recipe
    assert stages[-1] == look_match.STAGE_TEXTURE
    assert (found.sharpness, found.noise_reduction) == (2, -3)
    assert (found.grain, found.grain_size) == ("Strong", "Large")
    assert found.film_simulation == "ClassicChrome"
    assert (found.shadows, found.color) == (1.0, -1)
    assert engine.rendered
    assert all(r.film_simulation == "ClassicChrome" for r in engine.rendered)
    assert match.renders > len(engine.rendered)
    assert progress[-1][0] == replace(found, color_space="sRGB")
    assert match.renders - len(engine.rendered) < progress[-1][1]
    assert progress[-1][1] <= match.renders


# Grain codes as TextureEngine renders them, Off/Small to Strong/Large.
_XE5_GRAIN = {0: 0.75, 1: 0.75, 2: 0.30, 3: 0.32, 4: 0.43, 5: 0.02}


class GrainTable:
    """Scores grain the way the X-E5 did for a strong, large grain edit."""

    def compare(self, render: np.ndarray) -> float:
        """The measured grain gap plus a small sharpness and NR gap."""
        sharpness, nr, grain = (int(v) for v in render[0, 0])
        return _XE5_GRAIN[grain] + 0.1 * (abs(sharpness - 4) + abs(nr - 4))


def test_grain_strength_and_size_are_chosen_together() -> None:
    """A strong large grain is found although its halves score worse."""
    engine = TextureEngine()
    texture = look_match.TextureSearch(engine.render, GrainTable())
    caps = replace(CAPS, has_grain_size=True)
    match = look_match.search(
        Recipe(), caps, fake_render, target_for(Recipe()), texture=texture
    )
    assert (match.recipe.grain, match.recipe.grain_size) == ("Strong", "Large")
    assert (match.recipe.sharpness, match.recipe.noise_reduction) == (0, 0)


def test_grain_size_is_only_tried_where_the_body_has_it() -> None:
    """A body without grain size never renders Large."""
    engine = TextureEngine()
    texture = look_match.TextureSearch(engine.render, TextureTruth(0, 0, 5))
    match = look_match.search(
        Recipe(), CAPS, fake_render, target_for(Recipe()), texture=texture
    )
    assert all(r.grain_size == "Small" for r in engine.rendered)
    assert match.recipe.grain == "Strong"


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
        target.view(np.zeros((10, 10, 3), np.uint8))


def test_lab_of_white_and_black() -> None:
    """White and black land on the ends of the Lab lightness axis."""
    rgb = np.array([[255, 255, 255], [0, 0, 0]])
    values = look_match._lab_of_linear(look_match._linear(rgb))
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
    assert stages[0] == look_match.STAGE_FILM
    assert stages[-1] == look_match.STAGE_RECHECK
    middle = stages[1:-1]
    assert middle[0] == look_match.STAGE_LOOK
    assert (
        1 <= middle.count(look_match.STAGE_LOOK) <= look_match._CANDIDATES[1]
    )
    assert look_match.STAGE_FINE in middle


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


def test_a_sharp_subject_outweighs_a_soft_background() -> None:
    """An error in the detailed half costs more than one in the flat half."""
    rng = np.random.default_rng(3)
    edit = np.full((80, 120, 3), 128, np.uint8)
    edit[:, :60] = rng.integers(60, 200, size=(80, 60, 3))
    target = LookTarget(edit, Alignment(np.eye(3), 1.0), (120, 80))
    off_in_subject = edit.copy()
    off_in_subject[:, :60, 0] = np.clip(
        edit[:, :60, 0].astype(int) + 40, 0, 255
    )
    off_in_background = edit.copy()
    off_in_background[:, 60:, 0] = 168
    subject = target.compare(target.view(off_in_subject))
    background = target.compare(target.view(off_in_background))
    assert subject > background


def test_the_result_does_not_depend_on_the_start() -> None:
    """Any start leads to the same recipe, only the kept fields differ."""
    truth = Recipe(film_simulation="ClassicChrome", shadows=1.0, color=-1)
    target = target_for(truth)
    plain_start = look_match.search(Recipe(), CAPS, fake_render, target)
    odd_start = look_match.search(
        Recipe(
            film_simulation="Velvia",
            shadows=-2.0,
            highlights=3.0,
            color=4,
            wb_shift_r=-6,
            exposure=1.0,
            sharpness=-2,
        ),
        CAPS,
        fake_render,
        target,
    )
    assert odd_start.recipe == plain_start.recipe
    assert odd_start.recipe.sharpness == 0


def test_both_white_balance_modes_are_tried() -> None:
    """The search starts as shot and tries the camera's Auto as well."""
    seen: set[str] = set()

    def render(recipe: Recipe) -> np.ndarray:
        seen.add(recipe.white_balance)
        return fake_render(recipe)

    match = look_match.search(Recipe(), CAPS, render, target_for(Recipe()))
    assert seen == {"AsShot", "Auto"}
    assert match.recipe.white_balance in seen


def test_clarity_waits_for_the_last_stage() -> None:
    """Slow clarity renders only come once everything else settled."""
    caps = replace(CAPS, has_clarity=True)
    stages: list[str] = []
    clarity_stages: set[str] = set()

    def render(recipe: Recipe) -> np.ndarray:
        if recipe.clarity:
            clarity_stages.add(stages[-1])
        return fake_render(recipe)

    look_match.search(
        Recipe(),
        caps,
        render,
        target_for(Recipe(film_simulation="Velvia")),
        SearchHooks(stage=stages.append),
    )
    assert stages[-1] == look_match.STAGE_CLARITY
    assert clarity_stages == {look_match.STAGE_CLARITY}


def test_likeness_sets_texture_aside() -> None:
    """A sharpened edit of the same look reads close, its colors match."""
    render = fake_render(Recipe()).astype(np.float64)
    detail = render - gaussian(render, 1.0)
    edit = np.clip(render + 0.8 * detail, 0, 255).astype(np.uint8)
    target = LookTarget(edit, Alignment(np.eye(3), 1.0), FRAME)
    view = target.view(render.astype(np.uint8))
    assert target.likeness(view) < 0.5 * target.compare(view)


def test_the_leader_before_white_balance_is_tuned_too() -> None:
    """A simulation hidden by the white-balanced ranking still gets tuned."""
    sims = [
        (score, Recipe(film_simulation=sim))
        for score, sim in ((1.0, "Provia"), (2.0, "Velvia"), (3.0, "Astia"))
    ]
    unbalanced = {"Provia": 3.0, "Velvia": 2.0, "Astia": 1.0}
    leaders = look_match._leaders(
        sims, lambda recipe: unbalanced[recipe.film_simulation]
    )
    assert [r.film_simulation for r in leaders] == [
        "Provia",
        "Velvia",
        "Astia",
    ]
    assert len(look_match._leaders(sims[:2], lambda _r: 0.0)) == 2


def test_simulations_close_behind_the_leader_are_tuned_too() -> None:
    """Every simulation within the slack is tuned, up to the most."""
    ranked = [
        (score, Recipe(film_simulation=sim))
        for score, sim in (
            (3.433, "Provia"),
            (3.590, "RealaAce"),
            (3.825, "ClassicChrome"),
            (3.949, "ProNegStd"),
            (4.153, "ProNegHi"),
        )
    ]
    leaders = look_match._leaders(ranked, lambda _r: 0.0)
    assert [r.film_simulation for r in leaders] == [
        "Provia",
        "RealaAce",
        "ClassicChrome",
        "ProNegStd",
    ]
    far = [
        (3.0, Recipe(film_simulation="Provia")),
        (3.5, Recipe(film_simulation="Velvia")),
        (4.0, Recipe(film_simulation="Astia")),
    ]
    assert len(look_match._leaders(far, lambda _r: 0.0)) == 2
    crowded = [
        (3.0 + 0.01 * n, Recipe(film_simulation=s))
        for n, s in enumerate(
            ("Provia", "Velvia", "Astia", "Eterna", "Sepia", "Acros")
        )
    ]
    assert (
        len(look_match._leaders(crowded, lambda _r: 0.0))
        == look_match._CANDIDATES[1]
    )


def test_the_search_says_how_many_simulations_it_tunes() -> None:
    """The candidates hook hears the count before the tuning starts."""
    heard: list[int] = []
    stages: list[str] = []

    def candidates(count: int) -> None:
        heard.append(count)
        assert look_match.STAGE_LOOK not in stages

    look_match.search(
        Recipe(),
        CAPS,
        fake_render,
        target_for(Recipe(film_simulation="Velvia")),
        SearchHooks(stage=stages.append, candidates=candidates),
    )
    assert len(heard) == 1
    assert 2 <= heard[0] <= 3
