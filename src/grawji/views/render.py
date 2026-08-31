"""Pixel baking for preview and export: orientation, geometry, framing."""

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


def parse_aspect(label: str) -> float | None:
    """A "W:H" label as a width/height ratio, None for anything else."""
    left, _, right = label.partition(":")
    try:
        return float(left) / float(right)
    except (ValueError, ZeroDivisionError):
        return None


def add_border(
    pixbuf: Any, percent: float, color: str, aspect: float | None = None
) -> Any:
    """Return pixbuf on a solid border."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    border = max(1, round(max(w, h) * percent / 100.0)) if percent > 0 else 0
    total_w = w + 2 * border
    total_h = h + 2 * border
    if aspect is not None and aspect > 0:
        if total_w < total_h * aspect:
            total_w = round(total_h * aspect)
        else:
            total_h = round(total_w / aspect)
    if (total_w, total_h) == (w, h):
        return pixbuf
    rgba = Gdk.RGBA()
    if not rgba.parse(color):
        rgba.parse("#ffffff")
    framed = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB,
        pixbuf.get_has_alpha(),
        8,
        total_w,
        total_h,
    )
    framed.fill(
        (round(rgba.red * 255) << 24)
        | (round(rgba.green * 255) << 16)
        | (round(rgba.blue * 255) << 8)
        | 0xFF
    )
    pixbuf.copy_area(
        0, 0, w, h, framed, (total_w - w) // 2, (total_h - h) // 2
    )
    return framed


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
    if crop.angle == 0.0:
        # A pure crop needs no resampling: cut on integer pixels,
        # byte-exact (the cairo path below would bilinear-shift on
        # fractional offsets).
        x = min(w - 1, max(0, round(use_rect[0] * w)))
        y = min(h - 1, max(0, round(use_rect[1] * h)))
        cw = min(w - x, max(1, round(use_rect[2] * w)))
        ch = min(h - y, max(1, round(use_rect[3] * h)))
        return pixbuf.new_subpixbuf(x, y, cw, ch)
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
