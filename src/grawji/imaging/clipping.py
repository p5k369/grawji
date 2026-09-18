"""Clipping detection."""

from __future__ import annotations

from typing import Any

import gi
import numpy as np

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib

from grawji.imaging.render import pixel_rows

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
    channels = scaled.get_n_channels()

    rows = pixel_rows(scaled)[:, : width * channels]
    rgb = rows.reshape(height, width, channels)
    brightest = rgb[:, :, :3].max(axis=2)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    marked = False
    if shadows:
        crushed = brightest <= shadow_max
        out[crushed] = _SHADOW_RGBA
        marked = bool(crushed.any())
    if highlights:
        blown = brightest >= highlight_min
        out[blown] = _HIGHLIGHT_RGBA
        marked = marked or bool(blown.any())

    if not marked:
        return None
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(out.tobytes()),
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
    "clip_overlay",
]
