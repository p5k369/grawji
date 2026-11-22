"""Tests for the recipe an opened image starts with."""

from __future__ import annotations

from grawji.open_plan import plan_open
from grawji.recipe import Recipe
from grawji.settings import FROM_IMAGE, FROM_IMAGE_LABEL

_SHOT = Recipe(film_simulation="ClassicChrome", exposure=0.33)
_SAVED = Recipe(film_simulation="Velvia")


def _plan(**overrides: object):
    """A plan for an image shown from its embedded JPEG, as shot."""
    args: dict[str, object] = {
        "shot": _SHOT,
        "stored_ev": None,
        "carry": None,
        "selection": FROM_IMAGE,
        "saved": None,
        "has_embedded": True,
    }
    return plan_open(**(args | overrides))


def test_the_image_recipe_needs_no_render() -> None:
    """The embedded JPEG already shows the shot's own recipe."""
    plan = _plan()
    assert plan.recipe == _SHOT
    assert plan.label == FROM_IMAGE_LABEL
    assert plan.exposure == 0.33
    assert not plan.render


def test_a_stored_exposure_forces_a_render() -> None:
    """A changed exposure is not what the embedded JPEG shows."""
    plan = _plan(stored_ev=1.0)
    assert plan.exposure == 1.0
    assert plan.render


def test_a_stored_exposure_equal_to_the_shot_needs_no_render() -> None:
    """An override that matches the shot changes nothing on screen."""
    assert not _plan(stored_ev=0.33).render


def test_without_an_embedded_jpeg_the_image_recipe_renders() -> None:
    """Nothing on screen yet means the camera has to draw it."""
    assert _plan(has_embedded=False).render


def test_a_saved_recipe_is_applied_under_its_name() -> None:
    """The sticky choice wins over the shot's own recipe."""
    plan = _plan(selection="Punchy", saved=_SAVED)
    assert plan.recipe == _SAVED
    assert plan.label == "Punchy"
    assert plan.exposure == 0.33
    assert plan.render


def test_a_deleted_choice_falls_back_to_the_image_recipe() -> None:
    """A sticky name without a recipe behind it means the image's own."""
    plan = _plan(selection="Gone", saved=None)
    assert plan.recipe == _SHOT
    assert plan.label == FROM_IMAGE_LABEL


def test_unsaved_edits_carry_across_images() -> None:
    """Edits in progress stay, only the exposure is the new image's."""
    edit = Recipe(film_simulation="Acros", exposure=2.0)
    plan = _plan(carry=edit, selection="Punchy", saved=_SAVED, stored_ev=-1.0)
    assert plan.recipe == edit
    assert plan.label is None
    assert plan.exposure == -1.0
    assert plan.render


def test_without_a_profile_the_controls_are_left_alone() -> None:
    """No profile and no saved choice leaves whatever the controls show."""
    plan = _plan(shot=None)
    assert plan.recipe is None
    assert plan.exposure == 0.0
    assert plan.render


def test_without_a_profile_a_saved_recipe_still_applies() -> None:
    """The sticky choice does not depend on the image's profile."""
    plan = _plan(shot=None, selection="Punchy", saved=_SAVED)
    assert plan.recipe == _SAVED
    assert plan.label == "Punchy"
