"""A fixed-size recipe picture for list rows."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk

from grawji.recipes import RecipeLibrary

_RADIUS = 6.0
_EMPTY_ALPHA = 0.08


def thumb_texture(library: RecipeLibrary, name: str) -> Any:
    """Decode a recipe's stored picture into a texture, or None."""
    jpeg = library.thumb_jpeg(name)
    if jpeg is None:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(jpeg))
    except GLib.Error:
        return None


class RecipeThumb(Gtk.Widget):
    """A recipe's picture in a rounded tile of a fixed size."""

    __gtype_name__ = "GrawjiRecipeThumb"

    tile_width = GObject.Property(type=int, default=66)
    tile_height = GObject.Property(type=int, default=44)
    contain = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs: Any) -> None:
        """Build an empty tile."""
        super().__init__(**kwargs)
        self._texture: Any = None

    def set_texture(self, texture: Any) -> None:
        """Show the texture."""
        self._texture = texture
        self.queue_draw()

    def set_tile_size(self, width: int, height: int) -> None:
        """Change the tile's size."""
        self.tile_width = width
        self.tile_height = height
        self.queue_resize()

    def do_measure(
        self, orientation: Gtk.Orientation, _for_size: int
    ) -> tuple[int, int, int, int]:
        """Request the fixed tile size."""
        if orientation == Gtk.Orientation.HORIZONTAL:
            size = self.tile_width
        else:
            size = self.tile_height
        return size, size, -1, -1

    def do_snapshot(self, snapshot: Any) -> None:
        """Draw the picture into the tile."""
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        outline = Gsk.RoundedRect()
        outline.init_from_rect(bounds, _RADIUS)
        snapshot.push_rounded_clip(outline)
        texture = self._texture
        if texture is None or self.contain:
            color = self.get_color()
            color.alpha = _EMPTY_ALPHA
            snapshot.append_color(color, bounds)
        if texture is not None:
            fit = min if self.contain else max
            scale = fit(
                width / texture.get_width(), height / texture.get_height()
            )
            drawn_w = texture.get_width() * scale
            drawn_h = texture.get_height() * scale
            snapshot.save()
            snapshot.translate(
                Graphene.Point().init(
                    (width - drawn_w) / 2, (height - drawn_h) / 2
                )
            )
            texture.snapshot(snapshot, drawn_w, drawn_h)
            snapshot.restore()
        snapshot.pop()
