"""Read the JPEG preview embedded in a Fujifilm RAF file."""

from __future__ import annotations

import struct
from pathlib import Path

RAF_MAGIC = b"FUJIFILMCCD-RAW "
# The header stores the embedded-JPEG offset and length as big-endian
# uint32 at these byte positions.
_JPEG_OFFSET_POS = 84
_JPEG_LENGTH_POS = 88
_HEADER_MIN = _JPEG_LENGTH_POS + 4
_JPEG_SOI = b"\xff\xd8"


def embedded_jpeg(path: str | Path) -> bytes:
    """Return the JPEG preview embedded in a RAF file.

    Args:
        path: Path to the ``.RAF`` file.

    Returns:
        The embedded JPEG bytes.

    Raises:
        ValueError: If the file is not a RAF or has no embedded JPEG.
    """
    return embedded_jpeg_from_bytes(Path(path).read_bytes())


def embedded_jpeg_from_bytes(data: bytes) -> bytes:
    """Extract the embedded JPEG from raw RAF bytes.

    Args:
        data: The full contents of a RAF file.

    Returns:
        The embedded JPEG bytes.

    Raises:
        ValueError: If the bytes are not a RAF or hold no embedded JPEG.
    """
    if not data.startswith(RAF_MAGIC):
        msg = "not a Fujifilm RAF file (bad magic)"
        raise ValueError(msg)
    if len(data) < _HEADER_MIN:
        msg = "RAF header too short"
        raise ValueError(msg)
    offset = struct.unpack(
        ">I", data[_JPEG_OFFSET_POS : _JPEG_OFFSET_POS + 4]
    )[0]
    length = struct.unpack(
        ">I", data[_JPEG_LENGTH_POS : _JPEG_LENGTH_POS + 4]
    )[0]
    jpeg = data[offset : offset + length]
    if len(jpeg) != length or not jpeg.startswith(_JPEG_SOI):
        msg = "RAF has no valid embedded JPEG"
        raise ValueError(msg)
    return jpeg
