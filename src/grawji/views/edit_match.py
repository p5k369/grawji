"""The dialog that watches the camera search for an edit's recipe."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from grawji import look_match
from grawji.controllers.edit_match import RENDER_BUDGET
from grawji.recipe_text import film_sim_label
from grawji.search_estimate import describe
from grawji.views.textures import texture_for_rgb

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from grawji.recipe import Recipe

_UI = (
    resources.files("grawji")
    .joinpath("ui", "edit_match.ui")
    .read_text(encoding="utf-8")
)

_STAGES = {
    look_match.STAGE_FILM: "Trying the film simulations…",
    look_match.STAGE_LOOK: "Tuning tones and color…",
    look_match.STAGE_FINE: "Fine tuning…",
    look_match.STAGE_RECHECK: "Checking the other film simulations…",
    look_match.STAGE_CLARITY: "Trying clarity…",
    look_match.STAGE_TEXTURE: "Matching the texture on full-size renders…",
}
# Limits of the softened mean delta E for the verdicts, closest first.
_VERDICTS = (
    (1.0, "Very close to your edit."),
    (2.0, "Close to your edit."),
    (4.0, "Roughly your edit. Part of it is beyond what a recipe can do."),
)
_FAR = (
    "Only part of your edit. Local work like vignettes or masks is beyond"
    " what a recipe can do."
)
# The budget slider moves in steps of this many renders
BUDGET_STEP = 100
NO_LIMIT = 1600


def verdict(distance: float) -> str:
    """How close a found recipe comes, in words."""
    for limit, text in _VERDICTS:
        if distance < limit:
            return text
    return _FAR


@Gtk.Template(string=_UI)
class EditMatchDialog(Adw.Dialog):
    """Shows a running recipe-from-edit search and its result."""

    __gtype_name__ = "GrawjiEditMatchDialog"

    edit_picture = Gtk.Template.Child()
    camera_picture = Gtk.Template.Child()
    status_label = Gtk.Template.Child()
    detail_label = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    start_button = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()
    skin_row = Gtk.Template.Child()
    texture_row = Gtk.Template.Child()
    budget_row = Gtk.Template.Child()
    budget_scale = Gtk.Template.Child()
    options_revealer = Gtk.Template.Child()
    options_button = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()

    def __init__(
        self,
        on_apply: Callable[[Recipe], None],
        on_start: Callable[[bool, bool, int | None], None],
        budget: int | None = RENDER_BUDGET,
    ) -> None:
        """Create the dialog."""
        super().__init__()
        self._on_apply = on_apply
        self._on_start = on_start
        self._cancel: Callable[[], None] | None = None
        self._found: Recipe | None = None
        self.budget_scale.add_mark(RENDER_BUDGET, Gtk.PositionType.BOTTOM)
        self.budget_scale.set_format_value_func(self._budget_text)
        self.budget_scale.set_value(NO_LIMIT if budget is None else budget)
        self.budget_scale.connect("value-changed", self._on_budget_dragged)
        self.options_button.connect("toggled", self._on_options_toggled)
        self.cancel_button.connect("clicked", lambda *_a: self.close())
        self.start_button.connect("clicked", self._on_start_clicked)
        self.apply_button.connect("clicked", self._on_apply_clicked)
        self.connect("closed", self._on_closed)

    @property
    def budget(self) -> int | None:
        """The renders the next search may spend."""
        value = round(self.budget_scale.get_value())
        return None if value >= NO_LIMIT else value

    @staticmethod
    def _budget_text(_scale: Gtk.Scale, value: float) -> str:
        """The slider's value in words."""
        return "No limit" if value >= NO_LIMIT else f"{round(value)}"

    def _on_budget_dragged(self, scale: Gtk.Scale) -> None:
        """Snap the slider to whole steps."""
        snapped = round(scale.get_value() / BUDGET_STEP) * BUDGET_STEP
        if snapped != scale.get_value():
            scale.set_value(snapped)

    def set_cancel(self, cancel: Callable[[], None]) -> None:
        """What stops the search when the dialog closes early."""
        self._cancel = cancel

    def show_edit(self, pixels: NDArray[np.uint8]) -> None:
        """Show the edit and let the search start."""
        self.edit_picture.set_paintable(texture_for_rgb(pixels))
        if self._cancel is None and self._found is None:
            self.status_label.set_label(
                "Choose what counts most, then find the recipe."
            )
            self.start_button.set_sensitive(True)
            self.set_default_widget(self.start_button)

    def show_view(self, pixels: NDArray[np.uint8]) -> None:
        """Show the camera's best render so far."""
        self.camera_picture.set_paintable(texture_for_rgb(pixels))

    def set_stage(self, stage: str) -> None:
        """Name the stage the search is in."""
        self.status_label.set_label(_STAGES.get(stage, stage))

    def set_progress(self, recipe: Recipe, renders: int) -> None:
        """Name the best recipe so far and the camera's work."""
        self.detail_label.set_label(
            f"Best so far: {film_sim_label(recipe.film_simulation)}"
            f" · {renders} renders"
        )

    def set_estimate(self, fraction: float, seconds: float | None) -> None:
        """Show how far the search is and the time it still needs."""
        if self._cancel is None:
            return
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(describe(seconds) or " ")

    def finish(self, match: look_match.Match) -> None:
        """Show the result and offer to apply it."""
        self._cancel = None
        self._found = match.recipe
        self._stop_spinning(again=True)
        self.status_label.set_label(verdict(match.distance))
        self.detail_label.set_label(
            f"{film_sim_label(match.recipe.film_simulation)}"
            f" · {match.renders} renders"
        )
        self.apply_button.set_visible(True)
        self.set_default_widget(self.apply_button)
        self.apply_button.grab_focus()

    def fail(self, message: str, *, retry: bool = False) -> None:
        """Show why the search stopped."""
        self._cancel = None
        self._stop_spinning(again=retry)
        self.status_label.set_label(message)

    def _stop_spinning(self, *, again: bool) -> None:
        """Leave the running state."""
        self.spinner.set_spinning(False)
        self.spinner.set_visible(False)
        self.progress_bar.set_visible(False)
        self.cancel_button.set_label("Close")
        self.skin_row.set_sensitive(True)
        self.texture_row.set_sensitive(True)
        self.budget_row.set_sensitive(True)
        self.options_button.set_visible(again)
        self.start_button.set_label("Search Again")
        self.start_button.set_visible(again)
        self.start_button.set_sensitive(again)

    def _on_options_toggled(self, button: Gtk.ToggleButton) -> None:
        """Fold the options in or out after a search."""
        self.options_revealer.set_reveal_child(button.get_active())

    def _on_start_clicked(self, *_args: Any) -> None:
        """Enter the running state and start a search."""
        self.start_button.set_visible(False)
        self.apply_button.set_visible(False)
        self.skin_row.set_sensitive(False)
        self.texture_row.set_sensitive(False)
        self.budget_row.set_sensitive(False)
        self.options_button.set_active(False)
        self.options_button.set_visible(False)
        self.options_revealer.set_reveal_child(False)
        self.spinner.set_visible(True)
        self.spinner.set_spinning(True)
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text(" ")
        self.progress_bar.set_visible(True)
        self.cancel_button.set_label("Cancel")
        self.detail_label.set_label("")
        self.status_label.set_label("Finding the edit in the shot…")
        self._on_start(
            self.skin_row.get_active(),
            self.texture_row.get_active(),
            self.budget,
        )

    def _on_apply_clicked(self, *_args: Any) -> None:
        """Hand the found recipe on and close."""
        if self._found is not None:
            self._on_apply(self._found)
        self.close()

    def _on_closed(self, *_args: Any) -> None:
        """Stop a search that is still running."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
