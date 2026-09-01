"""GExiv2-backed metadata helpers for JPEG bytes and RAF files."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import gi

gi.require_version("GExiv2", "0.10")

from gi.repository import GExiv2, GLib

from grawji import exif, raf


def exif_rows(jpeg: bytes) -> list[tuple[str, str]]:
    """Read raw EXIF tags from JPEG bytes and format them for display."""
    GExiv2.initialize()
    meta = GExiv2.Metadata()
    try:
        meta.open_buf(jpeg)
    except GLib.Error:
        return []
    raw = {}
    for _label, tag, _fmt in exif.EXIF_FIELDS:
        try:
            value = meta.try_get_tag_string(tag)
        except GLib.Error:
            value = None
        if value:
            raw[tag] = value
    return exif.format_exif(raw)


def native_size(path: str) -> tuple[int, int] | None:
    """The RAF's delivered pixel size, oriented for display, or None."""
    size = raf.output_size(path)
    meta = GExiv2.Metadata()
    try:
        meta.open_path(path)
    except GLib.Error:
        return size

    def read_pair(width_tag: str, height_tag: str) -> tuple[int, int] | None:
        try:
            width = meta.try_get_tag_long(width_tag)
            height = meta.try_get_tag_long(height_tag)
        except GLib.Error:
            return None
        if width > 0 and height > 0:
            return int(width), int(height)
        return None

    if size is None:
        size = read_pair(
            "Exif.Photo.PixelXDimension", "Exif.Photo.PixelYDimension"
        )
    if size is None:
        size = read_pair(
            "Exif.Fujifilm.RawImageFullWidth",
            "Exif.Fujifilm.RawImageFullHeight",
        )
    if size is None:
        size = read_pair("Exif.Image.ImageWidth", "Exif.Image.ImageLength")
    if size is None:
        return None
    try:
        orientation = meta.try_get_tag_long("Exif.Image.Orientation")
    except GLib.Error:
        orientation = 1
    if orientation in (5, 6, 7, 8):
        return size[1], size[0]
    return size


def exif_orientation(path: str) -> int:
    """Read a file's EXIF orientation tag."""
    meta = GExiv2.Metadata()
    try:
        meta.open_path(path)
        value = meta.try_get_tag_long("Exif.Image.Orientation")
    except GLib.Error:
        return 1
    return value if 1 <= value <= 8 else 1  # noqa: PLR2004


def camera_model(path: str) -> str | None:
    """Read the camera model from a file's EXIF, or None."""
    meta = GExiv2.Metadata()
    try:
        meta.open_path(path)
        return meta.try_get_tag_string("Exif.Image.Model")
    except GLib.Error:
        return None


def _stamp(metadata: Any, *, artist: str, rights: str, comment: str) -> None:
    """Write the export credit and provenance tags onto open metadata."""
    if artist:
        metadata.try_set_tag_string("Exif.Image.Artist", artist)
    if rights:
        metadata.try_set_tag_string("Exif.Image.Copyright", rights)
    if comment:
        metadata.try_set_tag_string("Exif.Photo.UserComment", comment)


def copy_exif(
    source_jpeg: bytes,
    dest_path: str,
    *,
    artist: str = "",
    rights: str = "",
    comment: str = "",
) -> None:
    """Transplant the camera JPEG's metadata onto the exported file.

    The orientation tag is reset to normal because the caller bakes the
    orientation into the pixels before writing. A non-empty artist,
    copyright or provenance comment is written on top of the camera
    EXIF.
    """
    try:
        metadata = GExiv2.Metadata()
        metadata.open_buf(source_jpeg)
        metadata.set_orientation(GExiv2.Orientation.NORMAL)
        _stamp(metadata, artist=artist, rights=rights, comment=comment)
        metadata.save_file(dest_path)
    except GLib.Error:
        pass


def with_credits(
    jpeg: bytes, *, artist: str, rights: str, comment: str = ""
) -> bytes:
    """Return jpeg with artist/copyright/comment stamped, pixels as-is."""
    if not artist and not rights and not comment:
        return jpeg
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(jpeg)
        metadata = GExiv2.Metadata()
        metadata.open_path(str(tmp_path))
        _stamp(metadata, artist=artist, rights=rights, comment=comment)
        metadata.save_file(str(tmp_path))
        return tmp_path.read_bytes()
    except GLib.Error:
        return jpeg
    finally:
        tmp_path.unlink(missing_ok=True)
