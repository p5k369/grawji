#!/usr/bin/env python3
"""Smoke-test grawji inside the flatpak sandbox."""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import cairo  # noqa: F401
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GExiv2", "0.10")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import rawji  # noqa: F401
import usb.core
from gi.repository import GdkPixbuf, GExiv2

import grawji.views
from grawji.crop import CropRotate
from grawji.imaging.render import bake_pixbuf

# Files the manifest installs into /app, a manifest regression that
# drops one breaks desktop integration (the issue #85 class of bug).
_BUNDLE_FILES = (
    "share/applications/io.github.p5k369.grawji.desktop",
    "share/metainfo/io.github.p5k369.grawji.metainfo.xml",
    "share/icons/hicolor/scalable/apps/io.github.p5k369.grawji.svg",
)


def check_entry_point() -> str:
    """The installed launcher starts and reports its version."""
    result = subprocess.run(
        ["/app/bin/grawji", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    reported = result.stdout.strip()
    if not reported.startswith("grawji "):
        raise RuntimeError(f"unexpected --version output: {reported!r}")
    return reported


def check_usb_backend() -> str:
    """The bundled libusb loads as the pyusb backend.

    No camera is needed: find() returning nothing is fine, only a
    missing backend (broken libusb module) fails.
    """
    try:
        usb.core.find()
    except usb.core.NoBackendError as error:
        raise RuntimeError(f"pyusb has no backend: {error}") from error
    return "libusb backend loads"


def check_bundle_files() -> str:
    """The desktop file, metainfo and icon are installed in /app."""
    missing = [
        name for name in _BUNDLE_FILES if not (Path("/app") / name).exists()
    ]
    if missing:
        raise RuntimeError(f"missing from the bundle: {', '.join(missing)}")
    return f"{len(_BUNDLE_FILES)} files present"


def check_ui_imports() -> str:
    """Every view module imports, resolving its .ui template XML."""
    failed = []
    for module in pkgutil.iter_modules(grawji.views.__path__):
        try:
            importlib.import_module(f"grawji.views.{module.name}")
        except Exception as error:
            failed.append(f"{module.name}: {error}")
    if failed:
        raise RuntimeError("; ".join(failed))
    return "all view modules and their templates load"


def check_jpeg_roundtrip() -> str:
    """The runtime's JPEG encoder and loader both work on RGB."""
    return _pixbuf_roundtrip("jpeg", ["quality"], ["90"])


def check_png_roundtrip() -> str:
    """PNG works too (the thumbnail cache stores PNG)."""
    return _pixbuf_roundtrip("png", [], [])


def _pixbuf_roundtrip(fmt: str, keys: list[str], values: list[str]) -> str:
    """Encode a small pixbuf as fmt and decode it back."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
    pixbuf.fill(0x336699FF)
    ok, data = pixbuf.save_to_bufferv(fmt, keys, values)
    if not ok:
        raise RuntimeError(f"{fmt} encode failed")
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    decoded = loader.get_pixbuf()
    if (decoded.get_width(), decoded.get_height()) != (64, 48):
        raise RuntimeError(f"{fmt} decode came back with the wrong size")
    return f"{len(data)} bytes encoded and decoded"


def check_rotation_export() -> str:
    """A rotation bake still encodes as JPEG (issue #100 regression)."""
    source = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 300)
    source.fill(0x808080FF)
    baked = bake_pixbuf(
        source, CropRotate(angle=7.5, rect=(0.2, 0.2, 0.5, 0.5))
    )
    if baked.get_has_alpha():
        raise RuntimeError("rotation bake returned an RGBA pixbuf")
    ok, data = baked.save_to_bufferv("jpeg", ["quality"], ["95"])
    if not ok:
        raise RuntimeError("rotated bake failed to encode as JPEG")
    return f"3-channel bake, {len(data)} bytes"


def check_exif_roundtrip() -> str:
    """The bundled GExiv2 writes and reads tags on a JPEG file."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 16, 16)
    pixbuf.fill(0x808080FF)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        pixbuf.savev(str(path), "jpeg", [], [])
        meta = GExiv2.Metadata()
        meta.open_path(str(path))
        meta.try_set_tag_string("Exif.Image.Model", "X-E5")
        meta.save_file(str(path))
        again = GExiv2.Metadata()
        again.open_path(str(path))
        if again.try_get_tag_string("Exif.Image.Model") != "X-E5":
            raise RuntimeError("EXIF tag did not survive the roundtrip")
    finally:
        path.unlink(missing_ok=True)
    return "Exif.Image.Model written and read back"


def main() -> int:
    """Run every check, report, and fail on the first broken one."""
    checks: list[tuple[str, Callable[[], str]]] = [
        ("entry point", check_entry_point),
        ("bundle files", check_bundle_files),
        ("usb backend", check_usb_backend),
        ("ui imports", check_ui_imports),
        ("jpeg roundtrip", check_jpeg_roundtrip),
        ("png roundtrip", check_png_roundtrip),
        ("rotation export", check_rotation_export),
        ("exif roundtrip", check_exif_roundtrip),
    ]
    for name, check in checks:
        try:
            detail = check()
        except Exception as error:
            print(f"[FAIL] {name}: {error}")
            return 1
        print(f"[ ok ] {name}: {detail}")
    print("flatpak smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
