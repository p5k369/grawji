"""Tests for the pure clipping detection."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf

from grawji.imaging.clipping import clip_overlay


def _flat(value: int, width: int = 16, height: int = 16):
    """A solid RGB pixbuf with every channel at value."""
    pb = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pb.fill((value << 24) | (value << 16) | (value << 8) | 0xFF)
    return pb


def _split(top_value: int, bottom_value: int, height: int = 16):
    """A pixbuf whose top and bottom halves differ."""
    pb = _flat(top_value, 16, height)
    bottom = _flat(bottom_value, 16, height // 2)
    bottom.copy_area(0, 0, 16, height // 2, pb, 0, height // 2)
    return pb


def test_no_clipping_on_a_midtone():
    """A mid-gray image produces no overlay at all."""
    assert clip_overlay(_flat(120)) is None


def test_downscales_above_max_edge():
    """An image larger than max_edge is scaled down before scanning."""
    overlay = clip_overlay(_flat(255, 64, 48), max_edge=16)
    assert overlay is not None
    assert max(overlay.get_width(), overlay.get_height()) <= 16


def test_overlay_marks_clipped_pixels_only():
    """The overlay is opaque where clipped and transparent elsewhere."""
    overlay = clip_overlay(_split(255, 0))
    assert overlay is not None
    assert overlay.get_has_alpha()
    data = overlay.get_pixels()
    stride = overlay.get_rowstride()
    width = overlay.get_width()
    height = overlay.get_height()
    top = data[0:4]
    assert top[3] > 0 and top[0] > top[2]
    bottom_i = (height - 1) * stride
    bottom = data[bottom_i : bottom_i + 4]
    assert bottom[3] > 0 and bottom[2] > bottom[0]
    assert width == overlay.get_width()


def test_overlay_axes_can_be_disabled():
    """Disabling both axes yields no overlay."""
    assert clip_overlay(_flat(255), highlights=False, shadows=False) is None
    assert clip_overlay(_flat(255), highlights=False) is None
    assert clip_overlay(_flat(255), shadows=False) is not None
