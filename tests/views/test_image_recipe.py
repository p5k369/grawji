"""Which recipe an image opens with: carried over, or its own."""

from __future__ import annotations

import time
from typing import Any

import pytest

from grawji.camera.core import CameraSession, apply_recipe
from grawji.recipe import Recipe
from grawji.settings import FROM_IMAGE_LABEL
from tests.gui_support import pump

pytestmark = pytest.mark.gui

# Each image's in-camera recipe, as its profile.
SHOT_SIMS = {"a.RAF": "Provia", "b.RAF": "Astia", "c.RAF": "Velvia"}
FULL_PROFILE = 632


class FakeWorker:
    """Opens and renders at once, without a camera."""

    def open(self, raf_path: str, **kwargs: Any) -> None:
        """Report the open as done."""
        kwargs["on_done"](None)

    def render(self, recipe: Recipe, **kwargs: Any) -> None:
        """Renders are not looked at here."""

    def submit(self, task: Any, **kwargs: Any) -> None:
        """Nothing to run."""

    def stop(self, **kwargs: Any) -> None:
        """Nothing to stop."""


@pytest.fixture
def win(window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """The window, opening fake images whose profiles differ by sim."""
    window._worker.stop(close_session=False)
    monkeypatch.setattr(window, "_worker", FakeWorker())
    monkeypatch.setattr(window, "_decode_selection", lambda *a: None)
    monkeypatch.setattr(CameraSession, "is_open", property(lambda s: True))

    def profile(_session: Any) -> bytes:
        sim = SHOT_SIMS[window._raf_path.name]
        return apply_recipe(bytes(FULL_PROFILE), Recipe(film_simulation=sim))

    monkeypatch.setattr(CameraSession, "profile", property(profile))
    window.test_dir = tmp_path
    return window


@pytest.fixture
def per_image(win: Any) -> Any:
    """The window with "Give each image its own recipe" on."""
    win._settings.recipe_per_image = True
    return win


def _open(win: Any, name: str) -> None:
    """Select an image and wait until it has opened."""
    win._on_raf_selected(str(win.test_dir / name))
    deadline = time.monotonic() + 5
    while win._panel_image != str(win.test_dir / name):
        assert time.monotonic() < deadline, "image never opened"
        pump()
        time.sleep(0.01)


def _sim(win: Any) -> str:
    return str(win.recipe_panel.get_recipe().film_simulation)


def _pick_sim(win: Any, sim: str) -> None:
    """Edit the film simulation like the user does."""
    win.recipe_panel.film_row.set_selected(
        win.recipe_panel._film_sims.index(sim)
    )
    pump()


def test_edits_do_not_follow_to_the_next_image(per_image: Any) -> None:
    """The next image opens on its own recipe, not the edited one."""
    win = per_image
    _open(win, "a.RAF")
    _pick_sim(win, "Velvia")
    assert win.recipe_panel.is_modified

    _open(win, "b.RAF")
    assert win.recipe_panel.active_label == FROM_IMAGE_LABEL
    assert _sim(win) == "Astia"
    assert not win.recipe_panel.needs_save


def test_returning_to_an_edited_image_shows_its_edits(per_image: Any) -> None:
    """Edits stay with their image for the session."""
    win = per_image
    _open(win, "a.RAF")
    _pick_sim(win, "Velvia")
    _open(win, "b.RAF")
    _open(win, "a.RAF")
    assert _sim(win) == "Velvia"
    assert win.recipe_panel.is_modified
    assert win.recipe_panel.active_label == FROM_IMAGE_LABEL


def test_an_unedited_image_is_not_remembered(per_image: Any) -> None:
    """Images left on their own recipe are not kept."""
    win = per_image
    _open(win, "a.RAF")
    _open(win, "b.RAF")
    assert win._image_recipes == {}


def test_a_picked_recipe_does_not_stick(per_image: Any) -> None:
    """A recipe chosen from the picker applies to that image only."""
    win = per_image
    win._recipe_library.add("Astia look", Recipe(film_simulation="Astia"))
    _open(win, "a.RAF")
    win._apply_choice("Astia look")
    assert _sim(win) == "Astia"

    _open(win, "c.RAF")
    assert win.recipe_panel.active_label == FROM_IMAGE_LABEL
    assert _sim(win) == "Velvia"

    _open(win, "a.RAF")
    assert win.recipe_panel.active_label == "Astia look"
    assert _sim(win) == "Astia"


def test_by_default_edits_carry_over(win: Any) -> None:
    """Off, the edited recipe follows to the next image, as before."""
    _open(win, "a.RAF")
    _pick_sim(win, "Velvia")
    _open(win, "b.RAF")
    assert _sim(win) == "Velvia"
    assert win._image_recipes == {}


def test_by_default_a_picked_recipe_sticks(win: Any) -> None:
    """Off, the picker's choice applies to every image opened next."""
    win._recipe_library.add("Astia look", Recipe(film_simulation="Astia"))
    _open(win, "a.RAF")
    win._apply_choice("Astia look")
    _open(win, "c.RAF")
    assert win.recipe_panel.active_label == "Astia look"
    assert _sim(win) == "Astia"


def test_turning_it_off_forgets_the_edits(per_image: Any) -> None:
    """Remembered edits do not come back after switching it off and on."""
    win = per_image
    _open(win, "a.RAF")
    _pick_sim(win, "Velvia")
    _open(win, "b.RAF")
    assert win._image_recipes
    win._settings.recipe_per_image = False
    win._on_settings_changed()
    assert win._image_recipes == {}


def test_preferences_switch_sets_it() -> None:
    """The Behavior switch edits the setting and reports the change."""
    from grawji.settings import Settings
    from grawji.views.preferences import PreferencesDialog

    settings = Settings()
    changes: list[bool] = []
    dialog = PreferencesDialog(
        settings=settings, on_change=lambda: changes.append(True)
    )
    assert not dialog.recipe_per_image_row.get_active()
    dialog.recipe_per_image_row.set_active(True)
    assert settings.recipe_per_image is True
    assert changes
