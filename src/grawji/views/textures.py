"""GPU texture creation for the view layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


def texture_for_pixbuf(pixbuf: Any) -> Gdk.Texture:
    """A GPU texture from a pixbuf."""
    fmt = (
        Gdk.MemoryFormat.R8G8B8A8
        if pixbuf.get_has_alpha()
        else Gdk.MemoryFormat.R8G8B8
    )
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(),
        pixbuf.get_height(),
        fmt,
        pixbuf.read_pixel_bytes(),
        pixbuf.get_rowstride(),
    )


def texture_for_rgb(pixels: NDArray[np.uint8]) -> Gdk.Texture:
    """A GPU texture from a height by width by 3 RGB array."""
    height, width = pixels.shape[:2]
    return Gdk.MemoryTexture.new(
        width,
        height,
        Gdk.MemoryFormat.R8G8B8,
        GLib.Bytes.new(pixels[..., :3].tobytes()),
        width * 3,
    )
