"""Small pixbuf helpers shared by the decoding and rendering paths."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf

# EXIF orientation.
_R = GdkPixbuf.PixbufRotation
_ORIENTATIONS = {
    1: (_R.NONE, False),
    2: (_R.NONE, True),
    3: (_R.UPSIDEDOWN, False),
    4: (_R.UPSIDEDOWN, True),
    5: (_R.CLOCKWISE, True),
    6: (_R.CLOCKWISE, False),
    7: (_R.COUNTERCLOCKWISE, True),
    8: (_R.COUNTERCLOCKWISE, False),
}


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


def crop_to_aspect(pixbuf: Any, ratio: float) -> Any:
    """Cut a pixbuf down to the given width over height ratio.

    The camera pads its thumbnail to 4:3 (or at least almost) whatever the
    frame's shape, so the bars are cut mathematically.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if ratio <= 0 or width <= 0 or height <= 0:
        return pixbuf
    wanted_w, wanted_h = width, height
    if width / height > ratio:
        wanted_w = max(1, round(height * ratio))
    else:
        wanted_h = max(1, round(width / ratio))
    if (wanted_w, wanted_h) == (width, height):
        return pixbuf
    return pixbuf.new_subpixbuf(
        (width - wanted_w) // 2, (height - wanted_h) // 2, wanted_w, wanted_h
    )


def orient_exif(pixbuf: Any, orientation: int) -> Any:
    """Rotate/flip a pixbuf per its EXIF orientation."""
    rotation, flip = _ORIENTATIONS.get(orientation, (_R.NONE, False))
    pixbuf = pixbuf.rotate_simple(rotation) or pixbuf
    if flip:
        pixbuf = pixbuf.flip(True) or pixbuf
    return pixbuf
