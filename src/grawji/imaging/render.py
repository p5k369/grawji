"""Pixel baking for preview and export: orientation, geometry, framing."""

from __future__ import annotations

import math
from typing import Any

import cairo
import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GdkPixbuf, GLib

from grawji.crop import FULL_RECT, CropRotate, rotated_size
from grawji.imaging.thumbnails import orient_exif

_ROTATIONS = {
    90: GdkPixbuf.PixbufRotation.CLOCKWISE,
    180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
    270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
}

# Working size for edge analysis
_GRAY_TARGET = 400


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


def gray_rows(pixbuf: Any, target: int = _GRAY_TARGET) -> list[list[int]]:
    """Downscale a pixbuf and return grayscale rows."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    scale = target / max(width, height)
    if scale < 1.0:
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
        pixbuf = pixbuf.scale_simple(
            width, height, GdkPixbuf.InterpType.BILINEAR
        )
    data = pixbuf.get_pixels()
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    return [
        [
            data[y * stride + x * channels]
            + data[y * stride + x * channels + 1]
            + data[y * stride + x * channels + 2]
            for x in range(width)
        ]
        for y in range(height)
    ]


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


def oriented_jpeg(jpeg: bytes, fallback_orientation: int = 1) -> Any:
    """Decode JPEG bytes upright."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(jpeg)
    loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf.get_option("orientation") is not None:
        return pixbuf.apply_embedded_orientation() or pixbuf
    return orient_exif(pixbuf, fallback_orientation)


def trim_letterbox(pixbuf: Any, threshold: int = 24) -> Any:
    """Cut near-black letterbox bars off every edge of a pixbuf."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    data = pixbuf.get_pixels()
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()

    def row_dark(y: int) -> bool:
        row = data[y * stride : y * stride + width * channels]
        return max(row) < threshold

    def col_dark(x: int) -> bool:
        return all(
            max(
                data[y * stride + x * channels : y * stride + x * channels + 3]
            )
            < threshold
            for y in range(0, height, 4)
        )

    top = 0
    while top < height // 3 and row_dark(top):
        top += 1
    bottom = height
    while bottom > height * 2 // 3 and row_dark(bottom - 1):
        bottom -= 1
    left = 0
    while left < width // 3 and col_dark(left):
        left += 1
    right = width
    while right > width * 2 // 3 and col_dark(right - 1):
        right -= 1
    if (left, top, right, bottom) == (0, 0, width, height):
        return pixbuf
    return pixbuf.new_subpixbuf(left, top, right - left, bottom - top)


def thumb_jpeg(pixbuf: Any, max_edge: int = 256, quality: int = 82) -> bytes:
    """Encode a pixbuf as a small JPEG."""
    scaled = scale_to_edge(pixbuf, max_edge)
    ok, data = scaled.save_to_bufferv("jpeg", ["quality"], [str(quality)])
    if not ok:
        msg = "JPEG encoding failed"
        raise GLib.Error(msg)
    return bytes(data)


def scale_to_edge(pixbuf: Any, max_edge: int) -> Any:
    """Downscale so the longer edge is max_edge pixels."""
    if max_edge <= 0:
        return pixbuf
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    longer = max(width, height)
    if longer <= max_edge:
        return pixbuf
    scale = max_edge / longer
    return pixbuf.scale_simple(
        max(1, round(width * scale)),
        max(1, round(height * scale)),
        GdkPixbuf.InterpType.HYPER,
    )


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
