"""Reading and writing the camera's uncompressed TIFF."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple

import numpy as np

from grawji.imaging.render16 import Samples

# TIFF byte-order marks, and the magic that follows them.
_LITTLE = b"II"
_BIG = b"MM"
_MAGIC = 42
# A TIFF header is the byte order, the magic and the first IFD offset.
_HEADER = 8
# Every IFD entry is tag, type, count and a four-byte value or offset.
_ENTRY = 12
_VALUE_INLINE = 4
# The field types this module reads.
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_UNDEFINED = 7
_TYPE_WIDTH = {_TYPE_SHORT: 2, _TYPE_LONG: 4}
# The tags that describe the pixels.
_WIDTH = 256
_HEIGHT = 257
_BITS = 258
_COMPRESSION = 259
_PHOTOMETRIC = 262
_STRIP_OFFSETS = 273
_SAMPLES = 277
_ROWS_PER_STRIP = 278
_STRIP_COUNTS = 279
_PLANAR = 284
_ICC = 34675
# The only values that describe an engine TIFF.
_UNCOMPRESSED = 1
_RGB = 2
_CHUNKY = 1
_CHANNELS = 3
_BITS_8 = 8
SUPPORTED_BITS = (_BITS_8, 16)


class DecodedTiff(NamedTuple):
    """Decoded TIFF pixels, widened to 16 bits for the shared pipeline."""

    samples: Samples
    bits: int


class TiffError(RuntimeError):
    """The bytes are not a TIFF this module can read or write."""


def is_tiff(data: bytes) -> bool:
    """Whether the bytes open like a TIFF."""
    if len(data) < _HEADER:
        return False
    order = data[:2]
    if order not in (_LITTLE, _BIG):
        return False
    mark = "<H" if order == _LITTLE else ">H"
    return struct.unpack_from(mark, data, 2)[0] == _MAGIC


def _read_ifd(data: bytes) -> tuple[str, dict[int, list[int]]]:
    """Read IFD0 into a tag to values mapping, with the byte order."""
    if not is_tiff(data):
        raise TiffError("not a TIFF")
    order = "<" if data[:2] == _LITTLE else ">"
    try:
        start = struct.unpack_from(f"{order}I", data, 4)[0]
        count = struct.unpack_from(f"{order}H", data, start)[0]
    except struct.error as exc:
        raise TiffError("truncated TIFF header") from exc
    tags: dict[int, list[int]] = {}
    for index in range(count):
        entry = start + 2 + index * _ENTRY
        try:
            tag, kind, items = struct.unpack_from(f"{order}HHI", data, entry)
        except struct.error as exc:
            raise TiffError("truncated IFD") from exc
        width = _TYPE_WIDTH.get(kind)
        if width is None:
            continue
        span = width * items
        at = entry + 8
        if span > _VALUE_INLINE:
            at = struct.unpack_from(f"{order}I", data, at)[0]
        code = "H" if kind == _TYPE_SHORT else "I"
        try:
            tags[tag] = list(
                struct.unpack_from(f"{order}{items}{code}", data, at)
            )
        except struct.error as exc:
            raise TiffError(f"truncated value for tag {tag}") from exc
    return order, tags


def _one(tags: dict[int, list[int]], tag: int, default: int | None) -> int:
    """The single value of a tag, or a default when it is absent."""
    values = tags.get(tag)
    if not values:
        if default is None:
            raise TiffError(f"TIFF is missing tag {tag}")
        return default
    return values[0]


def _depth(tags: dict[int, list[int]]) -> int:
    """The bit depth, refusing anything but a uniform 8 or 16."""
    bits = tags.get(_BITS) or [8]
    if len(set(bits)) != 1 or bits[0] not in SUPPORTED_BITS:
        raise TiffError(f"unsupported bits per sample: {bits}")
    return bits[0]


def is_complete(data: bytes) -> bool:
    """Whether every strip the header promises is really present."""
    try:
        _order, tags = _read_ifd(data)
        offsets = tags.get(_STRIP_OFFSETS) or []
        counts = tags.get(_STRIP_COUNTS) or []
    except TiffError:
        return False
    if not offsets or len(offsets) != len(counts):
        return False
    return all(
        offset + count <= len(data)
        for offset, count in zip(offsets, counts, strict=True)
    )


def icc_profile(data: bytes) -> bytes | None:
    """The embedded ICC profile."""
    try:
        order, _tags = _read_ifd(data)
        start = struct.unpack_from(f"{order}I", data, 4)[0]
        count = struct.unpack_from(f"{order}H", data, start)[0]
    except (TiffError, struct.error):
        return None
    for index in range(count):
        entry = start + 2 + index * _ENTRY
        try:
            tag, kind, items = struct.unpack_from(f"{order}HHI", data, entry)
        except struct.error:
            return None
        if tag != _ICC or kind != _TYPE_UNDEFINED or not items:
            continue
        at = entry + 8
        if items > _VALUE_INLINE:
            at = struct.unpack_from(f"{order}I", data, at)[0]
        profile = data[at : at + items]
        return profile if len(profile) == items else None
    return None


def decode(data: bytes) -> DecodedTiff:
    """Decode an uncompressed RGB TIFF to interleaved 16-bit samples."""
    order, tags = _read_ifd(data)
    if _one(tags, _COMPRESSION, _UNCOMPRESSED) != _UNCOMPRESSED:
        raise TiffError("compressed TIFF is not supported")
    if _one(tags, _PHOTOMETRIC, _RGB) != _RGB:
        raise TiffError("only RGB TIFF is supported")
    if _one(tags, _PLANAR, _CHUNKY) != _CHUNKY:
        raise TiffError("planar TIFF is not supported")
    if _one(tags, _SAMPLES, _CHANNELS) != _CHANNELS:
        raise TiffError("only three-channel TIFF is supported")
    width = _one(tags, _WIDTH, None)
    height = _one(tags, _HEIGHT, None)
    bits = _depth(tags)
    offsets = tags.get(_STRIP_OFFSETS) or []
    counts = tags.get(_STRIP_COUNTS) or []
    if not offsets or len(offsets) != len(counts):
        raise TiffError("TIFF strip table is unusable")
    wanted = width * height * _CHANNELS * (bits // 8)
    if sum(counts) < wanted:
        raise TiffError("TIFF strips are shorter than the frame")
    if len(offsets) == 1:
        raw = data[offsets[0] : offsets[0] + wanted]
    else:
        raw = b"".join(
            data[offset : offset + count]
            for offset, count in zip(offsets, counts, strict=True)
        )[:wanted]
    dtype = np.dtype(np.uint8 if bits == _BITS_8 else f"{order}u2")
    flat = np.frombuffer(raw, dtype=dtype)
    samples = flat.reshape(height, width, _CHANNELS)
    return DecodedTiff(samples.astype(np.uint16), bits)


def _entries(
    width: int, height: int, bits: int
) -> list[tuple[int, int, int, int]]:
    """The IFD entries as tag, type, count and value, in tag order."""
    count = width * height * _CHANNELS * (bits // 8)
    # Header, the entry block, the next-IFD pointer and the bit triple.
    bits_at = _HEADER + 2 + 10 * _ENTRY + 4
    strip_at = bits_at + _CHANNELS * 2
    return [
        (_WIDTH, _TYPE_LONG, 1, width),
        (_HEIGHT, _TYPE_LONG, 1, height),
        # Three shorts do not fit the value field, so this is an offset.
        (_BITS, _TYPE_SHORT, _CHANNELS, bits_at),
        (_COMPRESSION, _TYPE_SHORT, 1, _UNCOMPRESSED),
        (_PHOTOMETRIC, _TYPE_SHORT, 1, _RGB),
        (_STRIP_OFFSETS, _TYPE_LONG, 1, strip_at),
        (_SAMPLES, _TYPE_SHORT, 1, _CHANNELS),
        (_ROWS_PER_STRIP, _TYPE_LONG, 1, height),
        (_STRIP_COUNTS, _TYPE_LONG, 1, count),
        (_PLANAR, _TYPE_SHORT, 1, _CHUNKY),
    ]


def header(width: int, height: int, bits: int) -> bytes:
    """Build the whole TIFF header that precedes the pixel strip."""
    if bits not in SUPPORTED_BITS:
        raise TiffError(f"cannot write {bits}-bit TIFF")
    out = bytearray(_LITTLE + struct.pack("<HI", _MAGIC, _HEADER))
    entries = _entries(width, height, bits)
    out += struct.pack("<H", len(entries))
    for tag, kind, count, value in entries:
        out += struct.pack("<HHI", tag, kind, count)
        inline = kind == _TYPE_SHORT and count * 2 <= _VALUE_INLINE
        if inline:
            out += struct.pack("<HH", value, 0)
        else:
            out += struct.pack("<I", value)
    out += struct.pack("<I", 0)
    out += struct.pack("<3H", bits, bits, bits)
    return bytes(out)


def encode(samples: Samples, path: str, *, bits: int) -> None:
    """Write samples to path as an uncompressed little-endian TIFF."""
    height, width = samples.shape[:2]
    payload = (
        samples.astype(np.uint8) if bits == _BITS_8 else samples.astype("<u2")
    )
    with Path(path).open("wb") as handle:
        handle.write(header(width, height, bits))
        handle.write(np.ascontiguousarray(payload).tobytes())
