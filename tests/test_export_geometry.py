"""Batch export applies a RAF's geometry sidecar when one exists."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GdkPixbuf

from grawji.crop import CropRotate, save_sidecar
from grawji.views.export import sidecar_decode


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
    save_sidecar(raf, CropRotate())  # identity writes no file
    assert sidecar_decode(str(raf)) is None


def test_sidecar_decode_applies_geometry(tmp_path: Path) -> None:
    """A stored crop/rotation is baked into the decoded pixels."""
    raf = tmp_path / "DSCF0002.RAF"
    raf.write_bytes(b"raf")
    save_sidecar(
        raf,
        CropRotate(orientation=90, rect=(0.25, 0.25, 0.5, 0.5)),
    )
    decode = sidecar_decode(str(raf))
    assert decode is not None
    pixbuf = decode(_jpeg_bytes(600, 400))
    assert (pixbuf.get_width(), pixbuf.get_height()) == (200, 300)
