"""Patch a d185 profile for a transplant conversion."""

from __future__ import annotations

import struct

import rawji

# d185 profile params sit at 513 + index*4.
_EV_OFFSET = 513 + 4 * 4
_FILM_SIM_OFFSET = 513 + 7 * 4
_WB_OFFSET = 513 + 11 * 4
_WB_SHIFT_R_OFFSET = 513 + 12 * 4
_WB_SHIFT_B_OFFSET = 513 + 13 * 4
# Carrier settings that bleed into the donor render unless reset:
# the WB shifts, WB color temperature, highlights, shadows, color and
# sharpness.
_NEUTRAL_OFFSETS = (
    _WB_SHIFT_R_OFFSET,
    _WB_SHIFT_B_OFFSET,
    513 + 14 * 4,
    513 + 15 * 4,
    513 + 16 * 4,
    513 + 17 * 4,
    513 + 18 * 4,
)
# The engine honors -2..+3 EV; anything below -2 silently falls back
# to 0 EV, so out-of-range values must never be written.
_EV_RANGE = (-2.0, 3.0)


def patch_profile(
    profile: bytes,
    *,
    film_sim: str | None = None,
    wb: str = "Daylight",
    neutralize: bool = True,
    wb_shift: tuple[int, int] = (0, 0),
) -> bytes:
    """Return the profile with the transplant recipe applied.

    Args:
        profile: The d185 profile bytes.
        film_sim: rawji FilmSimulation member name to apply.
        wb: rawji WhiteBalance member name written when neutralizing.
        neutralize: Reset the carrier's WB shifts, color temperature and
            tone settings so they do not render on the donor image.
        wb_shift: White-balance shift applied after neutralizing.
    """
    prof = bytearray(profile)
    if film_sim is not None:
        code = int(rawji.FilmSimulation[film_sim])
        struct.pack_into("<I", prof, _FILM_SIM_OFFSET, code)
    if neutralize:
        wb_code = int(rawji.WhiteBalance[wb])
        struct.pack_into("<I", prof, _WB_OFFSET, wb_code)
        for offset in _NEUTRAL_OFFSETS:
            struct.pack_into("<i", prof, offset, 0)
    if wb_shift != (0, 0):
        struct.pack_into("<i", prof, _WB_SHIFT_R_OFFSET, wb_shift[0])
        struct.pack_into("<i", prof, _WB_SHIFT_B_OFFSET, wb_shift[1])
    return bytes(prof)


def patch_ev(profile: bytes, ev: float) -> bytes:
    """Write an exposure offset into the profile."""
    low, high = _EV_RANGE
    if not low <= ev <= high:
        raise ValueError(
            f"EV {ev:+g} is outside the engine's honored {low:+g} to "
            f"{high:+g} range"
        )
    prof = bytearray(profile)
    struct.pack_into("<i", prof, _EV_OFFSET, round(ev * 1000))
    return bytes(prof)
