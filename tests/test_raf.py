"""Tests for embedded-JPEG extraction from RAF bytes and files."""

import struct

import pytest

from grawji.raf import (
    RAF_MAGIC,
    embedded_jpeg,
    embedded_jpeg_from_bytes,
    embedded_jpeg_prefix,
)

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


def _write_raf(tmp_path, jpeg: bytes):
    """Write a synthetic RAF wrapping jpeg and return its path."""
    path = tmp_path / "shot.RAF"
    path.write_bytes(_make_raf(jpeg))
    return path


def test_reads_jpeg_from_file(tmp_path):
    """The file path variant extracts the same JPEG as the bytes one."""
    jpeg = b"\xff\xd8" + b"fake-jpeg-payload" + b"\xff\xd9"
    assert embedded_jpeg(_write_raf(tmp_path, jpeg)) == jpeg


def test_reads_jpeg_from_str_path(tmp_path):
    """A plain string path is accepted as well."""
    jpeg = b"\xff\xd8\xff\xd9"
    assert embedded_jpeg(str(_write_raf(tmp_path, jpeg))) == jpeg


def test_file_rejects_non_raf(tmp_path):
    """A file without the RAF magic is rejected."""
    path = tmp_path / "not.RAF"
    path.write_bytes(b"NOT-A-RAF" + bytes(200))
    with pytest.raises(ValueError, match="bad magic"):
        embedded_jpeg(path)


def test_file_rejects_short_header(tmp_path):
    """A truncated file (no room for the offsets) is rejected."""
    path = tmp_path / "short.RAF"
    path.write_bytes(RAF_MAGIC + b"\x00\x00")
    with pytest.raises(ValueError, match="too short"):
        embedded_jpeg(path)


def test_file_rejects_truncated_jpeg(tmp_path):
    """A length field pointing past the end of the file is rejected."""
    data = bytearray(_make_raf(b"\xff\xd8\xff\xd9"))
    struct.pack_into(">I", data, 88, 4 + 10)  # claim more than is there
    path = tmp_path / "trunc.RAF"
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="no valid embedded"):
        embedded_jpeg(path)


def test_prefix_caps_the_read(tmp_path):
    """The prefix variant returns at most max_bytes of the JPEG."""
    jpeg = b"\xff\xd8" + b"x" * 100
    assert embedded_jpeg_prefix(_write_raf(tmp_path, jpeg), 10) == jpeg[:10]


def test_prefix_returns_whole_short_jpeg(tmp_path):
    """A JPEG shorter than max_bytes comes back complete."""
    jpeg = b"\xff\xd8\xff\xd9"
    assert embedded_jpeg_prefix(_write_raf(tmp_path, jpeg), 1024) == jpeg


def test_prefix_rejects_missing_jpeg_marker(tmp_path):
    """The prefix variant still validates the SOI marker."""
    data = bytearray(_make_raf(b"\xff\xd8\xff\xd9"))
    data[_OFFSET : _OFFSET + 2] = b"\x00\x00"  # corrupt the SOI marker
    path = tmp_path / "bad.RAF"
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="no valid embedded"):
        embedded_jpeg_prefix(path, 10)
