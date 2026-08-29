"""Bake CropRotate geometry into pixbuf pixels."""

from __future__ import annotations

import math
from typing import Any

import cairo
import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GdkPixbuf

from grawji.crop import FULL_RECT, CropRotate, rotated_size

_ROTATIONS = {
    90: GdkPixbuf.PixbufRotation.CLOCKWISE,
    180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
    270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
}


def orient_pixbuf(pixbuf: Any, orientation: int) -> Any:
    """Apply a coarse 90-degree orientation to a pixbuf."""
    rotation = _ROTATIONS.get(orientation)
    return pixbuf.rotate_simple(rotation) if rotation else pixbuf


def bake_pixbuf(pixbuf: Any, crop: CropRotate, *, rect: bool = True) -> Any:
    """Return pixbuf with the geometry applied to its pixels.

    Args:
        pixbuf: The exif-oriented source pixbuf.
        crop: The geometry to apply.
        rect: When False the crop rect is skipped and the whole rotated
            frame is returned.
    """
    pixbuf = orient_pixbuf(pixbuf, crop.orientation)
    use_rect = crop.rect if rect else FULL_RECT
    if crop.angle == 0.0 and use_rect == FULL_RECT:
        return pixbuf
    w, h = pixbuf.get_width(), pixbuf.get_height()
    bw, bh = rotated_size(w, h, crop.angle)
    x, y, rw, rh = use_rect
    cw = max(1, round(rw * bw))
    ch = max(1, round(rh * bh))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw, ch)
    ctx = cairo.Context(surface)
    ctx.translate(bw / 2 - x * bw, bh / 2 - y * bh)
    ctx.rotate(math.radians(crop.angle))
    ctx.translate(-w / 2, -h / 2)
    Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
    ctx.get_source().set_filter(cairo.FILTER_GOOD)
    ctx.paint()
    surface.flush()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, cw, ch)
