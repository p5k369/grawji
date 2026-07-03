"""GExiv2-backed metadata helpers for JPEG bytes and RAF files."""

from __future__ import annotations

import gi

gi.require_version("GExiv2", "0.10")

from gi.repository import GExiv2, GLib

from grawji import exif


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


def camera_model(path: str) -> str | None:
    """Read the camera model from a file's EXIF, or None."""
    meta = GExiv2.Metadata()
    try:
        meta.open_path(path)
        return meta.try_get_tag_string("Exif.Image.Model")
    except GLib.Error:
        return None


def copy_exif(source_jpeg: bytes, dest_path: str) -> None:
    """Transplant the camera JPEG's metadata onto the exported file.

    The orientation tag is reset to normal because the caller bakes the
    orientation into the pixels before writing.
    """
    try:
        metadata = GExiv2.Metadata()
        metadata.open_buf(source_jpeg)
        metadata.set_orientation(GExiv2.Orientation.NORMAL)
        metadata.save_file(dest_path)
    except GLib.Error:
        pass
