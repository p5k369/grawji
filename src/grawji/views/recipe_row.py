"""One recipe of the recipe manager's list."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from grawji.views.recipe_thumb import RecipeThumb

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_row.ui")
    .read_text(encoding="utf-8")
)

_ = RecipeThumb

_FIT_CSS = ("warning", "error")


@dataclass(frozen=True)
class Fit:
    """How a recipe fits the connected body.

    Attributes:
        label: The chip's text.
        css: The chip's style.
        tooltip: What the body drops.
    """

    label: str
    css: str
    tooltip: str


@dataclass(frozen=True)
class RowContent:
    """What a recipe row shows.

    Attributes:
        name: The recipe's name.
        summary: The recipe in one line.
        comment: The recipe's comment.
        texture: The recipe's picture.
        is_baseline: Whether the split view compares against it.
        fit: The compatibility chip.
        twins: The other recipes with the same look.
        hotkey: The number key that applies it.
    """

    name: str
    summary: str = ""
    comment: str = ""
    texture: Any = None
    is_baseline: bool = False
    fit: Fit | None = None
    twins: list[str] = field(default_factory=list)
    hotkey: int | None = None


@Gtk.Template(string=_UI)
class RecipeRow(Adw.ActionRow):
    """A recipe's picture, name and chips.

    Attributes:
        name: The recipe shown.
    """

    __gtype_name__ = "GrawjiRecipeRow"

    thumb = Gtk.Template.Child()
    baseline_chip = Gtk.Template.Child()
    fit_chip = Gtk.Template.Child()
    duplicate_chip = Gtk.Template.Child()
    hotkey_chip = Gtk.Template.Child()
    click = Gtk.Template.Child()
    menu_click = Gtk.Template.Child()
    drag_source = Gtk.Template.Child()
    drop_target = Gtk.Template.Child()

    def __init__(self, content: RowContent, **kwargs: Any) -> None:
        """Build the row of one recipe."""
        super().__init__(**kwargs)
        self.name = content.name
        self.set_title(content.name)
        self.set_subtitle(content.summary)
        if content.comment:
            self.set_tooltip_text(content.comment)
        self.thumb.set_texture(content.texture)
        self.baseline_chip.set_visible(content.is_baseline)
        self._show_fit(content.fit)
        self.duplicate_chip.set_visible(bool(content.twins))
        self.duplicate_chip.set_tooltip_text(
            f"Same look as: {', '.join(content.twins)}"
        )
        key = content.hotkey
        self.hotkey_chip.set_visible(key is not None)
        self.hotkey_chip.set_label(f"key {key}")
        self.hotkey_chip.set_tooltip_text(
            f"Press {key} in the main window to apply this recipe"
        )

    def _show_fit(self, fit: Fit | None) -> None:
        """Show how the recipe fits the connected body."""
        self.fit_chip.set_visible(fit is not None)
        for css in _FIT_CSS:
            self.fit_chip.remove_css_class(css)
        if fit is not None:
            self.fit_chip.set_label(fit.label)
            self.fit_chip.add_css_class(fit.css)
            self.fit_chip.set_tooltip_text(fit.tooltip)
