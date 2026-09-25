"""Auto-level suggestions from detected line segments.

lsdetect finds the image's line segments. Every segment close enough
to an axis votes for the rotation that would make it level (or
plumb), darktable-style. Votes cluster into candidates the editor can
cycle through, ranked by how much line weight agrees and how small
the correction is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import lsdetect
import numpy as np

from grawji.crop import level_delta

if TYPE_CHECKING:
    from numpy.typing import NDArray

# A line further off both axes than this is no leveling target.
DEV_LIMIT = 12.0
# Segments whose suggested corrections lie this close together are
# one physical line family.
_CLUSTER_TOL = 1.0
# Softly prefer small corrections: a family needing a large turn must
# bring correspondingly more line weight.
_TILT_PRIOR = 3.0
# Families carrying less than this share of the total weight are
# noise, not a suggestion.
_MIN_SHARE = 0.10
_MAX_CANDIDATES = 3
# How many of a family's lines the editor marks on screen.
_MARK_SEGMENTS = 12
# Segments shorter than this share of the longer image edge cannot
# define a horizon or a plumb line.
_MIN_LENGTH_SHARE = 0.04
# Anything smaller holds no lines worth leveling to.
_MIN_SIZE = 16

Endpoints = tuple[float, float, float, float]


@dataclass(frozen=True)
class Candidate:
    """One leveling suggestion: the angle plus the lines behind it."""

    delta: float
    segments: tuple[Endpoints, ...]


@dataclass(frozen=True)
class _Line:
    """One detected segment's vote."""

    delta: float
    weight: float
    endpoints: Endpoints


def suggest_candidates(
    gray: NDArray[np.float64], limit: int = _MAX_CANDIDATES
) -> list[Candidate]:
    """Ranked leveling suggestions with the lines behind each."""
    flat = 2
    if gray.ndim != flat or min(gray.shape) < _MIN_SIZE:
        return []
    field = np.ascontiguousarray(gray, dtype=np.float64)
    height, width = field.shape
    lines = _votes(lsdetect.detect(field), field, width, height)
    if not lines:
        return []
    lines.sort(key=lambda line: line.delta)
    clusters: list[list[_Line]] = []
    for line in lines:
        if clusters:
            members = clusters[-1]
            if abs(line.delta - _mean_delta(members)) <= _CLUSTER_TOL:
                members.append(line)
                continue
        clusters.append([line])
    total = sum(line.weight for line in lines)
    ranked = sorted(clusters, key=lambda members: -_score(members))
    out: list[Candidate] = []
    for members in ranked[:limit]:
        weight = sum(m.weight for m in members)
        if weight < total * _MIN_SHARE:
            continue
        heaviest = sorted(members, key=lambda m: -m.weight)
        out.append(
            Candidate(
                delta=_mean_delta(members),
                segments=tuple(m.endpoints for m in heaviest[:_MARK_SEGMENTS]),
            )
        )
    return out


def _votes(
    segments: list[lsdetect.Segment],
    gray: NDArray[np.float64],
    width: int,
    height: int,
) -> list[_Line]:
    """The near-axis segments as leveling votes."""
    floor = _MIN_LENGTH_SHARE * max(width, height)
    lines: list[_Line] = []
    for segment in segments:
        if segment.length < floor:
            continue
        delta = level_delta(segment.x2 - segment.x1, segment.y2 - segment.y1)
        if abs(delta) > DEV_LIMIT:
            continue
        lines.append(
            _Line(
                delta=delta,
                weight=segment.length**2 * _strength(gray, segment) ** 0.25,
                endpoints=(
                    segment.x1 / width,
                    segment.y1 / height,
                    segment.x2 / width,
                    segment.y2 / height,
                ),
            )
        )
    return lines


def _strength(gray: NDArray[np.float64], segment: lsdetect.Segment) -> float:
    """The mean gradient magnitude sampled along a segment.

    A faint line must not outvote a strong one of the same length, so
    the vote weight carries the edge contrast.
    """
    height, width = gray.shape
    steps = np.linspace(0.1, 0.9, 9)
    xs = np.clip(
        (segment.x1 + (segment.x2 - segment.x1) * steps).astype(int),
        1,
        width - 2,
    )
    ys = np.clip(
        (segment.y1 + (segment.y2 - segment.y1) * steps).astype(int),
        1,
        height - 2,
    )
    gx = gray[ys, xs + 1] - gray[ys, xs - 1]
    gy = gray[ys + 1, xs] - gray[ys - 1, xs]
    return float(np.hypot(gx, gy).mean()) + 1e-6


def _mean_delta(members: list[_Line]) -> float:
    """The weighted average correction of one family."""
    weight = sum(member.weight for member in members)
    return sum(member.delta * member.weight for member in members) / weight


def _score(members: list[_Line]) -> float:
    """A family's rank: its weight, damped for large corrections."""
    weight = sum(member.weight for member in members)
    return weight / (1.0 + (_mean_delta(members) / _TILT_PRIOR) ** 2)
