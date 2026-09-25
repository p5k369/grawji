"""Automatic perspective correction, ported from darktable's ashift.

Every idea in this module comes from the darktable project. The whole
automatic pipeline is a faithful Python port of their work, and the
credit for the method, the weighting, the vanishing-point search and
the crop fitting belongs to them, not to grawji. If you find this
feature useful, the people to thank are the darktable developers.

Ported from:

    src/iop/ashift.c
    Copyright (C) 2016-2026 darktable developers.
    Originally written by Ulrich Pegelow.

    src/iop/ashift_nmsimplex.c
    Copyright (C) 2016-2020 darktable developers.

    darktable is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published
    by the Free Software Foundation, either version 3 of the License,
    or (at your option) any later version.

    darktable is distributed in the hope that it will be useful, but
    WITHOUT ANY WARRANTY, without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

The Nelder-Mead optimizer inside ashift_nmsimplex.c is itself third
party code by Michael F. Hutt, under the MIT license. His notice is
reproduced above _simplex, where the port of his code lives.

The only substitution is the line source: segments come from lsdetect,
whose LSD provides about the same length, width and precision numbers
darktable's embedded LSD copy feeds into the weights.

Like darktable's default lens model, the fit assumes a 28 mm full-frame
focal length.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import lsdetect
import numpy as np
from numpy.typing import NDArray

# Constants as in darktable's ashift.c.
DEFAULT_F_LENGTH = 28.0
_MIN_LINE_LENGTH = 5.0
_MAX_TANGENTIAL_DEVIATION = 30.0
_ROTATION_RANGE = 10.0
_ROTATION_RANGE_SOFT = 180.0
_LENSSHIFT_RANGE = 1.0
_LENSSHIFT_RANGE_SOFT = 2.0
_SHEAR_RANGE = 0.2
_SHEAR_RANGE_SOFT = 0.5
_MINIMUM_FITLINES = 2
# Reject a fit whose lines still deviate more than this after warping,
# or that pegs a shift near its range.
_MAX_RESIDUAL_DEG = 1.5
_SHIFT_PEG = 0.95
# A trustworthy correction rests on a real line family.
_MIN_ACCEPT_LINES = 8
_RANSAC_RUNS = 400
_RANSAC_EPSILON = 2.0
_RANSAC_EPSILON_STEP = 1.0
# darktable tunes toward eliminating 60 percent. Its detection runs
# on small preview buffers with correspondingly few lines. lsdetect
# on our larger analysis frames yields much denser line sets, where
# 60 percent still leaves enough clutter to poison the fit, so the
# target is stricter here.
_RANSAC_ELIMINATION_RATIO = 85.0
_RANSAC_OPTIMIZATION_STEPS = 5
_RANSAC_OPTIMIZATION_DRY_RUNS = 50
_RANSAC_HURDLE = 5
_NMS_EPSILON = 1e-3
_NMS_SCALE = 1.0
_NMS_ITERATIONS = 400
_NMS_CROP_EPSILON = 100.0
_NMS_CROP_SCALE = 0.5
_NMS_CROP_ITERATIONS = 100
_NMS_ALPHA = 1.0
_NMS_BETA = 0.5
_NMS_GAMMA = 2.0

Mode = Literal["vertical", "horizontal", "both"]

_FloatArray = NDArray[np.float64]


class Segment(Protocol):
    """The slice of a detected segment the fit reads."""

    @property
    def x1(self) -> float:  # noqa: D102
        ...

    @property
    def y1(self) -> float:  # noqa: D102
        ...

    @property
    def x2(self) -> float:  # noqa: D102
        ...

    @property
    def y2(self) -> float:  # noqa: D102
        ...

    @property
    def width(self) -> float:  # noqa: D102
        ...

    @property
    def precision(self) -> float:  # noqa: D102
        ...

    @property
    def angle(self) -> float:  # noqa: D102
        ...

    @property
    def length(self) -> float:  # noqa: D102
        ...


@dataclass(frozen=True)
class Keystone:
    """A fitted correction: ashift's parameter set."""

    rotation: float = 0.0
    lensshift_v: float = 0.0
    lensshift_h: float = 0.0
    shear: float = 0.0

    @property
    def is_neutral(self) -> bool:
        """Whether this correction changes nothing."""
        eps = 1.0e-4
        return (
            abs(self.rotation) < eps
            and abs(self.lensshift_v) < eps
            and abs(self.lensshift_h) < eps
            and abs(self.shear) < eps
        )


@dataclass(frozen=True)
class FitReport:
    """A fitted correction plus how well the lines agree with it."""

    keystone: Keystone
    lines: int
    deviation_before: float
    deviation_after: float


def homography(keystone: Keystone, width: int, height: int) -> _FloatArray:
    """The forward homography for this image size (ashift).

    Port of _homography() with the generic-mode defaults (focal 28 mm
    full frame, orthocorr 0, aspect 1).
    """
    u, v = float(width), float(height)
    phi = math.radians(keystone.rotation)
    cosi, sini = math.cos(phi), math.sin(phi)
    shear = keystone.shear

    f_global = DEFAULT_F_LENGTH
    horifac = 1.0
    exppa_v = math.exp(keystone.lensshift_v)
    fdb_v = f_global / (14.4 + (v / u - 1.0) * 7.2)
    rad_v = fdb_v * (exppa_v - 1.0) / (exppa_v + 1.0)
    alpha_v = max(-1.5, min(1.5, math.atan(rad_v)))
    rt_v = math.sin(0.5 * alpha_v)
    r_v = max(0.1, 2.0 * (horifac - 1.0) * rt_v * rt_v + 1.0)

    vertifac = 1.0
    exppa_h = math.exp(keystone.lensshift_h)
    fdb_h = f_global / (14.4 + (u / v - 1.0) * 7.2)
    rad_h = fdb_h * (exppa_h - 1.0) / (exppa_h + 1.0)
    alpha_h = max(-1.5, min(1.5, math.atan(rad_h)))
    rt_h = math.sin(0.5 * alpha_h)
    r_h = max(0.1, 2.0 * (vertifac - 1.0) * rt_h * rt_h + 1.0)

    # Step 1: flip x and y.
    flip = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    out = flip

    # Step 2: rotation around the image center.
    rotate = np.array(
        [
            [cosi, -sini, -0.5 * v * cosi + 0.5 * u * sini + 0.5 * v],
            [sini, cosi, -0.5 * v * sini - 0.5 * u * cosi + 0.5 * u],
            [0.0, 0.0, 1.0],
        ]
    )
    out = rotate @ out

    # Step 3: shearing.
    shear_m = np.array([[1.0, shear, 0.0], [shear, 1.0, 0.0], [0.0, 0.0, 1.0]])
    out = shear_m @ out

    # Step 4: vertical lens shift.
    shift_v_m = np.array(
        [
            [exppa_v, 0.0, 0.0],
            [
                0.5 * ((exppa_v - 1.0) * u) / v,
                2.0 * exppa_v / (exppa_v + 1.0),
                -0.5 * ((exppa_v - 1.0) * u) / (exppa_v + 1.0),
            ],
            [(exppa_v - 1.0) / v, 0.0, 1.0],
        ]
    )
    out = shift_v_m @ out

    # Step 5: horizontal compression.
    compress_v = np.array(
        [[1.0, 0.0, 0.0], [0.0, r_v, 0.5 * u * (1.0 - r_v)], [0.0, 0.0, 1.0]]
    )
    out = compress_v @ out

    # Step 6: flip back.
    out = flip @ out

    # Step 7: horizontal lens shift, same matrix format.
    shift_h_m = np.array(
        [
            [exppa_h, 0.0, 0.0],
            [
                0.5 * ((exppa_h - 1.0) * v) / u,
                2.0 * exppa_h / (exppa_h + 1.0),
                -0.5 * ((exppa_h - 1.0) * v) / (exppa_h + 1.0),
            ],
            [(exppa_h - 1.0) / u, 0.0, 1.0],
        ]
    )
    out = shift_h_m @ out

    # Step 8: vertical compression.
    compress_h = np.array(
        [[1.0, 0.0, 0.0], [0.0, r_h, 0.5 * v * (1.0 - r_h)], [0.0, 0.0, 1.0]]
    )
    out = compress_h @ out

    # Step 9: aspect scaling is neutral at aspect 1.

    # Step 10: offset so no output coordinate goes negative.
    corners = np.array(
        [[0.0, 0.0], [u - 1.0, 0.0], [0.0, v - 1.0], [u - 1.0, v - 1.0]],
        dtype=float,
    )
    warped = _apply(out, corners)
    offset = np.array(
        [
            [1.0, 0.0, -float(warped[:, 0].min())],
            [0.0, 1.0, -float(warped[:, 1].min())],
            [0.0, 0.0, 1.0],
        ]
    )
    result: _FloatArray = offset @ out
    return result


def _apply(matrix: _FloatArray, points: _FloatArray) -> _FloatArray:
    """Send (x, y) points through a homography, plain coordinates."""
    homogeneous = np.column_stack([points, np.ones(len(points))])
    warped = homogeneous @ matrix.T
    return np.column_stack(
        [warped[:, 0] / warped[:, 2], warped[:, 1] / warped[:, 2]]
    )


def frame_transform(
    keystone: Keystone,
    width: int,
    height: int,
    matrix: _FloatArray | None = None,
) -> tuple[float, float, float]:
    """Scale and offset fitting the warped image back into its frame."""
    if matrix is None:
        matrix = homography(keystone, width, height)
    corners = np.array(
        [[0, 0], [width, 0], [0, height], [width, height]], dtype=float
    )
    warped = _apply(matrix, corners)
    scale = min(
        width / float(warped[:, 0].max() - warped[:, 0].min()),
        height / float(warped[:, 1].max() - warped[:, 1].min()),
    )
    return scale, float(warped[:, 0].min()), float(warped[:, 1].min())


def valid_quad(
    keystone: Keystone,
    width: int,
    height: int,
    angle: float = 0.0,
) -> _FloatArray:
    """The warped image outline on the rotated canvas."""
    matrix = homography(keystone, width, height)
    scale, off_x, off_y = frame_transform(keystone, width, height, matrix)
    corners = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]], dtype=float
    )
    quad = _apply(matrix, corners)
    quad[:, 0] = (quad[:, 0] - off_x) * scale
    quad[:, 1] = (quad[:, 1] - off_y) * scale
    radians = math.radians(angle)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    dx = quad[:, 0] - width / 2.0
    dy = quad[:, 1] - height / 2.0
    canvas_w = abs(width * cos_a) + abs(height * sin_a)
    canvas_h = abs(width * sin_a) + abs(height * cos_a)
    quad[:, 0] = (cos_a * dx - sin_a * dy + canvas_w / 2.0) / canvas_w
    quad[:, 1] = (sin_a * dx + cos_a * dy + canvas_h / 2.0) / canvas_h
    return quad


def rotate_quad(quad: _FloatArray, degrees: int) -> _FloatArray:
    """Carry a normalized quad through a 90-degree image rotation."""
    step = degrees % 360
    if step == 90:  # noqa: PLR2004
        return np.column_stack([1.0 - quad[:, 1], quad[:, 0]])
    if step == 180:  # noqa: PLR2004
        return np.column_stack([1.0 - quad[:, 0], 1.0 - quad[:, 1]])
    if step == 270:  # noqa: PLR2004
        return np.column_stack([quad[:, 1], 1.0 - quad[:, 0]])
    return quad


def rect_in_quad(
    rect: tuple[float, float, float, float], quad: _FloatArray
) -> bool:
    """Whether every corner of a rect lies inside the convex quad."""
    x, y, w, h = rect
    corners = np.array(
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=float
    )
    tolerance = -1e-9
    for i in range(4):
        a = quad[i]
        b = quad[(i + 1) % 4]
        edge = b - a
        cross = edge[0] * (corners[:, 1] - a[1]) - edge[1] * (
            corners[:, 0] - a[0]
        )
        if bool((cross < tolerance).any()):
            return False
    return True


def slide_into_quad(
    rect: tuple[float, float, float, float], quad: _FloatArray
) -> tuple[float, float, float, float]:
    """Translate a rect the shortest way to sit inside the convex quad."""
    if rect_in_quad(rect, quad):
        return rect
    x, y, w, h = rect
    corners = np.array(
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=float
    )
    planes = []
    for i in range(4):
        a = quad[i]
        edge = quad[(i + 1) % 4] - a
        grad = np.array([-edge[1], edge[0]])
        cross = edge[0] * (corners[:, 1] - a[1]) - edge[1] * (
            corners[:, 0] - a[0]
        )
        planes.append((grad, float(cross.min())))
    shift = np.zeros(2)
    tolerance = -1e-12
    for _ in range(60):
        moved = False
        for grad, margin in planes:
            slack = float(grad @ shift) + margin
            if slack < tolerance:
                length = float(grad @ grad)
                if length > 0.0:
                    shift = shift - grad * (slack / length)
                    moved = True
        if not moved:
            break
    return (x + float(shift[0]), y + float(shift[1]), w, h)


def auto_crop(  # noqa: PLR0915
    keystone: Keystone, width: int, height: int
) -> tuple[float, float, float, float]:
    """Largest inner crop free of the warp's empty area (darktable).

    A faithful port of ashift's do_crop/crop_fitness: the rectangle
    center is searched in input-image coordinates (so it is always
    inside the image after warping) and grown to the largest area at a
    free aspect angle bounded by the four output edges.
    """
    if keystone.is_neutral:
        return (0.0, 0.0, 1.0, 1.0)
    matrix = homography(keystone, width, height)
    wd, ht = float(width), float(height)
    vertices_in = np.array(
        [[0.0, 0.0], [0.0, ht], [wd, ht], [wd, 0.0]], dtype=float
    )
    vertices = _apply(matrix, vertices_in)
    homo = np.column_stack([vertices, np.ones(4)])
    edges = [np.cross(homo[n], homo[(n + 1) % 4]) for n in range(4)]

    def fitness(params: Sequence[float]) -> float:
        x, y, alpha = params
        centre = matrix @ np.array([x * wd, y * ht, 1.0])
        centre = centre / centre[2]
        d2min = math.inf
        for edge in edges:
            for sign in (1.0, -1.0):
                aux = np.array(
                    [
                        centre[0] + 10.0 * math.cos(alpha),
                        centre[1] + sign * 10.0 * math.sin(alpha),
                        1.0,
                    ]
                )
                diag = np.cross(centre, aux)
                point = np.cross(edge, diag)
                parallel_eps = 1e-12
                if abs(point[2]) < parallel_eps:
                    continue
                px, py = point[0] / point[2], point[1] / point[2]
                d2 = (centre[0] - px) ** 2 + (centre[1] - py) ** 2
                d2min = min(d2min, d2)
        area = 2.0 * d2min * math.sin(2.0 * alpha)
        return -area

    def constrain(params: list[float]) -> None:
        params[0] = abs(params[0])
        params[1] = abs(params[1])
        params[2] = abs(params[2])
        if params[0] > 1.0:
            params[0] = 1.0 - params[0]
        if params[1] > 1.0:
            params[1] = 1.0 - params[1]
        if params[2] > 0.5 * math.pi:
            params[2] = 0.5 * math.pi - params[2]

    start = [0.5, 0.5, math.atan2(ht, wd)]
    params, iters = _simplex(
        fitness,
        start,
        _NMS_CROP_EPSILON,
        _NMS_CROP_SCALE,
        _NMS_CROP_ITERATIONS,
        constrain,
    )
    if iters >= _NMS_CROP_ITERATIONS:
        return (0.0, 0.0, 1.0, 1.0)
    area = abs(fitness(params))
    if area == 0.0:
        return (0.0, 0.0, 1.0, 1.0)
    x, y, alpha = params
    d = math.sqrt(area / (2.0 * math.sin(2.0 * alpha)))
    centre = matrix @ np.array([x * wd, y * ht, 1.0])
    centre = centre / centre[2]
    scale, off_x, off_y = frame_transform(keystone, width, height, matrix)
    # Output margins (darktable) map into the warp output: subtract the
    # frame offset and apply the fit scale, then normalize by size.
    cl = (centre[0] - d * math.cos(alpha) - off_x) * scale / width
    cr = (centre[0] + d * math.cos(alpha) - off_x) * scale / width
    ct = (centre[1] - d * math.sin(alpha) - off_y) * scale / height
    cb = (centre[1] + d * math.sin(alpha) - off_y) * scale / height
    cl = min(max(cl, 0.0), 1.0)
    cr = min(max(cr, 0.0), 1.0)
    ct = min(max(ct, 0.0), 1.0)
    cb = min(max(cb, 0.0), 1.0)
    if cr - cl <= 0.0 or cb - ct <= 0.0:
        return (0.0, 0.0, 1.0, 1.0)
    return (cl, ct, cr - cl, cb - ct)


@dataclass
class _Lines:
    """The detected lines in ashift's working form."""

    coeffs: _FloatArray  # (n, 3), normalized so x^2 + y^2 = 1
    ends: _FloatArray  # (n, 2, 2) endpoint coordinates
    weights: _FloatArray
    vertical: NDArray[np.bool_]
    selected: NDArray[np.bool_]


def _lines_of(segments: Sequence[Segment]) -> _Lines | None:
    """Classify and weight the detected segments, ashift-style."""
    coeffs = []
    ends = []
    weights = []
    vertical = []
    for segment in segments:
        if segment.length <= _MIN_LINE_LENGTH:
            continue
        angle = segment.angle
        is_vertical = abs(abs(angle) - 90.0) < _MAX_TANGENTIAL_DEVIATION
        is_horizontal = (
            abs(abs(abs(angle) - 90.0) - 90.0) < _MAX_TANGENTIAL_DEVIATION
        )
        if not (is_vertical or is_horizontal):
            continue
        p1 = np.array([segment.x1, segment.y1, 1.0])
        p2 = np.array([segment.x2, segment.y2, 1.0])
        line = np.cross(p1, p2)
        norm = math.hypot(line[0], line[1])
        if norm == 0.0:
            continue
        coeffs.append(line / norm)
        ends.append([[segment.x1, segment.y1], [segment.x2, segment.y2]])
        # Weight = length * width * precision, as in ashift.
        weights.append(segment.length * segment.width * segment.precision)
        vertical.append(is_vertical)
    if not coeffs:
        return None
    return _Lines(
        coeffs=np.array(coeffs),
        ends=np.array(ends),
        weights=np.array(weights),
        vertical=np.array(vertical, dtype=bool),
        selected=np.ones(len(coeffs), dtype=bool),
    )


def _ransac(  # noqa: PLR0915
    coeffs: _FloatArray,
    weights: _FloatArray,
    index_set: list[int],
    total_weight: float,
    bounds: tuple[float, float, float, float],
    rng: np.random.Generator,
) -> NDArray[np.bool_]:
    """The vanishing-point RANSAC over one line family (darktable)."""
    xmin, xmax, ymin, ymax = bounds
    set_count = len(index_set)
    idx = np.asarray(index_set)
    fam_coeffs = coeffs[idx]  # (m, 3)
    fam_weights = weights[idx]
    shares = fam_weights / total_weight
    best_inout = np.zeros(set_count, dtype=bool)
    best_quality = 0.0

    epsilon = 10.0**-_RANSAC_EPSILON
    epsilon_step = _RANSAC_EPSILON_STEP
    lines_eliminated = 0
    valid_runs = 0

    optiruns = _RANSAC_OPTIMIZATION_STEPS * _RANSAC_OPTIMIZATION_DRY_RUNS
    if set_count > _RANSAC_HURDLE:
        riter = _RANSAC_RUNS
        permutations = None
    else:
        permutations = itertools.cycle(
            itertools.permutations(range(set_count))
        )
        riter = math.factorial(set_count)

    probabilities = shares / shares.sum()
    for run in range(optiruns + riter):
        if permutations is None or run < optiruns:
            pair = rng.choice(
                set_count, size=2, replace=False, p=probabilities
            )
            a, b = int(pair[0]), int(pair[1])
        else:
            order = next(permutations)
            a, b = order[0], order[1]

        vantage = np.cross(fam_coeffs[a], fam_coeffs[b])
        inside = (
            abs(vantage[2]) > 0.0
            and xmin <= vantage[0] / vantage[2] <= xmax
            and ymin <= vantage[1] / vantage[2] <= ymax
        )
        if np.allclose(vantage, 0.0) or inside:
            continue
        vantage = vantage / np.linalg.norm(vantage)

        # Distance of every family line to the vantage point at once.
        distances = np.abs(fam_coeffs @ vantage)
        inout = distances < epsilon
        inout[a] = inout[b] = True

        if run < optiruns:
            lines_eliminated += int((~inout).sum())
            valid_runs += 1
            at_step_end = (
                run % _RANSAC_OPTIMIZATION_DRY_RUNS
                == _RANSAC_OPTIMIZATION_DRY_RUNS - 1
            )
            if at_step_end and valid_runs > 0:
                ratio = 100.0 * lines_eliminated / (set_count * valid_runs)
                if ratio < _RANSAC_ELIMINATION_RATIO:
                    epsilon = 10.0 ** (math.log10(epsilon) - epsilon_step)
                elif ratio > _RANSAC_ELIMINATION_RATIO:
                    epsilon = 10.0 ** (math.log10(epsilon) + epsilon_step)
                epsilon_step /= 2.0
                lines_eliminated = 0
                valid_runs = 0
            continue

        # Real run: darktable's three-part quality, vectorized.
        contrib = (
            0.33 / set_count
            + 0.33 * shares
            + 0.33 * (1.0 - distances / epsilon) * set_count * shares
        )
        quality = float(contrib[inout].sum())
        if quality > best_quality:
            best_quality = quality
            best_inout = inout.copy()

    return best_inout


def _remove_outliers(
    lines: _Lines, width: int, height: int, rng: np.random.Generator
) -> None:
    """Run the vanishing-point RANSAC per family, as ashift does."""
    bounds = (0.0, float(width), 0.0, float(height))
    for family in (True, False):
        members = np.nonzero(lines.vertical == family)[0]
        pair_minimum = 2
        if len(members) <= pair_minimum:
            continue
        total = float(lines.weights[members].sum())
        flags = _ransac(
            lines.coeffs,
            lines.weights,
            [int(m) for m in members],
            total,
            bounds,
            rng,
        )
        lines.selected[members] = flags


@dataclass
class _FitState:
    """Everything model_fitness needs, mirroring ashift's fit struct.

    Parameters not being fitted keep their base values, ashift's
    NaN-marking translated into flags plus a base keystone.
    """

    lines: _Lines
    width: int
    height: int
    use_vertical: bool
    use_horizontal: bool
    fit_rotation: bool
    fit_lens_v: bool
    fit_lens_h: bool
    fit_shear: bool
    base: Keystone


def _logit(x: float, low: float, high: float) -> float:
    """Logit reparameterization onto an open interval (ashift)."""
    eps = 1.0e-6
    p = min(max((x - low) / (high - low), eps), 1.0 - eps)
    return 2.0 * math.atanh(2.0 * p - 1.0)


def _ilogit(value: float, low: float, high: float) -> float:
    """Inverse of _logit."""
    p = 0.5 * (1.0 + math.tanh(0.5 * value))
    return p * (high - low) + low


def _decode(params: Sequence[float], state: _FitState) -> Keystone:
    """The parameter vector as a Keystone, order as in ashift."""
    values = list(params)
    rotation = state.base.rotation
    lens_v = state.base.lensshift_v
    lens_h = state.base.lensshift_h
    shear = state.base.shear
    if state.fit_rotation:
        rotation = _ilogit(values.pop(0), -_ROTATION_RANGE, _ROTATION_RANGE)
    if state.fit_lens_v:
        lens_v = _ilogit(values.pop(0), -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
    if state.fit_lens_h:
        lens_h = _ilogit(values.pop(0), -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
    if state.fit_shear:
        shear = _ilogit(values.pop(0), -_SHEAR_RANGE, _SHEAR_RANGE)
    return Keystone(rotation, lens_v, lens_h, shear)


def _model_fitness(params: Sequence[float], state: _FitState) -> float:
    """The fit objective: squared axis deviations of the lines (ashift)."""
    keystone = _decode(params, state)
    matrix = homography(keystone, state.width, state.height)
    lines = state.lines
    used = lines.selected & (
        (lines.vertical & state.use_vertical)
        | (~lines.vertical & state.use_horizontal)
    )
    if not bool(used.any()):
        return float("inf")
    ends = lines.ends[used]
    flat = ends.reshape(-1, 2)
    warped = _apply(matrix, flat).reshape(-1, 2, 2)
    p1 = np.column_stack([warped[:, 0], np.ones(len(warped))])
    p2 = np.column_stack([warped[:, 1], np.ones(len(warped))])
    moved = np.cross(p1, p2)
    norm = np.hypot(moved[:, 0], moved[:, 1])
    norm[norm == 0.0] = 1.0
    moved /= norm[:, None]
    vertical = lines.vertical[used]
    weights = lines.weights[used]
    s = np.where(vertical, moved[:, 1], moved[:, 0])
    count = len(s)
    sumsq_v = float((s[vertical] ** 2 * weights[vertical]).sum())
    weight_v = float(weights[vertical].sum())
    count_v = int(vertical.sum())
    sumsq_h = float((s[~vertical] ** 2 * weights[~vertical]).sum())
    weight_h = float(weights[~vertical].sum())
    count_h = count - count_v
    v = sumsq_v / weight_v * count_v / count if weight_v > 0.0 else 0.0
    h = sumsq_h / weight_h * count_h / count if weight_h > 0.0 else 0.0
    return math.sqrt(1.0 - (1.0 - v) * (1.0 - h)) * 1.0e6


# The Nelder-Mead simplex below is third party code. darktable took it
# from Michael F. Hutt for ashift_nmsimplex.c, and grawji ports it from
# there. His license requires that this notice travel with the code,
# and it is reproduced in full below, unaltered. The mikehutt.com it
# names no longer resolves. His own copy of nmsimplex.c is on GitHub
# at https://github.com/huttmf/nelder-mead, same code, same license.
#
#   Program: nmsimplex.c
#   Author : Michael F. Hutt
#   http://www.mikehutt.com
#   11/3/97
#
#   An implementation of the Nelder-Mead simplex method.
#
#   Copyright (c) 1997-2011 <Michael F. Hutt>
#
#   Permission is hereby granted, free of charge, to any person
#   obtaining a copy of this software and associated documentation
#   files (the "Software"), to deal in the Software without
#   restriction, including without limitation the rights to use, copy,
#   modify, merge, publish, distribute, sublicense, and/or sell copies
#   of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be
#   included in all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
#   EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
#   NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
#   BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
#   ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
#   CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.
def _simplex(  # noqa: PLR0912, PLR0915
    objfunc: Callable[[list[float]], float],
    start: list[float],
    epsilon: float = _NMS_EPSILON,
    scale: float = _NMS_SCALE,
    iters: int = _NMS_ITERATIONS,
    constrain: Callable[[list[float]], None] | None = None,
) -> tuple[list[float], int]:
    """Nelder-Mead, ported from darktable's ashift_nmsimplex.c."""
    n = len(start)
    pn = scale * (math.sqrt(n + 1) - 1 + n) / (n * math.sqrt(2))
    qn = scale * (math.sqrt(n + 1) - 1) / (n * math.sqrt(2))
    vertices = [list(start)]
    for i in range(n):
        vertex = [start[j] + (pn if i == j else qn) for j in range(n)]
        vertices.append(vertex)
    if constrain is not None:
        for vertex in vertices:
            constrain(vertex)
    values = [objfunc(v) for v in vertices]

    iterations = 0
    for step in range(1, iters + 1):
        iterations = step
        worst = max(range(n + 1), key=lambda j: values[j])
        best = min(range(n + 1), key=lambda j: values[j])
        second_worst = best
        for j in range(n + 1):
            if values[second_worst] < values[j] < values[worst]:
                second_worst = j
        centroid = [
            sum(vertices[m][j] for m in range(n + 1) if m != worst) / n
            for j in range(n)
        ]
        reflected = [
            centroid[j] + _NMS_ALPHA * (centroid[j] - vertices[worst][j])
            for j in range(n)
        ]
        if constrain is not None:
            constrain(reflected)
        f_reflected = objfunc(reflected)
        if values[best] <= f_reflected < values[second_worst]:
            vertices[worst] = reflected
            values[worst] = f_reflected
        if f_reflected < values[best]:
            expanded = [
                centroid[j] + _NMS_GAMMA * (reflected[j] - centroid[j])
                for j in range(n)
            ]
            if constrain is not None:
                constrain(expanded)
            f_expanded = objfunc(expanded)
            if f_expanded < f_reflected:
                vertices[worst] = expanded
                values[worst] = f_expanded
            else:
                vertices[worst] = reflected
                values[worst] = f_reflected
        if f_reflected >= values[second_worst]:
            if f_reflected < values[worst]:
                contracted = [
                    centroid[j] + _NMS_BETA * (reflected[j] - centroid[j])
                    for j in range(n)
                ]
            else:
                contracted = [
                    centroid[j]
                    - _NMS_BETA * (centroid[j] - vertices[worst][j])
                    for j in range(n)
                ]
            if constrain is not None:
                constrain(contracted)
            f_contracted = objfunc(contracted)
            if f_contracted < values[worst]:
                vertices[worst] = contracted
                values[worst] = f_contracted
            else:
                for row in range(n + 1):
                    if row != best:
                        vertices[row] = [
                            vertices[best][j]
                            + (vertices[row][j] - vertices[best][j]) / 2.0
                            for j in range(n)
                        ]
                worst = max(range(n + 1), key=lambda j: values[j])
                second = second_worst
                values[worst] = objfunc(vertices[worst])
                values[second] = objfunc(vertices[second])
        average = sum(values) / (n + 1)
        spread = math.sqrt(sum((value - average) ** 2 for value in values) / n)
        if spread < epsilon:
            break
    best = min(range(n + 1), key=lambda j: values[j])
    return vertices[best], iterations


def analyze(
    gray: NDArray[np.float64],
    mode: Mode = "both",
) -> FitReport | None:
    """Detect line segments in a grayscale frame and fit them."""
    segments = lsdetect.detect(np.ascontiguousarray(gray))
    height, width = gray.shape
    return fit(segments, width, height, mode)


def fit(
    segments: Sequence[Segment],
    width: int,
    height: int,
    mode: Mode = "both",
) -> FitReport | None:
    """The automatic fit over the detected segments (darktable)."""
    lines = _lines_of(segments)
    if lines is None:
        return None
    rng = np.random.default_rng(0)
    _remove_outliers(lines, width, height, rng)
    vertical_count = int((lines.selected & lines.vertical).sum())
    horizontal_count = int((lines.selected & ~lines.vertical).sum())

    attempts: list[Mode] = (
        ["both", "vertical", "horizontal"] if mode == "both" else [mode]
    )
    for attempt in attempts:
        use_vertical = attempt in ("vertical", "both")
        use_horizontal = attempt in ("horizontal", "both")
        if use_vertical and vertical_count < _MINIMUM_FITLINES:
            continue
        if use_horizontal and horizontal_count < _MINIMUM_FITLINES:
            continue
        staged = _staged_start(
            lines,
            width,
            height,
            use_vertical=use_vertical,
            use_horizontal=use_horizontal,
        )
        state = _FitState(
            lines=lines,
            width=width,
            height=height,
            use_vertical=use_vertical,
            use_horizontal=use_horizontal,
            fit_rotation=True,
            fit_lens_v=use_vertical,
            fit_lens_h=use_horizontal,
            fit_shear=False,
            base=staged,
        )
        start = [_logit(staged.rotation, -_ROTATION_RANGE, _ROTATION_RANGE)]
        if state.fit_lens_v:
            start.append(
                _logit(staged.lensshift_v, -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
            )
        if state.fit_lens_h:
            start.append(
                _logit(staged.lensshift_h, -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
            )

        def objective(v: list[float], st: _FitState = state) -> float:
            return _model_fitness(v, st)

        params, iterations = _simplex(objective, start)
        if iterations >= _NMS_ITERATIONS:
            continue
        fitted = _decode(params, state)
        used = lines.selected & (
            (lines.vertical & use_vertical)
            | (~lines.vertical & use_horizontal)
        )
        after = _mean_deviation(fitted, lines, used, width, height)
        pegged = (
            abs(fitted.lensshift_v) > _SHIFT_PEG * _LENSSHIFT_RANGE
            or abs(fitted.lensshift_h) > _SHIFT_PEG * _LENSSHIFT_RANGE
        )
        if (
            int(used.sum()) < _MIN_ACCEPT_LINES
            or after > _MAX_RESIDUAL_DEG
            or pegged
        ):
            continue
        return FitReport(
            keystone=fitted,
            lines=int(used.sum()),
            deviation_before=_mean_deviation(
                Keystone(), lines, used, width, height
            ),
            deviation_after=after,
        )
    return None


def _staged_start(
    lines: _Lines,
    width: int,
    height: int,
    *,
    use_vertical: bool,
    use_horizontal: bool,
) -> Keystone:
    """A robust starting point: rotation first, then each shift alone."""

    def stage(
        base: Keystone,
        *,
        fit_rotation: bool,
        fit_lens_v: bool,
        fit_lens_h: bool,
        vertical: bool,
        horizontal: bool,
    ) -> Keystone:
        state = _FitState(
            lines=lines,
            width=width,
            height=height,
            use_vertical=vertical,
            use_horizontal=horizontal,
            fit_rotation=fit_rotation,
            fit_lens_v=fit_lens_v,
            fit_lens_h=fit_lens_h,
            fit_shear=False,
            base=base,
        )
        start = []
        if fit_rotation:
            start.append(
                _logit(base.rotation, -_ROTATION_RANGE, _ROTATION_RANGE)
            )
        if fit_lens_v:
            start.append(
                _logit(base.lensshift_v, -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
            )
        if fit_lens_h:
            start.append(
                _logit(base.lensshift_h, -_LENSSHIFT_RANGE, _LENSSHIFT_RANGE)
            )

        def objective(v: list[float], st: _FitState = state) -> float:
            return _model_fitness(v, st)

        params, _iterations = _simplex(objective, start)
        return _decode(params, state)

    base = stage(
        Keystone(),
        fit_rotation=True,
        fit_lens_v=False,
        fit_lens_h=False,
        vertical=use_vertical,
        horizontal=use_horizontal,
    )
    if use_vertical:
        base = stage(
            base,
            fit_rotation=False,
            fit_lens_v=True,
            fit_lens_h=False,
            vertical=True,
            horizontal=False,
        )
    if use_horizontal:
        base = stage(
            base,
            fit_rotation=False,
            fit_lens_v=False,
            fit_lens_h=True,
            vertical=False,
            horizontal=True,
        )
    return base


def _mean_deviation(
    keystone: Keystone,
    lines: _Lines,
    used: NDArray[np.bool_],
    width: int,
    height: int,
) -> float:
    """Weighted mean off-axis angle of the used lines."""
    matrix = homography(keystone, width, height)
    ends = lines.ends[used]
    warped = _apply(matrix, ends.reshape(-1, 2)).reshape(-1, 2, 2)
    delta = warped[:, 1] - warped[:, 0]
    off_vertical = np.degrees(
        np.arctan2(np.abs(delta[:, 0]), np.abs(delta[:, 1]))
    )
    off_horizontal = np.degrees(
        np.arctan2(np.abs(delta[:, 1]), np.abs(delta[:, 0]))
    )
    deviation = np.where(lines.vertical[used], off_vertical, off_horizontal)
    return float(np.average(deviation, weights=lines.weights[used]))
