"""Export building blocks: decode chains, JPEG writing, filenames."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio

from grawji import sidecar
from grawji.imaging import imagemeta
from grawji.imaging.render import (
    add_border,
    bake_pixbuf,
    parse_aspect,
    scale_to_edge,
)
from grawji.settings import Settings
from grawji.views.preview_view import oriented_pixbuf

SetBusy = Callable[..., None]


def sidecar_decode(raf_path: str) -> Callable[[bytes], Any] | None:
    """A decode callback applying the RAF's sidecar geometry, or None."""
    geometry = sidecar.load_crop(raf_path)
    if geometry.is_identity:
        return None
    return lambda jpeg: bake_pixbuf(oriented_pixbuf(jpeg), geometry)


def framing_active(settings: Settings) -> bool:
    """Whether the export border/padding would change any pixels."""
    return settings.export_border_enabled and (
        settings.export_border_percent > 0
        or parse_aspect(settings.export_border_aspect) is not None
    )


def resize_active(settings: Settings) -> bool:
    """Whether the export size limit would change any pixels."""
    return settings.export_max_edge > 0


def with_max_edge(
    decode: Callable[[bytes], Any], settings: Settings
) -> Callable[[bytes], Any]:
    """Wrap decode with the configured long-edge limit, if any."""
    if not resize_active(settings):
        return decode
    max_edge = settings.export_max_edge
    return lambda jpeg: scale_to_edge(decode(jpeg), max_edge)


def with_border(
    decode: Callable[[bytes], Any], settings: Settings
) -> Callable[[bytes], Any]:
    """Wrap decode with the configured export border, if any."""
    if not framing_active(settings):
        return decode
    percent = settings.export_border_percent
    color = settings.export_border_color
    aspect = parse_aspect(settings.export_border_aspect)
    return lambda jpeg: add_border(decode(jpeg), percent, color, aspect)


def export_basename(raf_path: Path | str) -> str:
    """Build an export filename from the RAF stem."""
    return f"{Path(raf_path).stem}.jpg"


def initial_folder(path: str) -> Gio.File | None:
    """A Gio.File for path if it is an existing directory, else None.

    Used to open an export dialog at the last-used export folder.
    """
    if path and Path(path).is_dir():
        return Gio.File.new_for_path(path)
    return None


def write_jpeg(
    jpeg: bytes,
    path: str,
    *,
    quality: int,
    decode: Callable[[bytes], Any],
    artist: str = "",
    rights: str = "",
) -> None:
    """Write jpeg to path with orientation and rotation baked in.

    decode turns the camera JPEG into the pixbuf to encode (the caller
    supplies it so the preview's manual rotation is applied). Encoding
    and the EXIF transplant happen on a temp file first, then the
    finished bytes are written to the chosen path in one go: that path
    may be an XDG document-portal proxy (Flatpak), which exiv2 cannot
    rewrite in place - doing so leaves a 0-byte file.

    Raises GLib.Error or OSError on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pixbuf = decode(jpeg)
        pixbuf.savev(tmp_path, "jpeg", ["quality"], [str(quality)])
        # GdkPixbuf re-encoding drops all metadata, so copy the camera's
        # EXIF back on (orientation is now baked into the pixels).
        imagemeta.copy_exif(jpeg, tmp_path, artist=artist, rights=rights)
        Path(path).write_bytes(Path(tmp_path).read_bytes())
    finally:
        Path(tmp_path).unlink(missing_ok=True)
