"""A Gaussian blur for small float images."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


def gaussian(image: NDArray[np.float64], sigma: float) -> NDArray[np.float64]:
    """Blur the first two axes by sigma pixels, holding the edges."""
    radius = math.ceil(3 * sigma)
    taps = np.exp(-(np.arange(-radius, radius + 1) ** 2) / (2 * sigma**2))
    taps /= taps.sum()
    out = np.asarray(image, dtype=np.float64)
    for axis in (0, 1):
        padding = [(0, 0)] * out.ndim
        padding[axis] = (radius, radius)
        padded = np.pad(out, padding, mode="edge")
        length = out.shape[axis]
        out = sum(
            (
                tap * np.take(padded, np.arange(i, i + length), axis=axis)
                for i, tap in enumerate(taps)
            ),
            np.zeros_like(out),
        )
    return out
