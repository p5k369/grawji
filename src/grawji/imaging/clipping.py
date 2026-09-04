"""Clipping detection: mark blown highlights and crushed shadows."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib

# A channel at or above this is a blown highlight.
HIGHLIGHT_MIN = 250
SHADOW_MAX = 5
# The overlay/analysis works at this longest edge to bound the per-pixel cost.
_MAX_EDGE = 900
# Overlay mark colors (R, G, B, A). Semi-opaque so texture shows through.
_HIGHLIGHT_RGBA = (255, 40, 40, 200)
_SHADOW_RGBA = (60, 120, 255, 200)
_CLEAR = (0, 0, 0, 0)


def _downscaled(pixbuf: Any, max_edge: int) -> Any:
    """A copy no larger than max_edge on the long edge."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    longer = max(width, height)
    if longer <= max_edge:
        return pixbuf
    scale = max_edge / longer
    return pixbuf.scale_simple(
        max(1, round(width * scale)),
        max(1, round(height * scale)),
        GdkPixbuf.InterpType.BILINEAR,
    )


def clip_fractions(
    pixbuf: Any,
    *,
    highlight_min: int = HIGHLIGHT_MIN,
    shadow_max: int = SHADOW_MAX,
    max_edge: int = _MAX_EDGE,
) -> tuple[float, float]:
    """Return the highlight and shadow clipped fractions of an image."""
    scaled = _downscaled(pixbuf, max_edge)
    width, height = scaled.get_width(), scaled.get_height()
    data = scaled.get_pixels()
    stride = scaled.get_rowstride()
    channels = scaled.get_n_channels()
    total = width * height
    if total == 0:  # pragma: no cover
        return 0.0, 0.0

    highlights = shadows = 0
    for y in range(height):
        base = y * stride
        for x in range(width):
            i = base + x * channels
            top = max(data[i], data[i + 1], data[i + 2])
            if top <= shadow_max:
                shadows += 1
            elif top >= highlight_min:
                highlights += 1
    return highlights / total, shadows / total


def clip_overlay(
    pixbuf: Any,
    *,
    highlight_min: int = HIGHLIGHT_MIN,
    shadow_max: int = SHADOW_MAX,
    max_edge: int = _MAX_EDGE,
    highlights: bool = True,
    shadows: bool = True,
) -> Any:
    """Build an RGBA overlay marking clipped pixels, or None if none."""
    if not (highlights or shadows):
        return None
    scaled = _downscaled(pixbuf, max_edge)
    width, height = scaled.get_width(), scaled.get_height()
    data = scaled.get_pixels()
    stride = scaled.get_rowstride()
    channels = scaled.get_n_channels()

    out = bytearray(width * height * 4)
    marked = False
    for y in range(height):
        src = y * stride
        dst = y * width * 4
        for x in range(width):
            i = src + x * channels
            top = max(data[i], data[i + 1], data[i + 2])
            if shadows and top <= shadow_max:
                pixel = _SHADOW_RGBA
                marked = True
            elif highlights and top >= highlight_min:
                pixel = _HIGHLIGHT_RGBA
                marked = True
            else:
                pixel = _CLEAR
            out[dst : dst + 4] = bytes(pixel)
            dst += 4

    if not marked:
        return None
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        width,
        height,
        width * 4,
    )


__all__ = [
    "HIGHLIGHT_MIN",
    "SHADOW_MAX",
    "clip_fractions",
    "clip_overlay",
]
