"""GExiv2-backed metadata helpers for JPEG bytes and RAF files."""

from __future__ import annotations

import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("GExiv2", "0.10")

from gi.repository import GExiv2, GLib

from grawji import exif, raf

# Smallest sensible Exif block: the 4-byte offset plus a TIFF header.
_EXIF_BLOCK_PREFIX = 12
# TIFF IFD layout: 12-byte entries, and the two tags naming the size.
_IFD_ENTRY = 12
_TAG_WIDTH = 0x0100
_TAG_HEIGHT = 0x0101
_TYPE_SHORT = 3
_TYPE_LONG = 4
_SHORT_MAX = 0xFFFF
# The Windows and Adobe percentages that go with one to five stars.
_RATING_PERCENT = {1: 1, 2: 25, 3: 50, 4: 75, 5: 99}


@dataclass(frozen=True)
class Stamp:
    """What an export stamps on top of the camera's metadata."""

    artist: str = ""
    rights: str = ""
    comment: str = ""
    rating: int = 0
    label: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing to stamp."""
        return self == NO_STAMP


NO_STAMP = Stamp()


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


def _stamp(metadata: Any, stamp: Stamp, *, xmp: bool = True) -> None:
    """Write the export credit, provenance and mark tags."""
    if stamp.artist:
        metadata.try_set_tag_string("Exif.Image.Artist", stamp.artist)
    if stamp.rights:
        metadata.try_set_tag_string("Exif.Image.Copyright", stamp.rights)
    if stamp.comment:
        metadata.try_set_tag_string("Exif.Photo.UserComment", stamp.comment)
    percent = _RATING_PERCENT.get(stamp.rating)
    if percent is not None:
        metadata.try_set_tag_string("Exif.Image.Rating", str(stamp.rating))
        metadata.try_set_tag_string("Exif.Image.RatingPercent", str(percent))
    if not xmp:
        return
    if stamp.rating:
        metadata.try_set_tag_string("Xmp.xmp.Rating", str(stamp.rating))
    if stamp.label:
        metadata.try_set_tag_string("Xmp.xmp.Label", stamp.label)


def _resize(metadata: Any, size: tuple[int, int] | None) -> None:
    """Point the size tags at the pixels actually being written."""
    if size is None:
        return
    width, height = size
    metadata.try_set_tag_long("Exif.Photo.PixelXDimension", width)
    metadata.try_set_tag_long("Exif.Photo.PixelYDimension", height)
    metadata.erase_exif_thumbnail()


def copy_exif(
    source_jpeg: bytes,
    dest_path: str,
    *,
    stamp: Stamp = NO_STAMP,
    size: tuple[int, int] | None = None,
    xmp: bool = True,
) -> None:
    """Transplant the camera JPEG's metadata onto the exported file."""
    try:
        metadata = GExiv2.Metadata()
        metadata.open_buf(source_jpeg)
        metadata.try_set_orientation(GExiv2.Orientation.NORMAL)
        _stamp(metadata, stamp, xmp=xmp)
        _resize(metadata, size)
        metadata.save_file(dest_path)
    except GLib.Error:
        pass


def _patch_ifd0_size(tiff: bytes, size: tuple[int, int] | None) -> bytes:
    """Correct IFD0's own width and height in a raw TIFF stream."""
    if size is None or tiff[:4] not in (b"II\x2a\x00", b"MM\x00\x2a"):
        return tiff
    order = "<" if tiff[:2] == b"II" else ">"
    out = bytearray(tiff)
    try:
        offset = struct.unpack_from(f"{order}I", out, 4)[0]
        count = struct.unpack_from(f"{order}H", out, offset)[0]
        for index in range(count):
            entry = offset + 2 + index * _IFD_ENTRY
            tag, kind, values = struct.unpack_from(f"{order}HHI", out, entry)
            if tag not in (_TAG_WIDTH, _TAG_HEIGHT) or values != 1:
                continue
            value = size[0] if tag == _TAG_WIDTH else size[1]
            if kind == _TYPE_LONG:
                struct.pack_into(f"{order}I", out, entry + 8, value)
            elif kind == _TYPE_SHORT and value <= _SHORT_MAX:
                struct.pack_into(f"{order}H", out, entry + 8, value)
    except struct.error:
        return tiff
    return bytes(out)


def stamp_exif_block(
    block: bytes,
    *,
    stamp: Stamp,
    size: tuple[int, int] | None = None,
) -> bytes:
    """Return a HEIF Exif block with the export stamp stamped in."""
    if len(block) < _EXIF_BLOCK_PREFIX:
        return block
    offset = int.from_bytes(block[:4], "big")
    header, tiff = block[: 4 + offset], block[4 + offset :]
    if not tiff.startswith((b"II*\x00", b"MM\x00*")):
        return block
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(tiff)
        metadata = GExiv2.Metadata()
        metadata.open_path(str(tmp_path))
        metadata.try_set_orientation(GExiv2.Orientation.NORMAL)
        _stamp(metadata, stamp, xmp=False)
        _resize(metadata, size)
        metadata.save_file(str(tmp_path))
        return header + _patch_ifd0_size(tmp_path.read_bytes(), size)
    except GLib.Error:
        return block
    finally:
        tmp_path.unlink(missing_ok=True)


def stamp_file(path: str, *, stamp: Stamp) -> None:
    """Stamp the export stamp onto a file already on disk."""
    if stamp.is_empty:
        return
    try:
        metadata = GExiv2.Metadata()
        metadata.open_path(path)
        _stamp(metadata, stamp)
        metadata.save_file(path)
    except GLib.Error:
        pass


def with_credits(jpeg: bytes, *, stamp: Stamp) -> bytes:
    """Return jpeg with the stamp stamped, pixels as-is."""
    if stamp.is_empty:
        return jpeg
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(jpeg)
        metadata = GExiv2.Metadata()
        metadata.open_path(str(tmp_path))
        _stamp(metadata, stamp)
        metadata.save_file(str(tmp_path))
        return tmp_path.read_bytes()
    except GLib.Error:
        return jpeg
    finally:
        tmp_path.unlink(missing_ok=True)
