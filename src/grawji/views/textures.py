"""GPU texture creation for the view layer."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib


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
        GLib.Bytes.new(pixbuf.get_pixels()),
        pixbuf.get_rowstride(),
    )
