"""Tests for processor-derived capabilities."""

import struct

from rawji.fuji_profile import create_profile_simple

from grawji.capabilities import (
    capabilities_for,
    is_xprocessor5,
    read_iopcode,
)


def _profile_with_iopcode(iopcode: str) -> bytes:
    """Build a minimal profile header carrying an IOPCode string."""
    return bytes(create_profile_simple(iopcode=iopcode))


def test_read_iopcode_round_trips():
    """The IOPCode string in the header is read back as an int."""
    assert read_iopcode(_profile_with_iopcode("FF159502")) == 0xFF159502


def test_read_iopcode_handles_short_profile():
    """A too-short profile yields None rather than raising."""
    assert read_iopcode(b"\x1d\x00") is None


def test_read_iopcode_handles_non_hex():
    """A non-hex IOPCode string yields None."""
    bad = bytearray(40)
    struct.pack_into("<H", bad, 0, 0x1D)
    bad[2] = 3  # two chars + terminator
    struct.pack_into("<H", bad, 3, ord("Z"))
    struct.pack_into("<H", bad, 5, ord("Z"))
    assert read_iopcode(bytes(bad)) is None


def test_is_xprocessor5():
    """The mask identifies XProcessor5 bodies, not older ones."""
    assert is_xprocessor5(0x00179500) is True
    assert is_xprocessor5(0xAB179599) is True
    assert is_xprocessor5(0xFF159502) is False  # X-T30, X-Trans IV


def test_capabilities_default_vs_xprocessor5():
    """Tone range is -2..+4 on all bodies; XProcessor5 adds 0.5 steps."""
    older = capabilities_for(_profile_with_iopcode("FF159502"))
    assert (older.tone_min, older.tone_max) == (-2, 4)
    assert older.tone_half_step is False

    xproc5 = capabilities_for(_profile_with_iopcode("FF179502"))
    assert (xproc5.tone_min, xproc5.tone_max) == (-2, 4)
    assert xproc5.tone_half_step is True
