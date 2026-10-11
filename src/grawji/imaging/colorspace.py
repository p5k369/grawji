"""Tell a picture's color space and bring AdobeRGB pixels to sRGB."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

SRGB = "sRGB"
ADOBE_RGB = "AdobeRGB"
# The EXIF interoperability index of an AdobeRGB JPEG (DCF option file).
ADOBE_RGB_INTEROP = "R03"
# The colorant tags of a profile, as D50 XYZ, for the two spaces.
_PRIMARIES = {
    SRGB: (
        (0.4361, 0.2225, 0.0139),
        (0.3851, 0.7169, 0.0971),
        (0.1431, 0.0606, 0.7141),
    ),
    ADOBE_RGB: (
        (0.6097, 0.3111, 0.0195),
        (0.2052, 0.6257, 0.0609),
        (0.1492, 0.0632, 0.7448),
    ),
}
_TOLERANCE = 0.02
_HEADER = 128
_TAG = 12
_ADOBE_GAMMA = 563 / 256
_SRGB_KNEE = 0.0031308
# AdobeRGB to XYZ then XYZ to linear sRGB, both for D65.
_ADOBE_TO_XYZ = np.array(
    [
        [0.5767309, 0.1855540, 0.1881852],
        [0.2973769, 0.6273491, 0.0752741],
        [0.0270343, 0.0706872, 0.9911085],
    ]
)
_XYZ_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ]
)
_ADOBE_TO_SRGB = (_XYZ_TO_SRGB @ _ADOBE_TO_XYZ).astype(np.float32)


def profile_space(icc: bytes) -> str | None:
    """The space an ICC profile describes."""
    found = _colorants(icc)
    if found is None:
        return None
    for space, wanted in _PRIMARIES.items():
        if all(
            abs(a - b) <= _TOLERANCE
            for have, want in zip(found, wanted, strict=True)
            for a, b in zip(have, want, strict=True)
        ):
            return space
    return None


def space_of(icc: bytes | None, interop: str | None) -> str:
    """A JPEG's color space from its profile."""
    if icc:
        space = profile_space(icc)
        if space is not None:
            return space
    if interop is not None and interop.strip() == ADOBE_RGB_INTEROP:
        return ADOBE_RGB
    return SRGB


def to_srgb(rgb: NDArray[np.uint8], space: str) -> NDArray[np.uint8]:
    """The pixels as sRGB, converted from AdobeRGB where that is the space."""
    if space != ADOBE_RGB:
        return rgb
    linear = (rgb[..., :3].astype(np.float32) / 255.0) ** _ADOBE_GAMMA
    srgb_linear = np.clip(linear @ _ADOBE_TO_SRGB.T, 0.0, 1.0)
    encoded = np.where(
        srgb_linear <= _SRGB_KNEE,
        srgb_linear * 12.92,
        1.055 * np.power(srgb_linear, 1 / 2.4) - 0.055,
    )
    return np.asarray((encoded * 255.0).round().astype(np.uint8))


def _colorants(
    icc: bytes,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None:
    """The red, green and blue colorant XYZ values of a profile."""
    try:
        (count,) = struct.unpack(">I", icc[_HEADER : _HEADER + 4])
        tags = {}
        for index in range(count):
            start = _HEADER + 4 + _TAG * index
            signature, offset, _size = struct.unpack(
                ">4sII", icc[start : start + _TAG]
            )
            if signature in (b"rXYZ", b"gXYZ", b"bXYZ"):
                tags[signature] = tuple(
                    value / 65536
                    for value in struct.unpack(
                        ">iii", icc[offset + 8 : offset + 20]
                    )
                )
        return tags[b"rXYZ"], tags[b"gXYZ"], tags[b"bXYZ"]
    except (KeyError, struct.error):
        return None
