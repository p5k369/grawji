"""Bake a fitted keystone correction into a pixbuf."""

from __future__ import annotations

import math
from typing import Any, Literal

import gi

gi.require_version("GdkPixbuf", "2.0")

import lsdetect
import numpy as np
from gi.repository import GdkPixbuf, GLib

from grawji.crop import rotated_size
from grawji.imaging.pixbufs import pixel_rows
from grawji.keystone import Keystone, frame_transform, homography

_CACHE_SLOTS = 2
_cache: list[tuple[tuple[int, float, float, float, float], Any, Any]] = []


def _key(pixbuf: Any, k: Keystone) -> tuple[int, float, float, float, float]:
    """Cache key."""
    return (id(pixbuf), k.rotation, k.lensshift_v, k.lensshift_h, k.shear)


def warp_cached(pixbuf: Any, correction: Keystone) -> Any:
    """warp_pixbuf, memoized on the pixbuf and the correction."""
    key = _key(pixbuf, correction)
    for slot_key, source, warped in _cache:
        if slot_key == key and source is pixbuf:
            return warped
    warped = warp_pixbuf(pixbuf, correction)
    _cache.insert(0, (key, pixbuf, warped))
    del _cache[_CACHE_SLOTS:]
    return warped


def is_cached(pixbuf: Any, correction: Keystone) -> bool:
    """Whether warp_cached would return instantly."""
    key = _key(pixbuf, correction)
    return any(
        slot_key == key and source is pixbuf
        for slot_key, source, _warped in _cache
    )


def warp_pixbuf(
    pixbuf: Any,
    correction: Keystone,
    resample: Literal["bilinear", "lanczos3"] = "bilinear",
) -> Any:
    """The pixbuf with the correction applied."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    source = pixel_rows(pixbuf)[:, : width * channels].reshape(
        height, width, channels
    )[:, :, :3]
    inverse = np.linalg.inv(homography(correction, width, height))
    scale, off_x, off_y = frame_transform(correction, width, height)
    flat = inverse.ravel()
    matrix = (
        float(flat[0]),
        float(flat[1]),
        float(flat[2]),
        float(flat[3]),
        float(flat[4]),
        float(flat[5]),
        float(flat[6]),
        float(flat[7]),
        float(flat[8]),
    )
    out = lsdetect.warp_rgb(
        np.ascontiguousarray(source),
        matrix,
        float(scale),
        float(off_x),
        float(off_y),
        filter=resample,
    )
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(out.tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        width,
        height,
        width * 3,
    )


def rotate_pixbuf(pixbuf: Any, angle: float, rect: tuple[float, ...]) -> Any:
    """Straighten a pixbuf by angle and cut rect out."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    source = pixel_rows(pixbuf)[:, : width * channels].reshape(
        height, width, channels
    )[:, :, :3]
    frame_w, frame_h = rotated_size(width, height, angle)
    left, top, rect_w, rect_h = rect
    out_w = max(1, round(rect_w * frame_w))
    out_h = max(1, round(rect_h * frame_h))
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    k1 = 0.5 + left * frame_w - frame_w / 2
    k2 = 0.5 + top * frame_h - frame_h / 2
    cx = cos * k1 + sin * k2 + width / 2 - 0.5
    cy = -sin * k1 + cos * k2 + height / 2 - 0.5
    matrix = (cos, sin, cx, -sin, cos, cy, 0.0, 0.0, 1.0)
    out = lsdetect.warp_rgb(
        np.ascontiguousarray(source),
        matrix,
        1.0,
        0.0,
        0.0,
        filter="lanczos3",
        out_size=(out_w, out_h),
    )
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(out.tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        out_w,
        out_h,
        out_w * 3,
    )
