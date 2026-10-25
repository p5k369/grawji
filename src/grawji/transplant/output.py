"""Crop and rotate a rendered transplant back to the donor image.

The donor sits at the top left of the carrier frame, so its content
occupies the top-left donor/carrier fraction of the render whatever the
padding renders as. The proportional crop is then inset by a few pixels
on every edge: the demosaic and sharpening run across the donor-to-
padding boundary and leave a seam there (BRIGHT overshoot rows, found
2026-09-14 when a +2 EV render made them visible), and the crop
rounding can leave a sliver of the padding itself. A fixed inset
removes both deterministically. Finally the image is rotated upright
per the donor's orientation.
"""

from __future__ import annotations

import io

from PIL import Image

from grawji.transplant import lens
from grawji.transplant.lens import LensTables

# libraw flip -> counterclockwise degrees to bring the image upright.
_FLIP_ROTATION = {3: 180, 5: 90, 6: -90}
# Seam inset: this fraction of the shorter crop side, at least the
# minimum, shaved off every edge of the donor crop.
_INSET_FRACTION = 0.002
_INSET_MIN = 2
# A thumbnail whose aspect is this close to the carrier's has no bars.
_ASPECT_TOLERANCE = 0.01


def finish_jpeg(  # noqa: PLR0913
    jpeg: bytes,
    *,
    flip: int,
    donor_size: tuple[int, int],
    carrier_size: tuple[int, int],
    offset: tuple[int, int] = (0, 0),
    donor_lens: LensTables | None = None,
    compensate: bool = True,
    quality: int = 95,
    letterboxed: bool = False,
    inset: bool = True,
) -> bytes:
    """Return the render cropped to the donor region and rotated.

    Args:
        jpeg: The image downloaded from the camera.
        flip: The donor's libraw flip code from the Transplant.
        donor_size: Height and width of the donor raw frame.
        carrier_size: Height and width of the carrier raw frame.
        offset: The donor's placement in the carrier frame.
        donor_lens: The donor lens's tables, for the distortion
            correction after the crop.
        compensate: Add the output sharpening that offsets the remap's
            high-frequency loss.
        quality: JPEG quality of the re-encoded output.
        letterboxed: The render is a camera thumbnail with the carrier
            frame letterboxed inside a 4:3 canvas.
        inset: Shave the boundary seam off every edge. A transplant
            embedded with picture context has no seam and keeps the
            donor's full picture.
    """
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if letterboxed:
        image = _unletterbox(image, carrier_size)
    width, height = image.size
    donor_h, donor_w = donor_size
    carrier_h, carrier_w = carrier_size
    off_y, off_x = offset
    left = round(width * off_x / carrier_w)
    top = round(height * off_y / carrier_h)
    image = image.crop(
        (
            left,
            top,
            left + round(width * donor_w / carrier_w),
            top + round(height * donor_h / carrier_h),
        )
    )
    if donor_lens is not None:
        image = lens.apply_donor_correction(
            image, donor_lens, compensate=compensate
        )
    if inset:
        image = _inset_seam(image)
    rotation = _FLIP_ROTATION.get(flip, 0)
    if rotation:
        image = image.rotate(rotation, expand=True)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """Width and height of JPEG bytes."""
    with Image.open(io.BytesIO(jpeg)) as image:
        return image.size


def _unletterbox(
    image: Image.Image, carrier_size: tuple[int, int]
) -> Image.Image:
    """Cut the carrier-aspect frame out of a letterboxed thumbnail."""
    carrier_h, carrier_w = carrier_size
    aspect = carrier_w / carrier_h
    width, height = image.size
    if abs(width / height - aspect) < _ASPECT_TOLERANCE:
        return image
    if width / height > aspect:
        frame_w = round(height * aspect)
        left = (width - frame_w) // 2
        return image.crop((left, 0, left + frame_w, height))
    frame_h = round(width / aspect)
    top = (height - frame_h) // 2
    return image.crop((0, top, width, top + frame_h))


def _inset_seam(image: Image.Image) -> Image.Image:
    """Shave the boundary seam and crop rounding off every edge."""
    inset = max(_INSET_MIN, round(min(image.size) * _INSET_FRACTION))
    if 2 * inset >= min(image.size):
        return image
    return image.crop(
        (inset, inset, image.width - inset, image.height - inset)
    )
