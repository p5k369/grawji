"""One recipe tried on the open image."""

from __future__ import annotations

from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from grawji.views.recipe_thumb import RecipeThumb

_UI = (
    resources.files("grawji")
    .joinpath("ui", "tryout_card.ui")
    .read_text(encoding="utf-8")
)

_ = RecipeThumb

_PLACEHOLDER_SHAPE = 2 / 3
_TALLEST_SHAPE = 4 / 3


@Gtk.Template(string=_UI)
class TryoutCard(Gtk.Box):
    """A recipe's name under the open image rendered with it.

    Attributes:
        name: The recipe shown.
        has_picture: Whether the render has arrived.
    """

    __gtype_name__ = "GrawjiTryoutCard"

    thumb = Gtk.Template.Child()
    title = Gtk.Template.Child()
    summary = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()

    def __init__(self, name: str, summary: str = "", **kwargs: Any) -> None:
        """Build the card of one recipe, still without its picture."""
        super().__init__(**kwargs)
        self.name = name
        self.title.set_label(name)
        self.summary.set_label(summary)
        self.has_picture = False
        self._width = int(self.thumb.tile_width)
        self._shape = _PLACEHOLDER_SHAPE

    def set_picture(self, texture: Any) -> None:
        """Show the render, the tile taking the picture's shape."""
        shape = texture.get_height() / texture.get_width()
        self._shape = min(shape, _TALLEST_SHAPE)
        self.thumb.set_texture(texture)
        self.has_picture = True
        self._resize()

    def set_width(self, width: int) -> None:
        """Draw the picture this wide."""
        if width != self._width:
            self._width = width
            self._resize()

    def _resize(self) -> None:
        """Give the tile its width and the picture's shape."""
        self.thumb.set_tile_size(self._width, round(self._width * self._shape))
