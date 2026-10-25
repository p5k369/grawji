"""Donor lens correction from the tables every Fujifilm RAF carries.

The conversion engine corrects a lens from a body-internal table keyed
on the lens ID of the carrier RAF. A carrier shot without a lens carries
no ID, so the engine renders the donor pixels uncorrected, and the donor's
own correction has to be applied by grawji. The data for it is in the donor
RAF itself: Fuji writes three radial tables (GeometricDistortionParams,
ChromaticAberrationParams, VignettingParams), each a leading number,
the knots as radii normalized to the half diagonal, then the values,
distortion in percent, vignetting as percent transmission. No lens
database is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lsdetect
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageFilter

from grawji.imaging import imagemeta
from grawji.transplant.errors import TransplantError

# Fewer knots than this is not a radial table.
_MIN_KNOTS = 2
# The tables' radius unit is this much larger than the half diagonal of
# the frame
RADIUS_SCALE = 1.01
# The corrected image is zoomed by this much, as the engine does to
# fill the frame after pulling the corners outward.
FILL_SCALE = 1.01
# The Lanczos remap low-passes the finest detail. This mild
# unsharp mask after the remap compensates the resampler's MTF loss to
# within a few percent without visible halos (tuned on the same renders).
_MTF_RADIUS = 0.8
_MTF_PERCENT = 30


@dataclass(frozen=True)
class LensTables:
    """The radial correction tables of one RAF.

    Attributes:
        knots: Radii the values are given at, normalized to the half
            diagonal, ascending.
        distortion: Percent the source radius exceeds the corrected
            radius at each knot.
        vignetting: Percent transmission at each knot.
        chromatic_red: Red radius offset at each knot, relative.
        chromatic_blue: Blue radius offset at each knot, relative.
    """

    knots: tuple[float, ...]
    distortion: tuple[float, ...]
    vignetting: tuple[float, ...]
    chromatic_red: tuple[float, ...]
    chromatic_blue: tuple[float, ...]

    @property
    def corrects(self) -> bool:
        """Whether the tables hold any correction at all.

        A lensless shot carries tables of zeros.
        """
        return any(self.distortion) or any(
            value not in (0.0, 100.0) for value in self.vignetting
        )


def lens_tables(path: Path | str) -> LensTables | None:
    """Read a RAF's correction tables."""
    raw = imagemeta.fuji_lens_tables(str(path))
    if raw is None:
        return None
    distortion, chromatic, vignetting = raw
    count = (len(distortion) - 1) // 2
    if count < _MIN_KNOTS or len(vignetting) < 1 + 2 * count:
        msg = f"{path} carries lens tables of an unknown layout"
        raise TransplantError(msg)
    if len(chromatic) >= 1 + 3 * count:
        red = tuple(chromatic[1 + count : 1 + 2 * count])
        blue = tuple(chromatic[1 + 2 * count : 1 + 3 * count])
    else:
        red = blue = (0.0,) * count
    return LensTables(
        knots=tuple(distortion[1 : 1 + count]),
        distortion=tuple(distortion[1 + count : 1 + 2 * count]),
        vignetting=tuple(vignetting[1 + count : 1 + 2 * count]),
        chromatic_red=red,
        chromatic_blue=blue,
    )


def donor_correction_from_paths(
    donor: Path | str, carrier: Path | str
) -> LensTables:
    """The donor's correction tables for a transplant."""
    carrier_tables = lens_tables(carrier)
    if carrier_tables is not None and carrier_tables.corrects:
        raise TransplantError(
            "the carrier was shot through a lens, so the engine bakes "
            "that lens's correction into the render. Use a carrier shot "
            "without a lens"
        )
    tables = lens_tables(donor)
    if tables is None:
        raise TransplantError(
            "the donor carries no lens correction tables. It cannot be "
            "corrected"
        )
    return tables


def radius(height: int, width: int) -> npt.NDArray[np.float64]:
    """Every pixel's radius in the tables unit for a frame of this size."""
    cy, cx = (height - 1) / 2, (width - 1) / 2
    yy, xx = np.mgrid[0:height, 0:width]
    return np.asarray(
        np.hypot(xx - cx, yy - cy) / np.hypot(cx, cy) * RADIUS_SCALE,
        dtype=np.float64,
    )


def vignetting_gain(
    tables: LensTables, height: int, width: int
) -> npt.NDArray[np.float64]:
    """The per-pixel gain that undoes the vignetting of a raw frame."""
    knots = np.concatenate([[0.0], tables.knots])
    transmission = np.concatenate(
        [[1.0], np.asarray(tables.vignetting) / 100.0]
    )
    return np.asarray(
        1.0 / np.interp(radius(height, width), knots, transmission)
    )


def distortion_factor(
    tables: LensTables,
    r: npt.NDArray[np.float64],
    fill: float = FILL_SCALE,
) -> npt.NDArray[np.float64]:
    """Source radius over corrected radius at corrected radius r."""
    factor = np.asarray(tables.distortion) / 100.0 + 1.0
    knots_out = np.asarray(tables.knots) / factor
    return np.asarray(np.interp(r, knots_out, factor) * fill)


def fill_scale(tables: LensTables, height: int, width: int) -> float:
    """The zoom the correction may apply without sampling off the frame."""
    cy, cx = (height - 1) / 2, (width - 1) / 2
    along_x = np.linspace(-cx, cx, 64)
    along_y = np.linspace(-cy, cy, 64)
    edge_x = np.concatenate(
        [along_x, along_x, np.full(64, -cx), np.full(64, cx)]
    )
    edge_y = np.concatenate(
        [np.full(64, -cy), np.full(64, cy), along_y, along_y]
    )
    r = np.hypot(edge_x, edge_y) / np.hypot(cx, cy) * RADIUS_SCALE
    outward = distortion_factor(tables, r, fill=1.0)
    along = np.maximum(np.abs(edge_x) / cx, np.abs(edge_y) / cy)
    limit = float(np.min(1.0 / np.maximum(outward * along, 1e-9)))
    return min(FILL_SCALE, limit)


def distortion_maps(
    tables: LensTables, height: int, width: int
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """Source coordinates of every output pixel."""
    cy, cx = (height - 1) / 2, (width - 1) / 2
    factor = distortion_factor(
        tables, radius(height, width), fill_scale(tables, height, width)
    )
    yy, xx = np.mgrid[0:height, 0:width]
    map_x = np.ascontiguousarray(cx + factor * (xx - cx), dtype=np.float32)
    map_y = np.ascontiguousarray(cy + factor * (yy - cy), dtype=np.float32)
    return map_x, map_y


def apply_donor_correction(
    image: Image.Image, donor: LensTables, *, compensate: bool = True
) -> Image.Image:
    """Apply the donor's distortion correction to a cropped image."""
    if not any(donor.distortion):
        return image
    map_x, map_y = distortion_maps(donor, image.height, image.width)
    pixels = np.ascontiguousarray(np.asarray(image.convert("RGB")))
    corrected = Image.fromarray(
        lsdetect.remap_rgb(pixels, map_x, map_y, "lanczos3")
    )
    if compensate:
        corrected = corrected.filter(
            ImageFilter.UnsharpMask(
                radius=_MTF_RADIUS, percent=_MTF_PERCENT, threshold=0
            )
        )
    return corrected
