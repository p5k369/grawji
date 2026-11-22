"""Tests for applying recipes on the user's behalf."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk

from grawji.image_exposure import ImageExposure
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from grawji.settings import Settings
from grawji.views.recipe_actions import (
    RecipeActions,
    RecipeHost,
    recipe_hotkey,
)

_NONE = Gdk.ModifierType(0)


class FakePanel:
    """The recipe controls, reduced to what the actions touch."""

    def __init__(self) -> None:
        """Start on a saved recipe without edits."""
        self.recipe = Recipe(exposure=0.67)
        self.label = "Start"
        self.needs_save = False
        self.is_modified = False
        self.history = 0

    @property
    def active_label(self) -> str:
        """The name the recipe is applied under."""
        return self.label

    def get_recipe(self) -> Recipe:
        """The recipe the controls show."""
        return self.recipe

    def set_active(self, recipe: Recipe, label: str) -> None:
        """Load a recipe under a name."""
        self.recipe, self.label = recipe, label

    def set_exposure(self, value: float) -> None:
        """Set the image's exposure on the controls."""
        self.recipe = Recipe(**{**self.recipe.to_dict(), "exposure": value})

    def undo(self) -> bool:
        """Step back when there is a step to take."""
        if self.history <= 0:
            return False
        self.history -= 1
        return True

    def redo(self) -> bool:
        """There is never a step to bring back here."""
        return False


@dataclass
class Rig:
    """The actions with their fakes."""

    panel: FakePanel
    settings: Settings
    library: RecipeLibrary
    applied: list[str] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    renders: list[int] = field(default_factory=list)
    saves: list[int] = field(default_factory=list)
    camera_file: bool = False
    actions: Any = None


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    """Actions over a library with one recipe on key 3."""
    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Punchy", Recipe(film_simulation="Velvia"))
    library.set_hotkey("Punchy", 3)
    rig = Rig(FakePanel(), Settings(), library)

    class Manager:
        def apply(self, name: str) -> None:
            rig.applied.append(name)

    rig.actions = RecipeActions(
        None,
        rig.panel,
        Manager(),
        rig.settings,
        RecipeHost(
            library=library,
            exposure=ImageExposure(),
            profile=lambda: None,
            is_open=lambda: True,
            open_path=lambda: None,
            uses_camera_file=lambda: rig.camera_file,
            render_thumb=lambda *_a, **_k: None,
            render_if_open=lambda: rig.renders.append(1),
            persist_exposure=lambda: None,
            set_status=rig.status.append,
            save_settings=lambda: rig.saves.append(1),
        ),
    )
    return rig


def test_number_keys_map_to_recipe_numbers() -> None:
    """Both key rows count, modifiers and other keys do not."""
    assert recipe_hotkey(Gdk.KEY_1, _NONE) == 1
    assert recipe_hotkey(Gdk.KEY_9, _NONE) == 9
    assert recipe_hotkey(Gdk.KEY_KP_4, _NONE) == 4
    assert recipe_hotkey(Gdk.KEY_0, _NONE) is None
    assert recipe_hotkey(Gdk.KEY_a, _NONE) is None
    assert recipe_hotkey(Gdk.KEY_3, Gdk.ModifierType.CONTROL_MASK) is None
    assert recipe_hotkey(Gdk.KEY_3, Gdk.ModifierType.ALT_MASK) is None


def test_a_recipe_key_applies_and_remembers_the_recipe(rig: Rig) -> None:
    """The key's recipe becomes the sticky choice for the next images."""
    assert rig.actions.on_key(Gdk.KEY_3, _NONE)
    assert rig.applied == ["Punchy"]
    assert rig.settings.open_recipe == "Punchy"
    assert rig.saves == [1]


def test_a_key_without_a_recipe_is_left_to_others(rig: Rig) -> None:
    """An unassigned number is not swallowed."""
    assert not rig.actions.on_key(Gdk.KEY_5, _NONE)
    assert rig.applied == []


def test_a_recipe_key_explains_itself_on_a_camera_file(rig: Rig) -> None:
    """A shot developed from its camera file takes no recipe."""
    rig.camera_file = True
    assert rig.actions.on_key(Gdk.KEY_3, _NONE)
    assert rig.applied == []
    assert "Switch to it with j" in rig.status[0]


def test_reset_keeps_the_image_exposure(rig: Rig) -> None:
    """The default recipe replaces the look, not the image's EV."""
    rig.actions.reset()
    assert rig.panel.label == "Default"
    assert rig.panel.recipe == Recipe(exposure=0.67)
    assert rig.renders == [1]


def test_undo_says_when_there_is_nothing_to_undo(rig: Rig) -> None:
    """The status line answers an undo that cannot happen."""
    rig.panel.history = 1
    rig.actions.undo()
    assert rig.status == []
    rig.actions.undo()
    assert rig.status == ["Nothing to undo."]
    rig.actions.redo()
    assert rig.status[-1] == "Nothing to redo."
