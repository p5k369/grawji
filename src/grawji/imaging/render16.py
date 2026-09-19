"""A render copy for 16-bit samples instead of 8-bit pixbufs."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from grawji.crop import FULL_RECT, CropRotate, rotated_size
from grawji.imaging.heif_codec import DecodedImage

_ROTATIONS = (90, 180, 270)
_TOP_LEFT = 1
# numpy rotates counter-clockwise, the tags count clockwise.
_EXIF_FLIPS: dict[int, Callable[[Samples], Samples]] = {
    2: lambda a: a[:, ::-1],
    3: lambda a: a[::-1, ::-1],
    4: lambda a: a[::-1],
    5: lambda a: a.transpose(1, 0, 2),
    6: lambda a: np.rot90(a, -1),
    7: lambda a: a.transpose(1, 0, 2)[::-1, ::-1],
    8: lambda a: np.rot90(a, 1),
}
_CHANNELS = 3
# Half a pixel
_HALF = 0.5
# Output rows resampled at a time, to bound the working set.
_BAND_ROWS = 256
Samples = NDArray[np.uint16]


def as_array(image: DecodedImage) -> Samples:
    """View decoded HEIF pixels as a height by width by RGB array."""
    words = np.frombuffer(image.pixels, dtype="<u2")
    rows = words.reshape(image.height, image.stride // 2)
    return np.ascontiguousarray(
        rows[:, : image.width * _CHANNELS].reshape(
            image.height, image.width, _CHANNELS
        )
    )


def orient(samples: Samples, orientation: int) -> Samples:
    """Apply a coarse 90-degree orientation."""
    if orientation not in _ROTATIONS:
        return samples
    # numpy rotates counter-clockwise, the sidecar angle is clockwise.
    return np.ascontiguousarray(np.rot90(samples, k=-(orientation // 90)))


def exif_orient(samples: Samples, orientation: int) -> Samples:
    """Turn stored samples upright, following an Exif orientation tag."""
    if orientation == _TOP_LEFT:
        return samples
    flipped = _EXIF_FLIPS.get(orientation)
    if flipped is None:
        return samples
    return np.ascontiguousarray(flipped(samples))


def bake(samples: Samples, crop: CropRotate) -> Samples:
    """Apply orientation, straightening and the crop rect."""
    samples = orient(samples, crop.orientation)
    if crop.angle == 0.0 and crop.rect == FULL_RECT:
        return samples
    height, width = samples.shape[:2]
    if crop.angle == 0.0:
        # A pure crop is exact: cut on whole pixels, no resampling.
        x = min(width - 1, max(0, round(crop.rect[0] * width)))
        y = min(height - 1, max(0, round(crop.rect[1] * height)))
        cut_w = min(width - x, max(1, round(crop.rect[2] * width)))
        cut_h = min(height - y, max(1, round(crop.rect[3] * height)))
        return np.ascontiguousarray(samples[y : y + cut_h, x : x + cut_w])
    return _rotate_crop(samples, crop)


def _rotate_crop(samples: Samples, crop: CropRotate) -> Samples:
    """Straighten by crop.angle and cut the crop rect out of the result."""
    height, width = samples.shape[:2]
    frame_w, frame_h = rotated_size(width, height, crop.angle)
    left, top, rect_w, rect_h = crop.rect
    out_w = max(1, round(rect_w * frame_w))
    out_h = max(1, round(rect_h * frame_h))
    radians = math.radians(crop.angle)
    cos, sin = math.cos(radians), math.sin(radians)
    xs = np.arange(out_w, dtype=np.float32) + _HALF + left * frame_w
    xs -= frame_w / 2
    out = np.empty((out_h, out_w, _CHANNELS), dtype=np.uint16)
    for start in range(0, out_h, _BAND_ROWS):
        stop = min(start + _BAND_ROWS, out_h)
        ys = np.arange(start, stop, dtype=np.float32) + _HALF + top * frame_h
        ys -= frame_h / 2
        grid_x, grid_y = xs[None, :], ys[:, None]
        src_x = cos * grid_x + sin * grid_y + width / 2 - _HALF
        src_y = -sin * grid_x + cos * grid_y + height / 2 - _HALF
        out[start:stop] = _sample_bilinear(samples, src_x, src_y)
    return out


def _sample_bilinear(
    samples: Samples, src_x: NDArray[np.float32], src_y: NDArray[np.float32]
) -> Samples:
    """Bilinear lookup of float coordinates, edges clamped."""
    height, width = samples.shape[:2]
    x0 = np.floor(src_x).astype(np.int32)
    y0 = np.floor(src_y).astype(np.int32)
    fx = (src_x - x0).astype(np.float32)[..., None]
    fy = (src_y - y0).astype(np.float32)[..., None]
    x0c = np.clip(x0, 0, width - 1)
    x1c = np.clip(x0 + 1, 0, width - 1)
    y0c = np.clip(y0, 0, height - 1)
    y1c = np.clip(y0 + 1, 0, height - 1)
    top = samples[y0c, x0c].astype(np.float32)
    top += (samples[y0c, x1c].astype(np.float32) - top) * fx
    bottom = samples[y1c, x0c].astype(np.float32)
    bottom += (samples[y1c, x1c].astype(np.float32) - bottom) * fx
    top += (bottom - top) * fy
    # Outside the source the frame is black
    inside = (
        (src_x >= -_HALF)
        & (src_x <= width - _HALF)
        & (src_y >= -_HALF)
        & (src_y <= height - _HALF)
    )
    top *= inside[..., None]
    return np.rint(top).astype(np.uint16)


def scale_to_edge(samples: Samples, max_edge: int) -> Samples:
    """Downscale so the longer edge is max_edge pixels."""
    height, width = samples.shape[:2]
    longer = max(width, height)
    if max_edge <= 0 or longer <= max_edge:
        return samples
    scale = max_edge / longer
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    x_edges = np.linspace(0, width, out_w + 1).round().astype(np.int32)
    y_edges = np.linspace(0, height, out_h + 1).round().astype(np.int32)
    x_counts = np.diff(x_edges)[None, :, None]
    out = np.empty((out_h, out_w, _CHANNELS), dtype=np.uint16)
    for start in range(0, out_h, _BAND_ROWS):
        stop = min(start + _BAND_ROWS, out_h)
        band = samples[y_edges[start] : y_edges[stop]]
        rows = np.add.reduceat(
            band.astype(np.float32),
            y_edges[start:stop] - y_edges[start],
            axis=0,
        )
        rows /= np.diff(y_edges[start : stop + 1])[:, None, None]
        cols = np.add.reduceat(rows, x_edges[:-1], axis=1)
        cols /= x_counts
        out[start:stop] = np.rint(cols).astype(np.uint16)
    return out


def add_border(
    samples: Samples,
    percent: float,
    color: tuple[int, int, int],
    aspect: float | None = None,
) -> Samples:
    """Place the image on a solid border, optionally padded to aspect."""
    height, width = samples.shape[:2]
    border = (
        max(1, round(max(width, height) * percent / 100.0))
        if percent > 0
        else 0
    )
    total_w = width + 2 * border
    total_h = height + 2 * border
    if aspect is not None and aspect > 0:
        if total_w < total_h * aspect:
            total_w = round(total_h * aspect)
        else:
            total_h = round(total_w / aspect)
    if (total_w, total_h) == (width, height):
        return samples
    framed = np.empty((total_h, total_w, _CHANNELS), dtype=np.uint16)
    framed[:, :] = np.array(color, dtype=np.uint16)
    x = (total_w - width) // 2
    y = (total_h - height) // 2
    framed[y : y + height, x : x + width] = samples
    return framed


def trim_even(samples: Samples) -> Samples:
    """Drop a last odd row or column."""
    height, width = samples.shape[:2]
    if width % 2 == 0 and height % 2 == 0:
        return samples
    return np.ascontiguousarray(
        samples[: height - height % 2, : width - width % 2]
    )


def scale_color(
    color: tuple[int, int, int], bits: int
) -> tuple[int, int, int]:
    """Lift an 8-bit border color to the sample depth in use."""
    top = (1 << bits) - 1
    return (
        round(color[0] * top / 255),
        round(color[1] * top / 255),
        round(color[2] * top / 255),
    )
