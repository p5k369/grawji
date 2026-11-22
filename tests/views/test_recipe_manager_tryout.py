"""Tests for trying recipes on the open image in the manager."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from grawji.views.recipe_manager import RecipeManagerDialog
from tests.gui_support import StubManagerHost, pump
from tests.image_support import jpeg_bytes, quadrants

pytestmark = pytest.mark.gui


@dataclass
class Asked:
    """What the dialog asked its host to try."""

    names: list[list[str]] = field(default_factory=list)
    on_picture: Callable[[str, bytes], None] | None = None
    on_settled: Callable[[], None] | None = None
    stops: int = 0

    def try_recipes(
        self,
        names: list[str],
        on_picture: Callable[[str, bytes], None],
        on_settled: Callable[[], None],
    ) -> None:
        """Note a run."""
        self.names.append(names)
        self.on_picture = on_picture
        self.on_settled = on_settled

    def stop(self) -> None:
        """Note a stop."""
        self.stops += 1


def manager(
    tmp_path: Path, *, image: str | None = "/shots/a.RAF"
) -> tuple[Any, Asked]:
    """A manager over three recipes, one of them in a folder."""
    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Velvia look", Recipe(film_simulation="Velvia"))
    library.add("Acros look", Recipe(film_simulation="Acros"))
    library.create_folder("Soft")
    library.add("Astia look", Recipe(film_simulation="Astia"), folder="Soft")
    asked = Asked()
    host = StubManagerHost(
        current_raf=lambda: image,
        try_recipes=asked.try_recipes,
        stop_tryout=asked.stop,
    )
    dialog = RecipeManagerDialog(library=library, host=host)
    pump()
    return dialog, asked


@pytest.fixture
def jpeg(tmp_path: Path) -> bytes:
    """A small real JPEG, as a render would be."""
    return jpeg_bytes(quadrants(), str(tmp_path / "render.jpg"), tag=1)


def test_trying_needs_an_open_image(tmp_path: Path) -> None:
    """Without an image there is nothing to try the recipes on."""
    dialog, _asked = manager(tmp_path, image=None)

    assert not dialog.tryout_button.get_sensitive()


def test_trying_without_a_selection_asks_for_one(tmp_path: Path) -> None:
    """Nothing renders until recipes are selected."""
    dialog, asked = manager(tmp_path)

    dialog.tryout_button.set_active(True)

    assert dialog.side_stack.get_visible_child_name() == "tryout"
    assert dialog.tryout_stack.get_visible_child_name() == "none"
    assert asked.names == [[]]
    assert dialog.stack.get_visible_child_name() == "list"


def test_only_the_selection_is_tried(tmp_path: Path) -> None:
    """The selected recipes get a card each, the others are left alone."""
    dialog, asked = manager(tmp_path)
    dialog.select_names(["Velvia look", "Astia look"])

    dialog.tryout_button.set_active(True)

    assert list(dialog._cards) == ["Velvia look", "Astia look"]
    assert asked.names == [["Velvia look", "Astia look"]]
    assert dialog.tryout_stack.get_visible_child_name() == "cards"
    assert dialog.tryout_spinner.get_visible()


def test_the_cards_follow_the_selection(tmp_path: Path) -> None:
    """Selecting in the list while trying changes what is shown."""
    dialog, asked = manager(tmp_path)
    dialog.tryout_button.set_active(True)

    dialog.select("Acros look")
    dialog.select("Astia look", toggle=True)

    assert list(dialog._cards) == ["Acros look", "Astia look"]
    assert asked.names[-1] == ["Acros look", "Astia look"]


def test_a_render_lands_on_its_card(tmp_path: Path, jpeg: bytes) -> None:
    """A finished render fills its card and the run ends the spinner."""
    dialog, asked = manager(tmp_path)
    dialog.select_names(["Velvia look", "Acros look"])
    dialog.tryout_button.set_active(True)
    assert asked.on_picture is not None
    assert asked.on_settled is not None

    asked.on_picture("Acros look", jpeg)
    asked.on_picture("Astia look", jpeg)
    asked.on_settled()

    assert dialog._cards["Acros look"].has_picture
    assert not dialog._cards["Velvia look"].has_picture
    assert not dialog.tryout_spinner.get_visible()


def test_a_card_applies_its_recipe(tmp_path: Path) -> None:
    """The button on a card applies that recipe to the open image."""
    dialog, _asked = manager(tmp_path)
    applied: list[str] = []
    dialog._host.apply_saved = applied.append
    dialog.select_names(["Velvia look", "Acros look"])
    dialog.tryout_button.set_active(True)

    dialog._cards["Acros look"].apply_button.emit("clicked")

    assert applied == ["Acros look"]


def test_leaving_returns_to_the_details(tmp_path: Path, jpeg: bytes) -> None:
    """Switching back stops the renders and stores no picture."""
    dialog, asked = manager(tmp_path)
    dialog.select("Acros look")
    dialog.tryout_button.set_active(True)
    assert asked.on_picture is not None
    asked.on_picture("Acros look", jpeg)

    dialog.tryout_button.set_active(False)

    assert dialog.side_stack.get_visible_child_name() == "manage"
    assert asked.stops == 1
    assert dialog._cards == {}
    assert dialog._library.thumb_jpeg("Acros look") is None


def test_closing_stops_the_renders(tmp_path: Path) -> None:
    """A closed manager leaves nothing rendering."""
    dialog, asked = manager(tmp_path)
    dialog.tryout_button.set_active(True)

    dialog.emit("closed")

    assert asked.stops == 1
