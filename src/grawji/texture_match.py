"""Compare an edit's texture, its fine detail and noise, with a render."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lsdetect
import numpy as np

from grawji.imaging.blur import gaussian
from grawji.imaging.pixbufs import area_resize

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from grawji.edit_align import Alignment

# The edge of one compared window.
WINDOW = 192
# How many windows are compared.
_WINDOWS = 8
# The bands.
_FINE_SIGMA = 1.0
_COARSE_SIGMA = 3.0
# Rows and columns at a window's rim the blur holds, left out.
_RIM = 3 * math.ceil(_COARSE_SIGMA)
# A window must map this far inside the frame.
_INSIDE = 16
# Flat windows are picked away from clipped areas.
_FLAT_LIGHTNESS = (0.08, 0.92)
# Keeps the log ratio finite on a noiseless window.
_EPS = 1e-3
# Below this many edit pixels per frame pixel the render is shrunk by a
# whole factor before the warp, which would otherwise skip pixels.
_SHRINK_BELOW = 0.5
# A corner this close to the horizon of the warp is off the frame.
_HORIZON = 1e-9
# Window detail is judged on the edit shrunk this much.
_DETAIL_SHRINK = 4
_LUMA = np.array([0.299, 0.587, 0.114])


@dataclass(frozen=True)
class Window:
    """One compared window."""

    x: int
    y: int
    fine: float
    coarse: float


def band_energies(gray: NDArray[np.float64]) -> tuple[float, float]:
    """The RMS energy of the fine and the coarse band of a window."""
    fine = gaussian(gray, _FINE_SIGMA)
    coarse = gaussian(gray, _COARSE_SIGMA)
    inner = (slice(_RIM, -_RIM), slice(_RIM, -_RIM))
    high = gray[inner] - fine[inner]
    mid = fine[inner] - coarse[inner]
    return (
        float(np.sqrt((high**2).mean())),
        float(np.sqrt((mid**2).mean())),
    )


def _gray(rgb: NDArray[np.uint8]) -> NDArray[np.float64]:
    """RGB bytes to gray."""
    return np.asarray(rgb[..., :3].astype(np.float64) @ _LUMA / 255.0)


def _log_gap(seen: float, wanted: float) -> float:
    """How far apart two energies are."""
    return abs(math.log((seen + _EPS) / (wanted + _EPS)))


class TextureTarget:
    """An edit's texture, measured where it tells the most."""

    def __init__(
        self,
        edit: NDArray[np.uint8],
        alignment: Alignment,
        aligned_edit_size: tuple[int, int],
        aligned_frame_size: tuple[int, int],
    ) -> None:
        """Keep the edit until the first render says what scale to use.

        Args:
            edit: The edit at full size.
            alignment: Where the edit sits in the frame, found at the
                aligned sizes.
            aligned_edit_size: Width and height the edit had for the
                alignment.
            aligned_frame_size: Width and height the frame had for it.
        """
        self._edit: NDArray[np.uint8] | None = edit
        self._edit_size = (edit.shape[1], edit.shape[0])
        self._alignment = alignment
        self._aligned_edit = aligned_edit_size
        self._aligned_frame = aligned_frame_size
        self._frame_size: tuple[int, int] | None = None
        self._shrink = 1
        self._matrix = np.eye(3)
        self._windows: list[Window] = []

    @property
    def windows(self) -> list[Window]:
        """The compared windows, empty before the first render."""
        return list(self._windows)

    def compare(self, render: NDArray[np.uint8]) -> float:
        """How far the render's texture is from the edit's."""
        height, width = render.shape[:2]
        if self._frame_size is None:
            self._prepare((width, height))
        elif self._frame_size != (width, height):
            msg = "render size differs from the first render"
            raise ValueError(msg)
        if not self._windows:
            return 0.0
        source = render[..., :3]
        if self._shrink > 1:
            source = area_resize(
                source, width // self._shrink, height // self._shrink
            )
        source = np.ascontiguousarray(source)
        gaps = []
        for window in self._windows:
            fine, coarse = band_energies(_gray(self._seen(source, window)))
            gaps.append(_log_gap(fine, window.fine))
            gaps.append(_log_gap(coarse, window.coarse))
        return float(np.mean(gaps))

    def _seen(
        self, source: NDArray[np.uint8], window: Window
    ) -> NDArray[np.uint8]:
        """The render's pixels under one window of the edit."""
        shift = np.eye(3)
        shift[0, 2] = -window.x
        shift[1, 2] = -window.y
        inverse = np.linalg.inv(shift @ self._matrix)
        a, b, c, d, e, f, g, h, i = (float(v) for v in inverse.ravel())
        return np.asarray(
            lsdetect.warp_rgb(
                source,
                (a, b, c, d, e, f, g, h, i),
                1.0,
                0.0,
                0.0,
                filter="lanczos3",
                out_size=(WINDOW, WINDOW),
            )
        )

    def _prepare(self, frame_size: tuple[int, int]) -> None:
        """Settle the scale, pick the windows and measure the edit."""
        self._frame_size = frame_size
        edit = self._edit
        if edit is None:
            msg = "the edit was released"
            raise ValueError(msg)
        matrix = self._full_matrix(frame_size)
        linear = matrix[:2, :2]
        per_frame_pixel = math.sqrt(abs(float(np.linalg.det(linear))))
        if per_frame_pixel > 1.0:
            width = max(1, round(self._edit_size[0] / per_frame_pixel))
            height = max(1, round(self._edit_size[1] / per_frame_pixel))
            edit = area_resize(edit[..., :3], width, height)
            matrix = (
                _scaling(
                    width / self._edit_size[0], height / self._edit_size[1]
                )
                @ matrix
            )
        elif per_frame_pixel < _SHRINK_BELOW:
            self._shrink = int(1.0 / per_frame_pixel)
            matrix = matrix @ _scaling(self._shrink, self._shrink)
        self._matrix = matrix
        self._windows = self._pick(edit, matrix)
        self._edit = None

    def _full_matrix(self, frame_size: tuple[int, int]) -> NDArray[np.float64]:
        """The alignment between the full frame and the full edit."""
        edit_scale = _scaling(
            self._edit_size[0] / self._aligned_edit[0],
            self._edit_size[1] / self._aligned_edit[1],
        )
        frame_scale = _scaling(
            self._aligned_frame[0] / frame_size[0],
            self._aligned_frame[1] / frame_size[1],
        )
        return np.asarray(edit_scale @ self._alignment.matrix @ frame_scale)

    def _pick(
        self, edit: NDArray[np.uint8], matrix: NDArray[np.float64]
    ) -> list[Window]:
        """The busiest and the flattest windows the frame covers."""
        height, width = edit.shape[:2]
        columns, rows = width // WINDOW, height // WINDOW
        if not columns or not rows or self._frame_size is None:
            return []
        inverse = np.linalg.inv(matrix)
        small = _gray(
            area_resize(
                edit[: rows * WINDOW, : columns * WINDOW],
                columns * WINDOW // _DETAIL_SHRINK,
                rows * WINDOW // _DETAIL_SHRINK,
            )
        )
        tile = WINDOW // _DETAIL_SHRINK
        candidates: list[tuple[float, float, int, int]] = []
        for row in range(rows):
            for column in range(columns):
                x, y = column * WINDOW, row * WINDOW
                if not self._covered(inverse, x, y):
                    continue
                patch = small[
                    row * tile : (row + 1) * tile,
                    column * tile : (column + 1) * tile,
                ]
                detail = float(
                    np.hypot(
                        np.diff(patch, axis=0)[:, :-1],
                        np.diff(patch, axis=1)[:-1],
                    ).mean()
                )
                candidates.append((detail, float(patch.mean()), x, y))
        if not candidates:
            return []
        candidates.sort(reverse=True)
        half = _WINDOWS // 2
        busy = candidates[:half]
        low, high = _FLAT_LIGHTNESS
        flat = [
            candidate
            for candidate in reversed(candidates[half:])
            if low <= candidate[1] <= high
        ][:half]
        return [self._measured(edit, x, y) for _d, _m, x, y in [*busy, *flat]]

    def _covered(self, inverse: NDArray[np.float64], x: int, y: int) -> bool:
        """Whether a window maps well inside the frame."""
        if self._frame_size is None:
            return False
        width, height = self._frame_size
        if self._shrink > 1:
            width, height = width // self._shrink, height // self._shrink
        corners = np.array(
            [
                [x, y, 1.0],
                [x + WINDOW, y, 1.0],
                [x, y + WINDOW, 1.0],
                [x + WINDOW, y + WINDOW, 1.0],
            ]
        )
        mapped = corners @ inverse.T
        if np.any(np.abs(mapped[:, 2]) < _HORIZON):
            return False
        points = mapped[:, :2] / mapped[:, 2:3]
        return bool(
            np.all(points[:, 0] >= _INSIDE)
            and np.all(points[:, 0] <= width - _INSIDE)
            and np.all(points[:, 1] >= _INSIDE)
            and np.all(points[:, 1] <= height - _INSIDE)
        )

    @staticmethod
    def _measured(edit: NDArray[np.uint8], x: int, y: int) -> Window:
        """A window with the edit's band energies inside it."""
        fine, coarse = band_energies(
            _gray(edit[y : y + WINDOW, x : x + WINDOW])
        )
        return Window(x, y, fine, coarse)


def _scaling(sx: float, sy: float) -> NDArray[np.float64]:
    """A homography that scales each axis."""
    return np.diag([sx, sy, 1.0])
