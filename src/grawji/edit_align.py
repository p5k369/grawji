"""Find where an edited export of a shot sits in the camera's frame."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lsdetect
import numpy as np

from grawji.imaging.pixbufs import area_resize

if TYPE_CHECKING:
    from numpy.typing import NDArray

# The frame is searched at this width, the edit at this long edge.
_FRAME_WIDTH = 160
_EDIT_EDGE = 200
_ANGLES = np.arange(-6.0, 6.01, 1.5)
# How wide the edit is in the frame: a quarter of it up to all of it.
_SCALES = np.exp(np.linspace(math.log(0.25), 0.0, 22))
_FINE_ANGLES = np.arange(-1.5, 1.51, 0.25)
_FINE_SCALES = np.exp(np.linspace(-0.06, 0.06, 13))
# Below this correlation the edit shows another shot, or nothing the
# frame can be matched by.
MIN_SCORE = 0.6
# A border row or column is flat to within this many levels.
_BORDER_TOLERANCE = 6
_BORDER_SHARE = 0.995
# The share of each edge left out of the search.
_MARGIN = 0.03
_LUMA = np.array([0.299, 0.587, 0.114])
_OPAQUE = 255


@dataclass(frozen=True)
class Alignment:
    """Where an edit sits in the frame."""

    matrix: NDArray[np.float64]
    score: float

    def warp(
        self, frame: NDArray[np.uint8], size: tuple[int, int]
    ) -> NDArray[np.uint8]:
        """A frame-sized image seen the way the edit shows it."""
        a, b, c, d, e, f, g, h, i = (
            float(value) for value in np.linalg.inv(self.matrix).ravel()
        )
        return np.asarray(
            lsdetect.warp_rgb(
                np.ascontiguousarray(frame[..., :3]),
                (a, b, c, d, e, f, g, h, i),
                1.0,
                0.0,
                0.0,
                filter="bilinear",
                out_size=size,
            )
        )

    def coverage(
        self, frame_size: tuple[int, int], size: tuple[int, int]
    ) -> NDArray[np.bool_]:
        """Which edit pixels the frame covers."""
        width, height = frame_size
        full = np.full((height, width, 3), _OPAQUE, dtype=np.uint8)
        return np.asarray(self.warp(full, size)[..., 0] == _OPAQUE)


def align(
    frame: NDArray[np.uint8], edit: NDArray[np.uint8]
) -> Alignment | None:
    """Where the edit sits in the frame."""
    top, bottom, left, right = border(edit)
    height, width = edit.shape[:2]
    # The edit is searched without a thin margin. An uncropped export
    # shows the whole frame, and a template the size of the frame never
    # fits inside it once lens corrections differ by a pixel.
    top += round(height * _MARGIN)
    bottom += round(height * _MARGIN)
    left += round(width * _MARGIN)
    right += round(width * _MARGIN)
    inner = edit[top : height - bottom, left : width - right]
    found = _align_inner(frame, inner)
    if found is None:
        return None
    shift = np.eye(3)
    shift[0, 2] = left
    shift[1, 2] = top
    return Alignment(shift @ found.matrix, found.score)


def _align_inner(
    frame: NDArray[np.uint8], edit: NDArray[np.uint8]
) -> Alignment | None:
    """Where an edit without border or margin sits in the frame."""
    frame_height, frame_width = frame.shape[:2]
    edit_height, edit_width = edit.shape[:2]
    frame_small = _shrink(frame, _FRAME_WIDTH)
    edit_small = _shrink(
        edit, round(_EDIT_EDGE * edit_width / max(edit_width, edit_height))
    )
    frame_edges = _edges(frame_small)
    edit_edges = _edges(edit_small)
    coarse, quarter = max(
        ((_search(frame_edges, np.rot90(edit_edges, k)), k) for k in range(4)),
        key=lambda found: found[0].score,
    )
    turned = np.rot90(edit_edges, quarter)
    fit = _search(
        frame_edges,
        turned,
        coarse.angle + _FINE_ANGLES,
        coarse.scale * _FINE_SCALES,
    )
    if fit.score < MIN_SCORE:
        return None
    to_small = _scaling(
        frame_small.shape[1] / frame_width,
        frame_small.shape[0] / frame_height,
    )
    placed = np.eye(3)
    placed[0, 2] = -fit.x + fit.origin[0]
    placed[1, 2] = -fit.y + fit.origin[1]
    unturn = _unturn(quarter, edit_edges.shape)
    to_edit = _scaling(
        edit_width / edit_small.shape[1], edit_height / edit_small.shape[0]
    )
    matrix = to_edit @ unturn @ fit.to_turned @ placed @ to_small
    return Alignment(matrix, fit.score)


def border(rgb: NDArray[np.uint8]) -> tuple[int, int, int, int]:
    """The top, bottom, left and right widths of a flat framing border."""
    image = rgb[..., :3].astype(np.int16)
    edge = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]])
    color = np.median(edge, axis=0)
    flat = np.abs(image - color).max(axis=-1) <= _BORDER_TOLERANCE
    rows = flat.mean(axis=1) > _BORDER_SHARE
    cols = flat.mean(axis=0) > _BORDER_SHARE
    return (
        _run(rows),
        _run(rows[::-1]),
        _run(cols),
        _run(cols[::-1]),
    )


@dataclass(frozen=True)
class _Fit:
    """One placement of the turned edit in the searched frame."""

    score: float
    angle: float
    scale: float
    x: int
    y: int
    origin: tuple[float, float]
    to_turned: NDArray[np.float64]


def _run(line: NDArray[np.bool_]) -> int:
    """How many leading entries are set."""
    if line.all():
        return 0
    return int(np.argmin(line))


def _shrink(rgb: NDArray[np.uint8], width: int) -> NDArray[np.float64]:
    """Luminance, 0 to 1, at the given width."""
    height = max(1, round(rgb.shape[0] * width / rgb.shape[1]))
    small = area_resize(np.ascontiguousarray(rgb[..., :3]), width, height)
    return np.asarray(small.astype(np.float64) @ _LUMA / 255.0)


def _edges(gray: NDArray[np.float64]) -> NDArray[np.float64]:
    """Gradient magnitude, compressed so strong edges do not dominate."""
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1] = gray[2:] - gray[:-2]
    return np.asarray(np.sqrt(np.hypot(gx, gy)))


def _search(
    frame: NDArray[np.float64],
    turned: NDArray[np.float64],
    angles: NDArray[np.float64] = _ANGLES,
    scales: NDArray[np.float64] = _SCALES,
) -> _Fit:
    """The best placement over a grid of angles and scales."""
    best = _Fit(-2.0, 0.0, 1.0, 0, 0, (0.0, 0.0), np.eye(3))
    for angle in angles:
        for scale in scales:
            template, mask, origin, to_turned = _template(
                turned, float(angle), float(scale), frame.shape[1]
            )
            score = _masked_ncc(frame, template, mask)
            y, x = np.unravel_index(int(np.argmax(score)), score.shape)
            if score[y, x] > best.score:
                best = _Fit(
                    float(score[y, x]),
                    float(angle),
                    float(scale),
                    int(x),
                    int(y),
                    origin,
                    to_turned,
                )
    return best


def _template(
    edit: NDArray[np.float64], angle: float, scale: float, frame_width: int
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    tuple[float, float],
    NDArray[np.float64],
]:
    """The edit turned and scaled into frame pixels."""
    height, width = edit.shape
    factor = scale * frame_width / width
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    rotation = np.array([[cos, -sin], [sin, cos]])
    corners = np.array(
        [[0, 0], [width, 0], [0, height], [width, height]], dtype=float
    )
    placed = corners * factor @ rotation.T
    low = placed.min(axis=0)
    size = np.ceil(placed.max(axis=0) - low).astype(int)
    xs, ys = np.meshgrid(
        np.arange(size[0]) + low[0], np.arange(size[1]) + low[1]
    )
    inverse = rotation.T / factor
    ex = inverse[0, 0] * xs + inverse[0, 1] * ys
    ey = inverse[1, 0] * xs + inverse[1, 1] * ys
    values, inside = _sample(edit, ex, ey)
    to_turned = np.eye(3)
    to_turned[:2, :2] = inverse
    origin = (float(low[0]), float(low[1]))
    return values * inside, inside.astype(float), origin, to_turned


def _sample(
    image: NDArray[np.float64], x: NDArray[np.float64], y: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    """Bilinear samples at pixel coordinates."""
    height, width = image.shape
    inside = (x >= 0) & (x <= width - 1) & (y >= 0) & (y <= height - 1)
    x = np.clip(x, 0, width - 1.0001)
    y = np.clip(y, 0, height - 1.0001)
    x0 = x.astype(int)
    y0 = y.astype(int)
    fx = x - x0
    fy = y - y0
    values = (
        image[y0, x0] * (1 - fx) * (1 - fy)
        + image[y0, x0 + 1] * fx * (1 - fy)
        + image[y0 + 1, x0] * (1 - fx) * fy
        + image[y0 + 1, x0 + 1] * fx * fy
    )
    return values, inside


def _masked_ncc(
    image: NDArray[np.float64],
    template: NDArray[np.float64],
    mask: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Masked normalized cross-correlation of a template over an image."""
    image_h, image_w = image.shape
    tmpl_h, tmpl_w = template.shape
    if tmpl_h > image_h or tmpl_w > image_w:
        return np.full((1, 1), -1.0)
    shape = (image_h + tmpl_h, image_w + tmpl_w)

    def correlate(
        values: NDArray[np.float64], kernel: NDArray[np.complex128]
    ) -> NDArray[np.float64]:
        spectrum = np.fft.rfft2(values, shape) * kernel
        return np.asarray(np.fft.irfft2(spectrum, shape))

    count = mask.sum()
    masked = template * mask
    mask_kernel = np.conj(np.fft.rfft2(mask, shape))
    sum_image = correlate(image, mask_kernel)
    sum_image2 = correlate(image**2, mask_kernel)
    cross = correlate(image, np.conj(np.fft.rfft2(masked, shape)))
    sum_tmpl = masked.sum()
    numerator = cross - sum_image * sum_tmpl / count
    var_image = np.maximum(sum_image2 - sum_image**2 / count, 1e-9)
    var_tmpl = max(float((masked**2).sum() - sum_tmpl**2 / count), 1e-9)
    score = numerator / np.sqrt(var_image * var_tmpl)
    return score[: image_h - tmpl_h + 1, : image_w - tmpl_w + 1]


def _scaling(sx: float, sy: float) -> NDArray[np.float64]:
    """Scale pixel indices, keeping pixel centers on pixel centers."""
    matrix = np.eye(3)
    matrix[0, 0] = sx
    matrix[1, 1] = sy
    matrix[0, 2] = 0.5 * sx - 0.5
    matrix[1, 2] = 0.5 * sy - 0.5
    return matrix


def _unturn(quarter: int, shape: tuple[int, ...]) -> NDArray[np.float64]:
    """Map pixels of np.rot90(image, quarter) back to the image."""
    height, width = shape[:2]
    rows = {
        0: [[1, 0, 0], [0, 1, 0]],
        1: [[0, -1, width - 1], [1, 0, 0]],
        2: [[-1, 0, width - 1], [0, -1, height - 1]],
        3: [[0, 1, 0], [-1, 0, height - 1]],
    }[quarter % 4]
    return np.array([*rows, [0, 0, 1]], dtype=float)
