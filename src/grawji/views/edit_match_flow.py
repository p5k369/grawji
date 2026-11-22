"""The steps from picking an edited picture to its matched recipe."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk

from grawji import mainloop
from grawji.camera.capabilities import Capabilities
from grawji.controllers.edit_match import (
    EditMatchCallbacks,
    EditMatchController,
    EditMatchError,
    EditMatchRequest,
    edit_preview,
)
from grawji.imaging import imagemeta
from grawji.look_match import SearchCancelledError
from grawji.recipe import Recipe
from grawji.settings import Settings
from grawji.views import dialogs
from grawji.views.edit_match import EditMatchDialog


@dataclass(frozen=True)
class EditMatchHost:
    """What the search for an edit's recipe needs from the window.

    Attributes:
        open_raf: The image open on the camera, None when there is none.
        recipe: The recipe the controls show, where the search starts.
        capabilities: What the developing body's controls can do.
        set_status: Takes a line for the status bar.
        confirm_discard: Runs its argument once unsaved edits are dealt
            with.
        apply: Takes the recipe found and the title to apply it under.
    """

    open_raf: Callable[[], str | None]
    recipe: Callable[[], Recipe]
    capabilities: Callable[[], Capabilities]
    set_status: Callable[[str], None]
    confirm_discard: Callable[[Callable[[], None]], None]
    apply: Callable[[Recipe, str], None]


class EditMatchFlow:
    """Asks for an edited export of the open image and matches its look."""

    def __init__(
        self,
        parent: Gtk.Window,
        controller: EditMatchController,
        settings: Settings,
        host: EditMatchHost,
    ) -> None:
        """Bind the flow to the window and the search controller."""
        self._parent = parent
        self._controller = controller
        self._settings = settings
        self._host = host

    def begin(self) -> None:
        """Ask for the edited picture."""
        if self._host.open_raf() is None:
            self._host.set_status(
                "Open an image with its camera connected first."
            )
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose an edited version of this image")
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)
        dialog.open(self._parent, None, self._on_picked)

    def _on_picked(self, dialog: Any, result: Any) -> None:
        """Open the search dialog for the picked edit."""
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        raf_path = self._host.open_raf()
        if path is None or raf_path is None:
            return
        self.search(raf_path, path)

    def search(self, raf_path: str, edit_path: str) -> None:
        """Present the search for one edit of one image."""
        title = Path(edit_path).stem
        orientation = imagemeta.exif_orientation(raf_path)
        start = self._host.recipe()
        capabilities = self._host.capabilities()

        def failed(error: Exception) -> None:
            if isinstance(error, SearchCancelledError):
                return
            if isinstance(error, EditMatchError):
                match_dialog.fail(str(error))
                return
            logging.getLogger("grawji").warning(
                "recipe from an edit failed: %s", error
            )
            match_dialog.fail(
                f"The camera stopped the search: {error}", retry=True
            )

        def run(
            skin_priority: bool, texture: bool, budget: int | None
        ) -> None:
            self._settings.edit_match_budget = budget or 0
            cancel = self._controller.start(
                EditMatchRequest(
                    raf_path=raf_path,
                    edit_path=edit_path,
                    orientation=orientation,
                    start=start,
                    capabilities=capabilities,
                    skin_priority=skin_priority,
                    texture=texture,
                    budget=budget,
                ),
                EditMatchCallbacks(
                    on_edit=match_dialog.show_edit,
                    on_view=match_dialog.show_view,
                    on_progress=match_dialog.set_progress,
                    on_stage=match_dialog.set_stage,
                    on_estimate=match_dialog.set_estimate,
                    on_done=match_dialog.finish,
                    on_error=failed,
                ),
            )
            match_dialog.set_cancel(cancel)

        match_dialog = EditMatchDialog(
            on_apply=lambda recipe: self._host.confirm_discard(
                partial(self._host.apply, recipe, title)
            ),
            on_start=run,
            budget=self._settings.edit_match_budget or None,
        )

        def preview() -> None:
            try:
                pixels = edit_preview(edit_path)
            except EditMatchError as error:
                mainloop.call(match_dialog.fail, str(error))
                return
            mainloop.call(match_dialog.show_edit, pixels)

        threading.Thread(
            target=preview, name="grawji-edit-preview", daemon=True
        ).start()
        dialogs.fit_dialog(
            match_dialog,
            self._parent,
            width_fraction=0.85,
            height_fraction=0.85,
        )
        match_dialog.present(self._parent)
