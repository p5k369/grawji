"""A render copy for 16-bit samples instead of 8-bit pixbufs."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import cast

import lsdetect
import numpy as np
from numpy.typing import NDArray

from grawji.crop import FULL_RECT, CropRotate, rotated_size
from grawji.imaging.heif_codec import DecodedImage
from grawji.imaging.pixbufs import area_resize
from grawji.keystone import Keystone, frame_transform, homography

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
    """Apply the keystone warp, orientation, straightening and the crop."""
    if crop.has_warp:
        samples = _warp(
            samples,
            Keystone(
                crop.keystone_rotation,
                crop.lensshift_v,
                crop.lensshift_h,
                crop.shear,
            ),
        )
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


def _warp(samples: Samples, keystone: Keystone) -> Samples:
    """Perspective-warp samples into their frame."""
    height, width = samples.shape[:2]
    inverse = np.linalg.inv(homography(keystone, width, height))
    scale, off_x, off_y = frame_transform(keystone, width, height)
    matrix = cast(
        "tuple[float, float, float, float, float, float, float, float, float]",
        tuple(float(value) for value in inverse.ravel()),
    )
    contiguous: Samples = np.ascontiguousarray(samples)
    return lsdetect.warp_rgb(
        contiguous, matrix, scale, off_x, off_y, filter="lanczos3"
    )


def _rotate_crop(samples: Samples, crop: CropRotate) -> Samples:
    """Straighten by crop.angle and cut the crop rect out of the result."""
    height, width = samples.shape[:2]
    frame_w, frame_h = rotated_size(width, height, crop.angle)
    left, top, rect_w, rect_h = crop.rect
    out_w = max(1, round(rect_w * frame_w))
    out_h = max(1, round(rect_h * frame_h))
    radians = math.radians(crop.angle)
    cos, sin = math.cos(radians), math.sin(radians)
    k1 = _HALF + left * frame_w - frame_w / 2
    k2 = _HALF + top * frame_h - frame_h / 2
    cx = cos * k1 + sin * k2 + width / 2 - _HALF
    cy = -sin * k1 + cos * k2 + height / 2 - _HALF
    matrix = cast(
        "tuple[float, float, float, float, float, float, float, float, float]",
        (cos, sin, cx, -sin, cos, cy, 0.0, 0.0, 1.0),
    )
    contiguous: Samples = np.ascontiguousarray(samples)
    return lsdetect.warp_rgb(
        contiguous,
        matrix,
        1.0,
        0.0,
        0.0,
        filter="lanczos3",
        out_size=(out_w, out_h),
    )


def scale_to_edge(samples: Samples, max_edge: int) -> Samples:
    """Downscale so the longer edge is max_edge pixels."""
    height, width = samples.shape[:2]
    longer = max(width, height)
    if max_edge <= 0 or longer <= max_edge:
        return samples
    scale = max_edge / longer
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    return area_resize(samples, out_w, out_h)


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
