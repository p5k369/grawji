"""Per-camera capabilities derived from the profile's processor code."""

from __future__ import annotations

from dataclasses import dataclass

from rawji.fuji_profile import is_xprocessor5, read_iopcode

__all__ = [
    "Capabilities",
    "capabilities_for",
    "is_xprocessor5",
    "read_iopcode",
]


@dataclass(frozen=True)
class Capabilities:
    """Recipe limits that vary by camera processor."""

    tone_min: int
    tone_max: int
    tone_half_step: bool


# Highlight/shadow tone is -2 to +4 on every body verified so far.
_DEFAULT = Capabilities(tone_min=-2, tone_max=4, tone_half_step=False)
_XPROCESSOR5 = Capabilities(tone_min=-2, tone_max=4, tone_half_step=True)


def capabilities_for(profile: bytes) -> Capabilities:
    """Return the capabilities for the body that produced this profile."""
    iopcode = read_iopcode(profile)
    if iopcode is not None and is_xprocessor5(iopcode):
        return _XPROCESSOR5
    return _DEFAULT
