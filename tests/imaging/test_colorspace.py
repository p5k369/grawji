"""Tests for telling and converting an edit's color space."""

from __future__ import annotations

import struct

import numpy as np

from grawji.imaging import colorspace


def profile(*colorants: tuple[float, float, float]) -> bytes:
    """A bare ICC profile carrying red, green and blue colorants."""
    tags = []
    body = b""
    base = 128 + 4 + 12 * len(colorants)
    for signature, xyz in zip(
        (b"rXYZ", b"gXYZ", b"bXYZ"), colorants, strict=True
    ):
        data = b"XYZ \0\0\0\0" + struct.pack(
            ">iii", *(round(v * 65536) for v in xyz)
        )
        tags.append(
            struct.pack(">4sII", signature, base + len(body), len(data))
        )
        body += data
    return b"\0" * 128 + struct.pack(">I", len(tags)) + b"".join(tags) + body


ADOBE = profile(
    (0.6097, 0.3111, 0.0195),
    (0.2052, 0.6257, 0.0609),
    (0.1492, 0.0632, 0.7448),
)
SRGB = profile(
    (0.4361, 0.2225, 0.0139),
    (0.3851, 0.7169, 0.0971),
    (0.1431, 0.0606, 0.7141),
)
PROPHOTO = profile(
    (0.7977, 0.2880, 0.0), (0.1352, 0.7119, 0.0), (0.0313, 0.0001, 0.8249)
)


def test_a_profile_is_told_by_its_colorants() -> None:
    """Both sRGB and AdobeRGB are recognized, anything else is not."""
    assert colorspace.profile_space(ADOBE) == colorspace.ADOBE_RGB
    assert colorspace.profile_space(SRGB) == colorspace.SRGB
    assert colorspace.profile_space(PROPHOTO) is None
    assert colorspace.profile_space(b"junk") is None


def test_the_profile_wins_over_the_exif_flag() -> None:
    """A profile decides, the EXIF flag only speaks without one."""
    assert colorspace.space_of(ADOBE, "R98") == colorspace.ADOBE_RGB
    assert colorspace.space_of(SRGB, "R03") == colorspace.SRGB
    assert colorspace.space_of(None, "R03") == colorspace.ADOBE_RGB
    assert colorspace.space_of(PROPHOTO, "R03") == colorspace.ADOBE_RGB
    assert colorspace.space_of(None, "R98") == colorspace.SRGB
    assert colorspace.space_of(None, None) == colorspace.SRGB


def test_adobe_rgb_pixels_come_over_to_srgb() -> None:
    """Neutrals stay neutral, saturated colors grow, sRGB passes through."""
    pixels = np.array(
        [[[128, 128, 128], [0, 255, 0], [100, 200, 100], [255, 255, 255]]],
        np.uint8,
    )
    assert colorspace.to_srgb(pixels, colorspace.SRGB) is pixels
    out = colorspace.to_srgb(pixels, colorspace.ADOBE_RGB)
    assert out.dtype == np.uint8
    gray = out[0, 0]
    assert abs(int(gray[0]) - int(gray[1])) <= 1
    assert abs(int(gray[1]) - int(gray[2])) <= 1
    assert tuple(out[0, 1]) == (0, 255, 0)
    assert int(out[0, 2, 1]) - int(out[0, 2, 0]) > 100
    assert tuple(out[0, 3]) == (255, 255, 255)
