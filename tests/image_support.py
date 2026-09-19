"""Camera-like test images, stored the way each format stores rotation."""

from __future__ import annotations

import struct
from pathlib import Path

import gi
import numpy as np

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GExiv2", "0.10")

from gi.repository import GdkPixbuf, GExiv2, GLib

# What a viewer has to do to a stored frame to make it upright.
STORED = {
    1: lambda a: a,
    2: lambda a: a[:, ::-1],
    3: lambda a: a[::-1, ::-1],
    4: lambda a: a[::-1],
    5: lambda a: a.transpose(1, 0, 2),
    6: lambda a: np.rot90(a, 1),
    7: lambda a: a.transpose(1, 0, 2)[::-1, ::-1],
    8: lambda a: np.rot90(a, -1),
}


def quadrants(width: int = 64, height: int = 96) -> np.ndarray:
    """An upright frame whose four corners are told apart at a glance."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    half_h, half_w = height // 2, width // 2
    frame[:half_h, :half_w] = (220, 40, 40)
    frame[:half_h, half_w:] = (40, 200, 60)
    frame[half_h:, :half_w] = (50, 60, 210)
    frame[half_h:, half_w:] = (230, 220, 40)
    return frame


def stored_as(upright: np.ndarray, tag: int) -> np.ndarray:
    """The frame as a file with that orientation tag would hold it."""
    return np.ascontiguousarray(STORED[tag](upright))


def tiff_bytes(stored: np.ndarray, *, tag: int, bits: int = 8) -> bytes:
    """An uncompressed RGB TIFF carrying an orientation tag."""
    height, width = stored.shape[:2]
    payload = stored.astype(np.uint8 if bits == 8 else "<u2")
    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 3, 0),
        (259, 3, 1, 1),
        (262, 3, 1, 2),
        (273, 4, 1, 0),
        (274, 3, 1, tag),
        (277, 3, 1, 3),
        (278, 4, 1, height),
        (279, 4, 1, width * height * 3 * (bits // 8)),
    ]
    bits_at = 8 + 2 + len(entries) * 12 + 4
    out = bytearray(b"II" + struct.pack("<HI", 42, 8))
    out += struct.pack("<H", len(entries))
    for tid, kind, count, value in entries:
        stored_value = {258: bits_at, 273: bits_at + 6}.get(tid, value)
        out += struct.pack("<HHI", tid, kind, count)
        out += (
            struct.pack("<HH", stored_value, 0)
            if kind == 3 and count == 1
            else struct.pack("<I", stored_value)
        )
    out += struct.pack("<I", 0) + struct.pack("<3H", bits, bits, bits)
    return bytes(out) + np.ascontiguousarray(payload).tobytes()


def widen(frame: np.ndarray, bits: int = 16) -> np.ndarray:
    """Lift an 8-bit frame to a deeper sample range."""
    top = (1 << bits) - 1
    return (frame.astype(np.uint32) * top // 255).astype(np.uint16)


def pixbuf_of(frame: np.ndarray) -> GdkPixbuf.Pixbuf:
    """Wrap an array of 8-bit RGB as a pixbuf."""
    height, width = frame.shape[:2]
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(np.ascontiguousarray(frame).tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        width,
        height,
        width * 3,
    )


def jpeg_bytes(stored: np.ndarray, path: str, *, tag: int) -> bytes:
    """A JPEG with the orientation in its Exif, like the camera writes."""
    pixbuf_of(stored).savev(path, "jpeg", ["quality"], ["95"])
    metadata = GExiv2.Metadata()
    metadata.open_path(path)
    metadata.try_set_orientation(GExiv2.Orientation(tag))
    metadata.save_file(path)
    return Path(path).read_bytes()


def shown(path: str) -> np.ndarray:
    """A written JPEG as a viewer shows it, orientation applied."""
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path).apply_embedded_orientation()
    channels = pixbuf.get_n_channels()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    raw = np.frombuffer(pixbuf.get_pixels(), np.uint8)
    missing = height * stride - raw.size
    if missing > 0:
        raw = np.concatenate((raw, np.zeros(missing, np.uint8)))
    rows = raw[: height * stride].reshape(height, stride)
    frame = rows[:, : width * channels].reshape(height, width, channels)
    return frame[:, :, :3]
