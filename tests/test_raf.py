"""Tests for embedded-JPEG extraction from RAF bytes."""

import struct

import pytest

from grawji.raf import RAF_MAGIC, embedded_jpeg_from_bytes

_OFFSET = 96  # where we place the fake JPEG in the synthetic RAF


def _make_raf(jpeg: bytes) -> bytes:
    """Build a minimal synthetic RAF wrapping the given JPEG bytes."""
    header = bytearray(_OFFSET)
    header[: len(RAF_MAGIC)] = RAF_MAGIC
    struct.pack_into(">I", header, 84, _OFFSET)
    struct.pack_into(">I", header, 88, len(jpeg))
    return bytes(header) + jpeg


def test_extracts_embedded_jpeg():
    """The embedded JPEG is sliced out using the header offset/length."""
    jpeg = b"\xff\xd8" + b"fake-jpeg-payload" + b"\xff\xd9"
    assert embedded_jpeg_from_bytes(_make_raf(jpeg)) == jpeg


def test_rejects_non_raf():
    """Bytes without the RAF magic are rejected."""
    with pytest.raises(ValueError, match="bad magic"):
        embedded_jpeg_from_bytes(b"NOT-A-RAF" + bytes(200))


def test_rejects_short_header():
    """A truncated header (no room for the offsets) is rejected."""
    with pytest.raises(ValueError, match="too short"):
        embedded_jpeg_from_bytes(RAF_MAGIC + b"\x00\x00")


def test_rejects_missing_jpeg_marker():
    """A region that does not start with the JPEG SOI is rejected."""
    raf = bytearray(_make_raf(b"\xff\xd8\xff\xd9"))
    raf[_OFFSET : _OFFSET + 2] = b"\x00\x00"  # corrupt the SOI marker
    with pytest.raises(ValueError, match="no valid embedded"):
        embedded_jpeg_from_bytes(bytes(raf))
