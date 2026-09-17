"""imaging.render16 must frame exactly like imaging.render."""

from __future__ import annotations

import gi
import numpy as np
import pytest

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf

from grawji.crop import CropRotate
from grawji.imaging import render, render16

_GEOMETRIES = [
    CropRotate(),
    CropRotate(rect=(0.25, 0.1, 0.5, 0.6)),
    CropRotate(orientation=90),
    CropRotate(orientation=180, rect=(0.0, 0.0, 0.5, 0.5)),
    CropRotate(angle=5.65, rect=(0.2, 0.1, 0.5, 0.7)),
    CropRotate(angle=-12.0),
]


def sample_pixbuf(width: int = 240, height: int = 160):
    """A gradient pixbuf, deterministic and asymmetric."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixels = bytearray(pixbuf.get_rowstride() * height)
    stride, channels = pixbuf.get_rowstride(), pixbuf.get_n_channels()
    for y in range(height):
        for x in range(width):
            index = y * stride + x * channels
            pixels[index] = x % 256
            pixels[index + 1] = y % 256
            pixels[index + 2] = (x + y) % 256
    return GdkPixbuf.Pixbuf.new_from_bytes(
        __import__("gi").repository.GLib.Bytes.new(bytes(pixels)),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        width,
        height,
        stride,
    )


def as_samples(pixbuf) -> render16.Samples:
    """The same pixels as a 16-bit array, values left in 0 to 255."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride, channels = pixbuf.get_rowstride(), pixbuf.get_n_channels()
    data = np.frombuffer(pixbuf.get_pixels(), dtype=np.uint8)
    out = np.empty((height, width, 3), dtype=np.uint16)
    for y in range(height):
        start = y * stride
        row = data[start : start + width * channels]
        out[y] = row.reshape(width, channels)[:, :3]
    return out


@pytest.mark.parametrize("crop", _GEOMETRIES)
def test_bake_matches_the_pixbuf_path(crop):
    """Both paths produce the same framing for the same geometry."""
    pixbuf = sample_pixbuf()
    baked = render.bake_pixbuf(pixbuf, crop)
    samples = render16.bake(as_samples(pixbuf), crop)
    assert (samples.shape[1], samples.shape[0]) == (
        baked.get_width(),
        baked.get_height(),
    )


@pytest.mark.parametrize("crop", _GEOMETRIES[:4])
def test_bake_keeps_the_pixels_for_exact_geometry(crop):
    """Without resampling the samples are the pixbuf's, unchanged."""
    pixbuf = sample_pixbuf()
    baked = render.bake_pixbuf(pixbuf, crop)
    samples = render16.bake(as_samples(pixbuf), crop)
    assert np.array_equal(samples, as_samples(baked))


@pytest.mark.parametrize("max_edge", [0, 1000, 120, 37])
def test_scale_to_edge_matches_the_pixbuf_path(max_edge):
    """The long-edge limit lands on the same output size."""
    pixbuf = sample_pixbuf()
    scaled = render.scale_to_edge(pixbuf, max_edge)
    samples = render16.scale_to_edge(as_samples(pixbuf), max_edge)
    assert (samples.shape[1], samples.shape[0]) == (
        scaled.get_width(),
        scaled.get_height(),
    )


@pytest.mark.parametrize(
    ("percent", "aspect"), [(0.0, None), (4.0, None), (3.0, 1.0), (0.0, 2.0)]
)
def test_add_border_matches_the_pixbuf_path(percent, aspect):
    """Border and aspect padding agree on the framed size."""
    pixbuf = sample_pixbuf()
    framed = render.add_border(pixbuf, percent, "#ffffff", aspect)
    samples = render16.add_border(
        as_samples(pixbuf), percent, (255, 255, 255), aspect
    )
    assert (samples.shape[1], samples.shape[0]) == (
        framed.get_width(),
        framed.get_height(),
    )


def test_border_color_is_lifted_to_the_sample_depth():
    """White stays white when the samples are 10-bit."""
    assert render16.scale_color((255, 255, 255), 10) == (1023, 1023, 1023)
    assert render16.scale_color((0, 0, 0), 10) == (0, 0, 0)
    assert render16.scale_color((128, 64, 0), 8) == (128, 64, 0)


def test_trim_even_drops_only_odd_edges():
    """The encoder pads odd sizes, so odd edges are trimmed first."""
    odd = np.zeros((7, 5, 3), dtype=np.uint16)
    assert render16.trim_even(odd).shape == (6, 4, 3)
    even = np.zeros((6, 4, 3), dtype=np.uint16)
    assert render16.trim_even(even) is even


@pytest.mark.parametrize("crop", [_GEOMETRIES[4], _GEOMETRIES[5]])
def test_straightening_matches_the_pixbuf_path(crop):
    """A resampled rotation lands on the same pixels, within rounding."""
    pixbuf = sample_pixbuf()
    baked = as_samples(render.bake_pixbuf(pixbuf, crop)).astype(float)
    samples = render16.bake(as_samples(pixbuf), crop).astype(float)
    assert samples.shape == baked.shape
    difference = np.abs(samples - baked)
    assert difference.mean() < 2.0
    assert np.percentile(difference, 99) <= 8
