"""Per-camera capabilities derived from the profile's processor code."""

from __future__ import annotations

from dataclasses import dataclass

# Offsets in the profile header (matches rawji's layout): a uint16 prop count
# at 0, a length byte at 2, then the IOPCode as a null-terminated wide-char
# (UTF-16-LE) string from offset 3.
_IOPCODE_LEN_OFFSET = 2
_IOPCODE_STR_OFFSET = 3


def read_iopcode(profile: bytes) -> int | None:
    """Return the profile's IOPCode as an int, or None if unreadable."""
    if len(profile) <= _IOPCODE_STR_OFFSET:
        return None
    char_count = profile[_IOPCODE_LEN_OFFSET]
    chars = []
    offset = _IOPCODE_STR_OFFSET
    for _ in range(max(0, char_count)):
        if offset + 2 > len(profile):
            return None
        code = int.from_bytes(profile[offset : offset + 2], "little")
        offset += 2
        if code == 0:
            break
        chars.append(chr(code))
    try:
        return int("".join(chars), 16)
    except ValueError:
        return None


def is_xprocessor5(iopcode: int) -> bool:
    """Whether an IOPCode identifies an XProcessor5 body."""
    return (iopcode & 0x00FFFF00) == 0x00179500


@dataclass(frozen=True)
class Capabilities:
    """Recipe limits that vary by camera processor."""

    tone_min: int
    tone_max: int


# X-Trans III/IV: highlight/shadow tone is -2 to +4.
_DEFAULT = Capabilities(tone_min=-2, tone_max=4)
# XProcessor5 bodies extend the low end to -4.
_XPROCESSOR5 = Capabilities(tone_min=-4, tone_max=4)


def capabilities_for(profile: bytes) -> Capabilities:
    """Return the capabilities for the body that produced this profile."""
    iopcode = read_iopcode(profile)
    if iopcode is not None and is_xprocessor5(iopcode):
        return _XPROCESSOR5
    return _DEFAULT
