"""Batch export applies a RAF's geometry sidecar when one exists."""

from __future__ import annotations

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GdkPixbuf

from grawji.crop import CropRotate
from grawji.settings import Settings
from grawji.sidecar import save_crop
from grawji.views.export import sidecar_decode, with_border
from grawji.views.render import add_border


def _jpeg_bytes(width: int, height: int) -> bytes:
    """Encode a plain filled image as JPEG bytes."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill(0xFF8800FF)
    ok, buffer = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    assert ok
    return bytes(buffer)


def test_sidecar_decode_without_sidecar(tmp_path: Path) -> None:
    """No sidecar (or identity) means the camera bytes pass through."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    assert sidecar_decode(str(raf)) is None
    save_crop(raf, CropRotate())  # identity writes no file
    assert sidecar_decode(str(raf)) is None


def test_sidecar_decode_applies_geometry(tmp_path: Path) -> None:
    """A stored crop/rotation is baked into the decoded pixels."""
    raf = tmp_path / "DSCF0002.RAF"
    raf.write_bytes(b"raf")
    save_crop(
        raf,
        CropRotate(orientation=90, rect=(0.25, 0.25, 0.5, 0.5)),
    )
    decode = sidecar_decode(str(raf))
    assert decode is not None
    pixbuf = decode(_jpeg_bytes(600, 400))
    assert (pixbuf.get_width(), pixbuf.get_height()) == (200, 300)


def test_add_border_dimensions_and_color() -> None:
    """The border adds percent-of-longer-edge on all four sides."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 600, 400)
    pixbuf.fill(0xFF8800FF)
    out = add_border(pixbuf, 5.0, "#000000")
    # 5% of 600 = 30 on each side.
    assert (out.get_width(), out.get_height()) == (660, 460)
    data, stride, n = (
        out.get_pixels(),
        out.get_rowstride(),
        out.get_n_channels(),
    )

    def px(x, y):
        offset = y * stride + x * n
        return tuple(data[offset : offset + 3])

    assert px(2, 2) == (0, 0, 0)
    assert px(660 - 3, 460 - 3) == (0, 0, 0)
    assert px(330, 230) == (255, 136, 0)
    assert add_border(pixbuf, 0.0, "#000000") is pixbuf
    white = add_border(pixbuf, 5.0, "not-a-color")
    wdata, wstride, wn = (
        white.get_pixels(),
        white.get_rowstride(),
        white.get_n_channels(),
    )
    assert tuple(wdata[2 * wstride + 2 * wn : 2 * wstride + 2 * wn + 3]) == (
        255,
        255,
        255,
    )


def test_with_border_wraps_only_when_enabled() -> None:
    """with_border is a passthrough unless framing would do anything."""
    decode = lambda jpeg: jpeg  # noqa: E731
    assert with_border(decode, Settings()) is decode
    disabled = Settings(export_border_enabled=False, export_border_percent=5)
    assert with_border(decode, disabled) is decode
    noop = Settings(export_border_enabled=True, export_border_percent=0.0)
    assert with_border(decode, noop) is decode
    on = Settings(
        export_border_enabled=True,
        export_border_percent=5.0,
        export_border_color="#000000",
    )
    wrapped = with_border(decode, on)
    assert wrapped is not decode
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 200, 100)
    pixbuf.fill(0xFF8800FF)
    out = with_border(lambda _j: pixbuf, on)(b"")
    assert (out.get_width(), out.get_height()) == (220, 120)


def test_add_border_pads_to_aspect() -> None:
    """The aspect fill extends the framed canvas, image centered."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 600, 400)
    pixbuf.fill(0xFF8800FF)
    out = add_border(pixbuf, 5.0, "#000000", 1.0)
    assert (out.get_width(), out.get_height()) == (660, 660)
    data, stride, n = (
        out.get_pixels(),
        out.get_rowstride(),
        out.get_n_channels(),
    )

    def px(x, y):
        offset = y * stride + x * n
        return tuple(data[offset : offset + 3])

    assert px(330, 5) == (0, 0, 0)
    assert px(330, 330) == (255, 136, 0)
    square = add_border(pixbuf, 0.0, "#000000", 1.0)
    assert (square.get_width(), square.get_height()) == (600, 600)
    assert add_border(pixbuf, 0.0, "#000000", 1.5) is pixbuf
