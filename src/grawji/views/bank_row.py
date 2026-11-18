"""One custom bank or FS dial position of the connected body."""

from __future__ import annotations

from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from grawji.views.recipe_thumb import RecipeThumb

_UI = (
    resources.files("grawji")
    .joinpath("ui", "bank_row.ui")
    .read_text(encoding="utf-8")
)

_HINT = "Drop a recipe here"

_ = RecipeThumb


@Gtk.Template(string=_UI)
class BankRow(Adw.ActionRow):
    """A drop target row: the slot, its name and the recipe going in.

    Attributes:
        loaded: The name the camera reported for the bank.
        dropped: The name the latest dropped recipe gave the bank.
    """

    __gtype_name__ = "GrawjiBankRow"

    slot_label = Gtk.Template.Child()
    thumb = Gtk.Template.Child()
    clear_button = Gtk.Template.Child()
    edit_button = Gtk.Template.Child()

    def __init__(self, slot: str, *, renamable: bool = True) -> None:
        """Build the empty row of a slot such as C1 or FS2."""
        super().__init__()
        self.slot_label.set_label(slot)
        self.edit_button.set_visible(renamable)
        self.loaded = ""
        self.dropped: str | None = None
        self._named = renamable
        self._recipe: str | None = None
        self._note = ""
        self._update()

    @property
    def bank_name(self) -> str:
        """The bank name shown, which a transfer would write."""
        return str(self.get_title())

    def set_bank_name(self, name: str) -> None:
        """Show a bank name."""
        self.set_title(name)
        self._update()

    def assign(self, recipe: str, note: str, texture: Any) -> None:
        """Show the recipe a transfer would write into the slot."""
        self._recipe = recipe
        self._note = note
        if not self._named:
            self.set_title(recipe)
        self.thumb.set_texture(texture)
        self.thumb.set_visible(texture is not None)
        self.clear_button.set_visible(True)
        self.slot_label.add_css_class("accent")
        self._update()

    def clear_assignment(self) -> None:
        """Show the slot without a pending recipe."""
        self._recipe = None
        self._note = ""
        if not self._named:
            self.set_title("")
        self.thumb.set_visible(False)
        self.clear_button.set_visible(False)
        self.slot_label.remove_css_class("accent")
        self._update()

    def _update(self) -> None:
        """Say in the second line what a transfer does to the slot."""
        recipe = self._recipe
        if recipe is None:
            text = "" if self.bank_name else _HINT
            self.set_tooltip_text(None)
        else:
            if recipe != self.bank_name:
                text = f"→ {recipe}"
            elif self.loaded and self.loaded != recipe:
                text = f"replaces {self.loaded}"
            else:
                text = "will be written"
            text += self._note
            self.set_tooltip_text(f"{recipe}{self._note}")
        self.set_subtitle(text)
