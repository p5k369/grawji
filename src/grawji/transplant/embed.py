"""Embed a donor RAF's raw pixels into a native carrier frame.

The carrier is an uncompressed RAF from the connected body. Its raw
pixel array is located inside the proprietary CFA container by matching
a row of rawpy's decode (the container is not a plain TIFF, so the
array cannot be addressed by header fields alone). The donor raw is
black-level matched and placed at the offset that aligns its X-Trans
6x6 color phase with the carrier's. The rest of the frame keeps the
carrier's pixels as padding. Bodies can start the pattern on a
different phase, and a wrong phase scrambles the color channels into a
magenta cast, so the offset is derived from each RAF's CFA pattern
rather than assumed zero.

The CFA header dimensions stay NATIVE on purpose: the engine's geometry
correction is calibrated at the native scale, and declaring the donor's
smaller frame instead warps the edges. The caller crops the donor
region out of the rendered JPEG afterwards.

The white balance the engine applies is BAKED into the carrier's meta
block as a G/R/B multiplier triple (u16 LE, stored with copies), not
taken from the d185 profile's WB mode byte. build_transplant therefore
copies the donor's own as-shot triple over the carrier's, so any donor
white balance, including Auto, renders as the donor camera chose it.
The carrier's triple comes from libraw's camera_whitebalance, which
reads exactly these stored integers, so its copies can be found by
value with no per-body offsets. For the donor, an Auto WB shot stores
two triples, the plain auto levels and the levels with the shooting
fine-tune applied. The engine starts from the plain levels and adds a
recipe's shift itself, so copying libraw's triple would apply the shift twice.
The plain auto levels are read from the metadata and win when present.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import rawpy

from grawji.imaging import imagemeta
from grawji.transplant import lens
from grawji.transplant.errors import TransplantError

__all__ = [
    "Transplant",
    "TransplantError",
    "build_transplant",
    "cfa_phase_offset",
    "embed_frame",
    "locate_pixel_base",
    "scale_exposure",
    "transfer_as_shot_wb",
]

# The RAF offset table holds big-endian u32 fields starting at 0x54.
_META_OFFSET_FIELD = 0x5C
_CFA_OFFSET_FIELD = 0x64
# The tested sensors store 14-bit values in u16 cells.
_WHITE = 16383


@dataclass(frozen=True)
class Transplant:
    """A carrier RAF with the donor's pixels embedded.

    Attributes:
        raf: The patched carrier RAF.
        flip: The donor's libraw flip code.
        donor_size: Height and width of the donor raw frame.
        carrier_size: Height and width of the carrier raw frame.
    """

    raf: bytes
    flip: int
    donor_size: tuple[int, int]
    carrier_size: tuple[int, int]
    offset: tuple[int, int]


@dataclass(frozen=True)
class _RawFrame:
    """A decoded raw frame with its black level, orientation and CFA."""

    pixels: npt.NDArray[np.uint16]
    black: int
    flip: int
    pattern: npt.NDArray[np.str_]
    wb: tuple[int, int, int] = (0, 0, 0)
    # top, left, height, width of the visible sensor area in pixels
    visible: tuple[int, int, int, int] = (0, 0, 0, 0)


def _decode(path: Path) -> _RawFrame:
    """Decode a RAF's raw sensor data with rawpy."""
    with rawpy.imread(str(path)) as raw:
        pat = np.asarray(raw.raw_pattern)
        desc = raw.color_desc.decode()
        letters = np.array(
            [
                [desc[pat[i, j]] for j in range(pat.shape[1])]
                for i in range(pat.shape[0])
            ]
        )
        # libraw reports the RAF's stored WB triple as (R, G, B, 0);
        # the file stores it in G/R/B order.
        r, g, b = (round(v) for v in raw.camera_whitebalance[:3])
        sizes = raw.sizes
        return _RawFrame(
            pixels=np.asarray(raw.raw_image, dtype=np.uint16).copy(),
            black=int(raw.black_level_per_channel[0]),
            flip=int(sizes.flip),
            pattern=letters,
            wb=(int(g), int(r), int(b)),
            visible=(
                int(sizes.top_margin),
                int(sizes.left_margin),
                int(sizes.height),
                int(sizes.width),
            ),
        )


def cfa_phase_offset(
    donor: npt.NDArray[np.str_], carrier: npt.NDArray[np.str_]
) -> tuple[int, int]:
    """Row/col offset placing the donor on the carrier's CFA phase."""
    rows, cols = donor.shape
    for dy in range(rows):
        for dx in range(cols):
            rolled = np.roll(np.roll(carrier, -dy, axis=0), -dx, axis=1)
            if np.array_equal(rolled, donor):
                return dy, dx
    raise TransplantError(
        "donor and carrier CFA patterns cannot be phase-aligned"
    )


def locate_pixel_base(blob: bytes, pixels: npt.NDArray[np.uint16]) -> int:
    """Find the raw pixel array inside the carrier's CFA container."""
    (cfa_off,) = struct.unpack_from(">I", blob, _CFA_OFFSET_FIELD)
    height, width = (int(dim) for dim in pixels.shape)
    row = height // 2
    needle = pixels[row].astype("<u2").tobytes()
    index = blob.find(needle, cfa_off)
    if index < 0:
        raise TransplantError(
            "could not locate the carrier pixel array. The carrier "
            "must be an uncompressed RAF from the connected body"
        )
    return index - row * width * 2


def embed_frame(
    carrier: npt.NDArray[np.uint16],
    donor: npt.NDArray[np.uint16],
    black_delta: int,
    offset: tuple[int, int] = (0, 0),
) -> npt.NDArray[np.uint16]:
    """Place the donor raw into the carrier frame at offset."""
    donor_h, donor_w = donor.shape
    carrier_h, carrier_w = carrier.shape
    off_y, off_x = offset
    if donor_h + off_y > carrier_h or donor_w + off_x > carrier_w:
        raise TransplantError(
            f"donor frame {donor_w}x{donor_h} at offset {offset} does not "
            f"fit the carrier's {carrier_w}x{carrier_h}"
        )
    shifted = np.clip(donor.astype(np.int32) + black_delta, 0, _WHITE)
    frame = carrier.copy()
    frame[off_y : off_y + donor_h, off_x : off_x + donor_w] = shifted.astype(
        np.uint16
    )
    return frame


def transfer_as_shot_wb(
    blob: bytearray,
    carrier_wb: tuple[int, int, int],
    donor_wb: tuple[int, int, int],
) -> int:
    """Copy the donor's baked WB triple over the carrier's, in place."""
    if donor_wb == carrier_wb:
        return 0
    meta_off, meta_len = struct.unpack_from(">2I", blob, _META_OFFSET_FIELD)
    needle = struct.pack("<3H", *carrier_wb)
    replacement = struct.pack("<3H", *donor_wb)
    count = 0
    index = blob.find(needle, meta_off, meta_off + meta_len)
    while index >= 0:
        blob[index : index + len(needle)] = replacement
        count += 1
        index = blob.find(needle, index, meta_off + meta_len)
    if not count:
        raise TransplantError(
            "could not locate the carrier's white-balance triple in "
            "the meta block. Cannot transfer the donor's white balance"
        )
    return count


def scale_exposure(
    pixels: npt.NDArray[np.uint16], black: int, scale: float
) -> npt.NDArray[np.uint16]:
    """Scale black-subtracted linear sensor values by an exposure factor."""
    if not 0.0 < scale <= 1.0:
        raise TransplantError(
            f"exposure scale {scale} is not in (0, 1]; scaling up "
            "would clip at the 14-bit ceiling"
        )
    if scale == 1.0:
        return pixels
    scaled = np.clip(
        np.round((pixels.astype(np.float64) - black) * scale) + black,
        0,
        _WHITE,
    )
    return scaled.astype(np.uint16)


def undo_vignetting(
    pixels: npt.NDArray[np.uint16],
    black: int,
    visible: tuple[int, int, int, int],
    tables: lens.LensTables,
) -> npt.NDArray[np.uint16]:
    """Multiply the visible sensor area by the lens's vignetting gain."""
    top, left, height, width = visible
    gain = lens.vignetting_gain(tables, height, width)
    frame = pixels.astype(np.float64)
    area = frame[top : top + height, left : left + width]
    area = (area - black) * gain + black
    frame[top : top + height, left : left + width] = area
    return np.clip(np.round(frame), 0, _WHITE).astype(np.uint16)


def build_transplant(
    donor: Path,
    carrier: Path,
    *,
    transfer_wb: bool = True,
    exposure_scale: float = 1.0,
    donor_lens: lens.LensTables | None = None,
) -> Transplant:
    """Embed the donor RAF's pixels into the carrier RAF."""
    blob = bytearray(carrier.read_bytes())
    carrier_frame = _decode(carrier)
    donor_frame = _decode(donor)
    donor_wb = imagemeta.fuji_auto_wb_levels(str(donor)) or donor_frame.wb
    base = locate_pixel_base(bytes(blob), carrier_frame.pixels)
    offset = cfa_phase_offset(donor_frame.pattern, carrier_frame.pattern)
    donor_pixels = donor_frame.pixels
    if donor_lens is not None:
        donor_pixels = undo_vignetting(
            donor_pixels, donor_frame.black, donor_frame.visible, donor_lens
        )
    frame = embed_frame(
        carrier_frame.pixels,
        scale_exposure(donor_pixels, donor_frame.black, exposure_scale),
        carrier_frame.black - donor_frame.black,
        offset,
    )
    height, width = frame.shape
    blob[base : base + height * width * 2] = frame.astype("<u2").tobytes()
    if transfer_wb and min(donor_wb) > 0 and min(carrier_frame.wb) > 0:
        transfer_as_shot_wb(blob, carrier_frame.wb, donor_wb)
    donor_h, donor_w = donor_frame.pixels.shape
    return Transplant(
        raf=bytes(blob),
        flip=donor_frame.flip,
        donor_size=(int(donor_h), int(donor_w)),
        carrier_size=(int(height), int(width)),
        offset=(int(offset[0]), int(offset[1])),
    )
