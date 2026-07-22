"""Read the JPEG preview embedded in a Fujifilm RAF file."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

RAF_MAGIC = b"FUJIFILMCCD-RAW "
# The header stores the embedded-JPEG offset and length as big-endian
# uint32 at these byte positions.
_JPEG_OFFSET_POS = 84
_JPEG_LENGTH_POS = 88
_HEADER_MIN = _JPEG_LENGTH_POS + 4
_JPEG_SOI = b"\xff\xd8"


def _parse_extent(header: bytes) -> tuple[int, int]:
    """Validate a RAF header and return its embedded JPEG (offset, length)."""
    if not header.startswith(RAF_MAGIC):
        msg = "not a Fujifilm RAF file (bad magic)"
        raise ValueError(msg)
    if len(header) < _HEADER_MIN:
        msg = "RAF header too short"
        raise ValueError(msg)
    offset = struct.unpack(
        ">I", header[_JPEG_OFFSET_POS : _JPEG_OFFSET_POS + 4]
    )[0]
    length = struct.unpack(
        ">I", header[_JPEG_LENGTH_POS : _JPEG_LENGTH_POS + 4]
    )[0]
    return offset, length


def _jpeg_extent(handle: BinaryIO) -> tuple[int, int]:
    """Read a RAF header and return its embedded JPEG (offset, length)."""
    return _parse_extent(handle.read(_HEADER_MIN))


def embedded_jpeg(path: str | Path) -> bytes:
    """Return the JPEG preview embedded in a RAF file."""
    with Path(path).open("rb") as handle:
        offset, length = _jpeg_extent(handle)
        handle.seek(offset)
        jpeg = handle.read(length)
    if len(jpeg) != length or not jpeg.startswith(_JPEG_SOI):
        msg = "RAF has no valid embedded JPEG"
        raise ValueError(msg)
    return jpeg


def embedded_jpeg_prefix(path: str | Path, max_bytes: int) -> bytes:
    """Return the leading bytes of the embedded JPEG, up to max_bytes."""
    with Path(path).open("rb") as handle:
        offset, length = _jpeg_extent(handle)
        handle.seek(offset)
        jpeg = handle.read(min(length, max_bytes))
    if not jpeg.startswith(_JPEG_SOI):
        msg = "RAF has no valid embedded JPEG"
        raise ValueError(msg)
    return jpeg


def embedded_jpeg_from_bytes(data: bytes) -> bytes:
    """Extract the embedded JPEG from raw RAF bytes."""
    offset, length = _parse_extent(data)
    jpeg = data[offset : offset + length]
    if len(jpeg) != length or not jpeg.startswith(_JPEG_SOI):
        msg = "RAF has no valid embedded JPEG"
        raise ValueError(msg)
    return jpeg
