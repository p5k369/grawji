"""Tests for the HEIF export plumbing."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from grawji.camera import core
from grawji.imaging import export, heif, heif_codec, imagemeta


def test_file_type_patches_the_profile_slot():
    """A HEIF request lands in profile slot 1 at offset 517."""
    base = bytes(632)
    patched = core.apply_file_type(base, "heif")
    assert struct.unpack_from("<I", patched, 517)[0] == 18
    assert len(patched) == len(base)


def test_jpeg_and_unknown_file_types_leave_the_profile_alone():
    """Anything grawji is unsure about keeps the RAF's own value."""
    base = bytes(range(256)) * 3
    assert core.apply_file_type(base, "jpeg") == base
    assert core.apply_file_type(base, "tiff") == base
    assert core.apply_file_type(b"short", "heif") == b"short"


def test_is_heif_matches_the_container_magic():
    """The ftyp box at offset 4 is what marks a HEIF file."""
    assert heif.is_heif(b"\x00\x00\x00\x18ftypheic" + bytes(16))
    assert not heif.is_heif(b"\xff\xd8\xff\xe1" + bytes(16))
    assert not heif.is_heif(b"")


def test_format_suffix_and_basename():
    """Each format names its own file."""
    assert export.format_suffix("heif") == ".heif"
    assert export.format_suffix("nonsense") == ".jpg"
    assert export.export_basename("/x/y.RAF", fmt="heif") == "y.heif"


def test_stamp_exif_block_keeps_the_header_and_adds_credits():
    """Credits go into the TIFF stream inside a HEIF Exif block."""
    tiff = _tiff_with_model()
    block = (6).to_bytes(4, "big") + b"Exif\x00\x00" + tiff
    stamped = imagemeta.stamp_exif_block(
        block, artist="A", rights="R", comment="C"
    )
    assert stamped[:10] == block[:10]
    assert stamped != block


def test_stamp_exif_block_passes_junk_through():
    """A block that is not a TIFF stream is returned untouched."""
    junk = b"\x00\x00\x00\x06Exif\x00\x00not a tiff"
    assert imagemeta.stamp_exif_block(junk, artist="A", rights="") == junk
    assert (
        imagemeta.stamp_exif_block(b"\x00", artist="A", rights="") == b"\x00"
    )


def _tiff_with_model() -> bytes:
    """A minimal little-endian TIFF carrying one string tag."""
    model = b"X-E5\x00\x00\x00\x00"
    header = b"II\x2a\x00" + struct.pack("<I", 8)
    entries = struct.pack("<H", 1)
    entries += struct.pack("<HHII", 0x0110, 2, len(model), 8 + 2 + 12 + 4)
    entries += struct.pack("<I", 0)
    return header + entries + model


@pytest.mark.skipif(not heif_codec.available(), reason="libheif not installed")
def test_encode_round_trips_10_bit_samples(tmp_path):
    """The encoder writes a HEIF that decodes back at full depth."""
    samples = np.full((48, 64, 3), 700, dtype=np.uint16)
    out = tmp_path / "out.heif"
    heif_codec.encode(samples, str(out), quality=100)
    assert heif.is_heif(out.read_bytes())
    decoded = heif_codec.decode(out.read_bytes())
    assert decoded is not None
    assert (decoded.width, decoded.height, decoded.bits) == (64, 48, 10)


def test_exif_block_of_junk_is_none():
    """Bytes that are not HEIF yield no Exif block instead of raising."""
    assert heif.exif_block(b"") is None
    assert heif.exif_block(b"not a heif file at all") is None


def test_is_complete_spots_a_cut_transfer():
    """A box claiming more bytes than arrived means a short download."""
    box = b"\x00\x00\x00\x18ftypheix" + bytes(12)
    mdat = (1024).to_bytes(4, "big") + b"mdat" + bytes(1016)
    assert heif.is_complete(box + mdat)
    assert not heif.is_complete(box + mdat[:512])
    assert not heif.is_complete(box[:12])


def test_is_complete_allows_a_trailing_pad():
    """The camera writes a short tail the box chain does not cover."""
    box = b"\x00\x00\x00\x18ftypheix" + bytes(12)
    assert heif.is_complete(box + bytes(117))


def test_stamp_exif_block_rewrites_the_size():
    """After a crop the size tags must describe the exported frame."""
    tiff = _tiff_with_size(7728, 5152)
    block = (6).to_bytes(4, "big") + b"Exif\x00\x00" + tiff
    stamped = imagemeta.stamp_exif_block(
        block, artist="", rights="", size=(4066, 6100)
    )
    assert _sizes_in(stamped[10:]) == (4066, 6100)


def test_patch_ifd0_size_leaves_other_streams_alone():
    """Anything that is not a TIFF stream comes back untouched."""
    assert imagemeta._patch_ifd0_size(b"not a tiff", (10, 20)) == b"not a tiff"
    tiff = _tiff_with_size(4, 3)
    assert imagemeta._patch_ifd0_size(tiff, None) == tiff


def _sizes_in(tiff: bytes) -> tuple[int, int]:
    """Read IFD0's width and height back out of a TIFF stream."""
    offset = struct.unpack_from("<I", tiff, 4)[0]
    count = struct.unpack_from("<H", tiff, offset)[0]
    found = {}
    for index in range(count):
        entry = offset + 2 + index * 12
        tag, _kind, _n = struct.unpack_from("<HHI", tiff, entry)
        if tag in (0x0100, 0x0101):
            found[tag] = struct.unpack_from("<I", tiff, entry + 8)[0]
    return found.get(0x0100, 0), found.get(0x0101, 0)


def _tiff_with_size(width: int, height: int) -> bytes:
    """A minimal little-endian TIFF carrying the two size tags."""
    entries = struct.pack("<H", 2)
    entries += struct.pack("<HHII", 0x0100, 4, 1, width)
    entries += struct.pack("<HHII", 0x0101, 4, 1, height)
    entries += struct.pack("<I", 0)
    return b"II\x2a\x00" + struct.pack("<I", 8) + entries


def _tiny_heif(payload: bytes) -> bytes:
    """A minimal HEIF carrying one Exif item in its mdat."""
    ftyp = struct.pack(">I", 16) + b"ftypheic" + bytes(4)
    infe = (
        struct.pack(">I", 20)
        + b"infe"
        + bytes([2, 0, 0, 0])
        + struct.pack(">HH", 1, 0)
        + b"Exif"
    )
    iinf = (
        struct.pack(">I", 8 + 4 + 2 + len(infe))
        + b"iinf"
        + bytes(4)
        + struct.pack(">H", 1)
        + infe
    )
    iloc_size = 8 + 4 + 2 + 2 + 16
    meta_size = 8 + 4 + len(iinf) + iloc_size
    payload_at = len(ftyp) + meta_size + 8
    iloc = (
        struct.pack(">I", iloc_size)
        + b"iloc"
        + bytes([1, 0, 0, 0])
        + bytes([0x44, 0x00])
        + struct.pack(">H", 1)
        + struct.pack(">HHH", 1, 0, 0)
        + struct.pack(">H", 1)
        + struct.pack(">II", payload_at, len(payload))
    )
    meta = struct.pack(">I", meta_size) + b"meta" + bytes(4) + iinf + iloc
    mdat = struct.pack(">I", len(payload) + 8) + b"mdat" + payload
    return ftyp + meta + mdat


def test_replace_exif_writes_a_shorter_block_in_place():
    """A block that fits is written over the old one, file size kept."""
    original = _tiny_heif(b"EXIF-ORIGINAL-BLOCK")
    out = heif.replace_exif(original, b"SHORTER")
    assert out is not None
    assert len(out) == len(original)
    assert b"SHORTER" in out


def test_replace_exif_appends_a_longer_block():
    """A block that does not fit goes into a new mdat at the end."""
    original = _tiny_heif(b"SHORT")
    longer = b"A-MUCH-LONGER-EXIF-BLOCK" * 4
    out = heif.replace_exif(original, longer)
    assert out is not None
    assert len(out) == len(original) + len(longer) + 8
    assert out.endswith(longer)
    assert heif.is_complete(out)


def test_replace_exif_gives_up_on_an_unknown_layout():
    """Without an Exif item there is nothing to replace."""
    assert heif.replace_exif(b"not a heif", b"block") is None
    assert heif.replace_exif(_tiny_heif(b"x")[:20], b"block") is None
