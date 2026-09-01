"""Tests for the small imaging helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
gi.require_version("GExiv2", "0.10")

from gi.repository import GdkPixbuf, GExiv2

from grawji.imaging.imagemeta import camera_model, exif_orientation, exif_rows
from grawji.imaging.render import gray_rows, parse_aspect, texture_for_pixbuf


def _flat_pixbuf(width: int, height: int, value: int) -> GdkPixbuf.Pixbuf:
    """A solid-gray RGB pixbuf."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill((value << 24) | (value << 16) | (value << 8) | 0xFF)
    return pixbuf


def _tagged_jpeg(tmp_path: Path, tags: dict[str, str]) -> Path:
    """A tiny JPEG carrying the given EXIF string tags."""
    out = tmp_path / "tagged.jpg"
    _flat_pixbuf(8, 8, 128).savev(str(out), "jpeg", [], [])
    meta = GExiv2.Metadata()
    meta.open_path(str(out))
    for tag, value in tags.items():
        meta.try_set_tag_string(tag, value)
    meta.save_file(str(out))
    return out


def test_texture_matches_pixbuf_dimensions() -> None:
    """The GPU texture mirrors the pixbuf's size."""
    texture = texture_for_pixbuf(_flat_pixbuf(6, 4, 10))
    assert (texture.get_width(), texture.get_height()) == (6, 4)


def test_gray_rows_shape_and_downscale() -> None:
    """Rows come back at the downscaled size with summed channels."""
    rows = gray_rows(_flat_pixbuf(40, 20, 100), target=10)
    assert len(rows) == 5
    assert all(len(row) == 10 for row in rows)
    assert rows[0][0] == 300  # r+g+b of the flat gray


def test_gray_rows_never_upscales() -> None:
    """A pixbuf smaller than the target keeps its size."""
    rows = gray_rows(_flat_pixbuf(4, 3, 10), target=100)
    assert (len(rows), len(rows[0])) == (3, 4)


def test_parse_aspect() -> None:
    """W:H labels parse to a ratio, everything else to None."""
    assert parse_aspect("3:2") == pytest.approx(1.5)
    assert parse_aspect("Free") is None
    assert parse_aspect("1:0") is None


def test_exif_orientation_reads_the_tag(tmp_path: Path) -> None:
    """The orientation tag is read, absent files default to normal."""
    jpeg = _tagged_jpeg(tmp_path, {"Exif.Image.Orientation": "6"})
    assert exif_orientation(str(jpeg)) == 6
    assert exif_orientation(str(tmp_path / "missing.jpg")) == 1


def test_camera_model_reads_the_tag(tmp_path: Path) -> None:
    """The model tag is read, absent files return None."""
    jpeg = _tagged_jpeg(tmp_path, {"Exif.Image.Model": "X-E5"})
    assert camera_model(str(jpeg)) == "X-E5"
    assert camera_model(str(tmp_path / "missing.jpg")) is None


def test_exif_rows_formats_known_tags(tmp_path: Path) -> None:
    """exif_rows turns raw tags into display label/value pairs."""
    jpeg = _tagged_jpeg(
        tmp_path,
        {
            "Exif.Image.Model": "X-E5",
            "Exif.Photo.FocalLength": "3500/100",
        },
    )
    rows = dict(exif_rows(jpeg.read_bytes()))
    assert rows["Camera"] == "X-E5"
    assert rows["Focal length"] == "35 mm"


def test_exif_rows_tolerates_garbage() -> None:
    """Unparseable bytes yield an empty list, not an exception."""
    assert exif_rows(b"not a jpeg") == []
