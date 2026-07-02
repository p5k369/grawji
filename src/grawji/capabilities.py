"""Per-camera capabilities derived from the profile's processor code."""

from __future__ import annotations

from dataclasses import dataclass

from rawji.fuji_profile import (
    PROFILE_PARAMS_OFFSET,
    is_xprocessor5,
    read_iopcode,
)

_OFFSET_SMOOTH_SKIN = PROFILE_PARAMS_OFFSET + 23 * 4
_OFFSET_COLOR_CHROME_BLUE = PROFILE_PARAMS_OFFSET + 24 * 4
_OFFSET_CLARITY = PROFILE_PARAMS_OFFSET + 26 * 4

__all__ = [
    "Capabilities",
    "capabilities_for",
    "is_xprocessor5",
    "read_iopcode",
]


@dataclass(frozen=True)
class Capabilities:
    """Recipe limits that vary by camera body."""

    tone_min: int
    tone_max: int
    tone_half_step: bool
    has_clarity: bool
    has_color_chrome_blue: bool
    has_smooth_skin: bool


def capabilities_for(profile: bytes) -> Capabilities:
    """Return the capabilities for the body that produced this profile."""
    iopcode = read_iopcode(profile)
    xproc5 = iopcode is not None and is_xprocessor5(iopcode)
    return Capabilities(
        tone_min=-2,
        tone_max=4,
        tone_half_step=xproc5,
        has_clarity=len(profile) >= _OFFSET_CLARITY + 4,
        has_color_chrome_blue=len(profile) >= _OFFSET_COLOR_CHROME_BLUE + 4,
        has_smooth_skin=len(profile) >= _OFFSET_SMOOTH_SKIN + 4,
    )
