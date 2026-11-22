"""Tests for trying recipes on the open image."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from grawji.recipe import Recipe
from grawji.tryout import Tryout

VELVIA = Recipe(film_simulation="Velvia")
ACROS = Recipe(film_simulation="Acros")
ASTIA = Recipe(film_simulation="Astia")


@dataclass
class Camera:
    """Holds every render until the test finishes it."""

    asked: list[tuple[Recipe, Callable[[bytes | None], None]]] = field(
        default_factory=list
    )

    def render(
        self, recipe: Recipe, on_done: Callable[[bytes | None], None]
    ) -> None:
        """Queue a render."""
        self.asked.append((recipe, on_done))

    def finish(self, index: int = -1, picture: bytes | None = b"jpeg") -> None:
        """Let one render come back."""
        self.asked[index][1](picture)


@dataclass
class Seen:
    """What a tryout handed out."""

    pictures: list[str] = field(default_factory=list)
    settled: int = 0

    def picture(self, name: str, _jpeg: bytes) -> None:
        """Note a picture."""
        self.pictures.append(name)

    def settle(self) -> None:
        """Note that nothing more is coming."""
        self.settled += 1


def test_recipes_render_one_after_another() -> None:
    """Only one render is on the camera at a time, in the given order."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)

    tryout.show(
        "a.RAF", [("V", VELVIA), ("A", ACROS)], seen.picture, seen.settle
    )
    assert [recipe for recipe, _done in camera.asked] == [VELVIA]

    camera.finish()
    assert seen.pictures == ["V"]
    assert [recipe for recipe, _done in camera.asked] == [VELVIA, ACROS]
    assert seen.settled == 0

    camera.finish()
    assert seen.pictures == ["V", "A"]
    assert seen.settled == 1


def test_pictures_are_kept_for_the_image() -> None:
    """Showing the recipes again hands them out without a render."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)
    camera.finish()
    tryout.stop()

    again = Seen()
    tryout.show("a.RAF", [("V", VELVIA)], again.picture, again.settle)

    assert len(camera.asked) == 1
    assert again.pictures == ["V"]
    assert again.settled == 1


def test_another_image_renders_anew() -> None:
    """Pictures of one image are not shown for the next."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)
    camera.finish()

    other = Seen()
    tryout.show("b.RAF", [("V", VELVIA)], other.picture, other.settle)

    assert other.pictures == []
    assert len(camera.asked) == 2


def test_another_exposure_renders_anew() -> None:
    """A recipe at a different exposure is a different picture."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)
    camera.finish()

    brighter = Recipe(film_simulation="Velvia", exposure=1.0)
    tryout.show("a.RAF", [("V", brighter)], seen.picture, seen.settle)

    assert len(camera.asked) == 2


def test_a_narrowed_list_skips_what_it_dropped() -> None:
    """Recipes filtered away while rendering are not rendered."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    everything = [("V", VELVIA), ("A", ACROS), ("S", ASTIA)]
    tryout.show("a.RAF", everything, seen.picture, seen.settle)

    narrowed = Seen()
    tryout.show("a.RAF", [("S", ASTIA)], narrowed.picture, narrowed.settle)
    camera.finish()
    camera.finish()

    assert [recipe for recipe, _done in camera.asked] == [VELVIA, ASTIA]
    assert narrowed.pictures == ["V", "S"]
    assert narrowed.settled == 1


def test_a_recipe_under_way_is_not_rendered_twice() -> None:
    """Asking again for the recipe on the camera waits for that render."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)

    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)
    camera.finish()

    assert len(camera.asked) == 1
    assert seen.pictures == ["V"]
    assert seen.settled == 1


def test_recipes_with_the_same_settings_share_a_render() -> None:
    """Two names for one look cost one render."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    twin = Recipe(film_simulation="Velvia")

    tryout.show(
        "a.RAF", [("V", VELVIA), ("Twin", twin)], seen.picture, seen.settle
    )
    camera.finish()

    assert len(camera.asked) == 1
    assert seen.pictures == ["V", "Twin"]


def test_a_failed_render_ends_the_run() -> None:
    """Without a camera the remaining recipes are not attempted."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show(
        "a.RAF", [("V", VELVIA), ("A", ACROS)], seen.picture, seen.settle
    )

    camera.finish(picture=None)

    assert len(camera.asked) == 1
    assert seen.pictures == []
    assert seen.settled == 1


def test_stopping_keeps_the_render_under_way() -> None:
    """A render that returns after a stop is kept but not handed out."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show(
        "a.RAF", [("V", VELVIA), ("A", ACROS)], seen.picture, seen.settle
    )

    tryout.stop()
    camera.finish()

    assert seen.pictures == []
    assert len(camera.asked) == 1
    again = Seen()
    tryout.show("a.RAF", [("V", VELVIA)], again.picture, again.settle)
    assert again.pictures == ["V"]
    assert len(camera.asked) == 1


def test_a_render_lost_with_the_image_does_not_block_the_next() -> None:
    """A render that never returns for an old image is not waited for."""
    camera, seen = Camera(), Seen()
    tryout = Tryout(camera.render)
    tryout.show("a.RAF", [("V", VELVIA)], seen.picture, seen.settle)

    tryout.show("b.RAF", [("A", ACROS)], seen.picture, seen.settle)

    assert [recipe for recipe, _done in camera.asked] == [VELVIA, ACROS]
    camera.finish(0)
    assert seen.pictures == []
