"""What the window does with recipes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from grawji.camera.core import recipe_from_profile
from grawji.image_exposure import ImageExposure
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from grawji.settings import FROM_IMAGE, FROM_IMAGE_LABEL, Settings
from grawji.views.recipe_library import RecipeLibraryController
from grawji.views.recipe_panel import RecipePanel


def recipe_hotkey(keyval: int, state: Any) -> int | None:
    """The recipe number a bare 1-9 press stands for."""
    modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
    if state & modifiers:
        return None
    if Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
        return int(keyval - Gdk.KEY_1 + 1)
    if Gdk.KEY_KP_1 <= keyval <= Gdk.KEY_KP_9:
        return int(keyval - Gdk.KEY_KP_1 + 1)
    return None


@dataclass(frozen=True)
class RecipeHost:
    """What the recipe actions need from the window.

    Attributes:
        library: The saved recipes.
        exposure: The open image's exposure.
        profile: The open image's camera profile.
        uses_camera_file: Whether the open shot is developed from its
            camera file instead of its RAW.
        render_if_open: Renders the preview if an image is open.
        persist_exposure: Stores the image's exposure when it changed.
        set_status: Takes a line for the status bar.
        save_settings: Writes the settings to disk.
    """

    library: RecipeLibrary
    exposure: ImageExposure
    profile: Callable[[], bytes | None]
    uses_camera_file: Callable[[], bool]
    render_if_open: Callable[[], None]
    persist_exposure: Callable[[], None]
    set_status: Callable[[str], None]
    save_settings: Callable[[], None]


class RecipeActions:
    """Applies recipes to the controls on the user's behalf."""

    def __init__(
        self,
        parent: Gtk.Window,
        panel: RecipePanel,
        manager: RecipeLibraryController,
        settings: Settings,
        host: RecipeHost,
    ) -> None:
        """Bind the actions to the window, the controls and the library."""
        self._parent = parent
        self._panel = panel
        self._manager = manager
        self._settings = settings
        self._host = host

    def apply(self, name: str) -> None:
        """Apply the picked recipe, guarding unsaved recipe edits."""
        self.confirm_discard(partial(self._apply_choice, name))

    def apply_found(self, recipe: Recipe, title: str) -> None:
        """Apply a recipe found for an edit, its exposure as the image EV."""
        self._manager.apply_unsaved(recipe, title)
        self._host.exposure.current = recipe.exposure
        self._host.persist_exposure()

    def confirm_discard(self, proceed: Callable[[], None]) -> None:
        """Run proceed, first asking about unsaved recipe edits."""
        if not self._panel.needs_save:
            proceed()
            return
        label = self._panel.active_label
        body = (
            f"The controls changed since “{label}” was applied."
            if self._panel.is_modified
            else f"“{label}” has not been saved yet."
        )
        dialog = Adw.AlertDialog(heading="Unsaved recipe changes", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save…")
        dialog.set_response_appearance(
            "discard", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("save")

        def on_response(_dialog: Any, response: str) -> None:
            if response == "discard":
                proceed()
            elif response == "save":
                self._manager.save_current()

        dialog.connect("response", on_response)
        dialog.present(self._parent)

    def reset(self) -> None:
        """Reset all controls to the default recipe, keeping the EV."""
        exposure = self._panel.get_recipe().exposure
        self._panel.set_active(Recipe(), "Default")
        self._panel.set_exposure(exposure)
        self._host.render_if_open()

    def undo(self) -> None:
        """Take the last recipe edit back."""
        if not self._panel.undo():
            self._host.set_status("Nothing to undo.")

    def redo(self) -> None:
        """Bring the last undone recipe edit back."""
        if not self._panel.redo():
            self._host.set_status("Nothing to redo.")

    def on_key(self, keyval: int, state: Any) -> bool:
        """Apply the recipe assigned to a bare number key."""
        hotkey = recipe_hotkey(keyval, state)
        if hotkey is None:
            return False
        name = self._host.library.recipe_for_hotkey(hotkey)
        if name is None:
            return False
        if self._host.uses_camera_file():
            self._host.set_status(
                "Recipes develop the RAW. Switch to it with j."
            )
            return True
        self.apply(name)
        return True

    def _apply_choice(self, name: str) -> None:
        """Apply and remember the recipe chosen in the panel's picker."""
        self._settings.open_recipe = name
        self._host.save_settings()
        if name == FROM_IMAGE:
            self._apply_from_image()
        else:
            self._manager.apply(name)

    def _apply_from_image(self) -> None:
        """Load the open image's own in-camera recipe into the controls."""
        profile = self._host.profile()
        if profile is None:
            return
        self._panel.set_active(recipe_from_profile(profile), FROM_IMAGE_LABEL)
        current = self._host.exposure.current
        if current is not None:
            self._panel.set_exposure(current)
        self._host.render_if_open()
