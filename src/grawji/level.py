"""Automatic straightening: suggest angles that level an image."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from grawji.crop import level_delta

# Lines further than this from an axis are assumed intentional.
DEV_LIMIT = 12.0
# Squared Scharr magnitude a pixel needs to count as an edge.
_MAG_MIN = 300**2
# A pixel joins a region while its signed gradient direction agrees
# with the region's running mean within this many degrees.
_GROW_TOL = 2.0
# Regions smaller than this many pixels are noise.
_MIN_REGION = 20
# Major/minor variance ratio a region needs to count as a line rather
# than a textured blob.
_MIN_ELONGATION = 12.0
_MINOR_FLOOR = 0.25
# Hard thickness cap: a line's cross-axis variance stays small no
# matter how long it is.
_MINOR_MAX = 3.0
# Fragments of one interrupted line chain when their directions agree
# within ~1.5 degrees and their lateral offset is tiny.
_CHAIN_SIN = 0.026
_CHAIN_OFFSET = 3.0
# Segments within this many degrees of a cluster's running mean fold
# into one candidate.
_CLUSTER_TOL = 1.0
# The Scharr window needs a pixel of border on every side.
_MIN_SIZE = 3
# A candidate needs at least this share of the total segment weight.
_MIN_SHARE = 0.10
_MAX_CANDIDATES = 3
# Longest segments kept per candidate for the on-screen marking.
_MARK_SEGMENTS = 12

# 8-connected neighborhood for region growing.
_NEIGHBORS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


def _edge_angles(
    gray: Sequence[Sequence[int]], width: int, height: int
) -> list[list[float | None]]:
    """Per-pixel signed gradient direction of strong edges."""
    angles: list[list[float | None]] = [[None] * width for _ in range(height)]
    for y in range(1, height - 1):
        r0, r1, r2 = gray[y - 1], gray[y], gray[y + 1]
        row = angles[y]
        for x in range(1, width - 1):
            gx = (3 * r0[x + 1] + 10 * r1[x + 1] + 3 * r2[x + 1]) - (
                3 * r0[x - 1] + 10 * r1[x - 1] + 3 * r2[x - 1]
            )
            gy = (3 * r2[x - 1] + 10 * r2[x] + 3 * r2[x + 1]) - (
                3 * r0[x - 1] + 10 * r0[x] + 3 * r0[x + 1]
            )
            if gx * gx + gy * gy < _MAG_MIN:
                continue
            row[x] = math.degrees(math.atan2(gy, gx))
    return angles


def _angle_gap(a: float, b: float) -> float:
    """Absolute angular difference in degrees, wrap-safe."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


Endpoints = tuple[float, float, float, float]


@dataclass(frozen=True)
class Segment:
    """One fitted line: its leveling angle, weight and endpoints."""

    delta: float
    weight: float
    endpoints: Endpoints


@dataclass(frozen=True)
class Candidate:
    """One leveling suggestion: the angle plus the lines behind it."""

    delta: float
    share: float
    segments: tuple[Endpoints, ...]


@dataclass(frozen=True)
class _Region:
    """Second-order moments of one grown pixel region."""

    n: int
    mean_x: float
    mean_y: float
    sxx: float
    syy: float
    sxy: float

    def minor_ok(self) -> bool:
        """Whether the region is still thin enough to be a line."""
        half_gap = math.sqrt(
            (self.sxx - self.syy) ** 2 / 4 + self.sxy * self.sxy
        )
        return (self.sxx + self.syy) / 2 - half_gap <= _MINOR_MAX


def _region_of(pixels: list[tuple[int, int]]) -> _Region:
    """The moments of a pixel region."""
    n = len(pixels)
    mean_x = sum(x for x, _ in pixels) / n
    mean_y = sum(y for _, y in pixels) / n
    sxx = syy = sxy = 0.0
    for x, y in pixels:
        dx = x - mean_x
        dy = y - mean_y
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    return _Region(n, mean_x, mean_y, sxx / n, syy / n, sxy / n)


def _merge_regions(a: _Region, b: _Region) -> _Region:
    """Combine two regions' moments."""
    n = a.n + b.n
    mean_x = (a.n * a.mean_x + b.n * b.mean_x) / n
    mean_y = (a.n * a.mean_y + b.n * b.mean_y) / n
    parts = []
    for r in (a, b):
        ox, oy = r.mean_x - mean_x, r.mean_y - mean_y
        parts.append(
            (
                r.n * (r.sxx + ox * ox),
                r.n * (r.syy + oy * oy),
                r.n * (r.sxy + ox * oy),
            )
        )
    sxx = (parts[0][0] + parts[1][0]) / n
    syy = (parts[0][1] + parts[1][1]) / n
    sxy = (parts[0][2] + parts[1][2]) / n
    return _Region(n, mean_x, mean_y, sxx, syy, sxy)


def _fit_region(region: _Region, width: int, height: int) -> Segment | None:
    """PCA line fit of a region, or None when it is not a line."""
    sxx, syy, sxy = region.sxx, region.syy, region.sxy
    half_gap = math.sqrt((sxx - syy) ** 2 / 4 + sxy * sxy)
    major = (sxx + syy) / 2 + half_gap
    minor = (sxx + syy) / 2 - half_gap
    if minor > _MINOR_MAX:
        return None  # thick blob, not a line
    if major < _MIN_ELONGATION * max(minor, _MINOR_FLOOR):
        return None
    if sxy == 0.0:
        direction = (1.0, 0.0) if sxx >= syy else (0.0, 1.0)
    else:
        direction = (sxy, major - sxx)
    length = math.sqrt(direction[0] ** 2 + direction[1] ** 2)
    if length == 0.0:
        return None
    ux, uy = direction[0] / length, direction[1] / length
    half = math.sqrt(3.0 * major)
    endpoints = (
        (region.mean_x - ux * half) / width,
        (region.mean_y - uy * half) / height,
        (region.mean_x + ux * half) / width,
        (region.mean_y + uy * half) / height,
    )
    return Segment(level_delta(ux, uy), region.n * major, endpoints)


def _direction_of(region: _Region) -> tuple[float, float]:
    """Unit direction of a region's major axis."""
    sxx, syy, sxy = region.sxx, region.syy, region.sxy
    half_gap = math.sqrt((sxx - syy) ** 2 / 4 + sxy * sxy)
    major = (sxx + syy) / 2 + half_gap
    if sxy == 0.0:
        return (1.0, 0.0) if sxx >= syy else (0.0, 1.0)
    dx, dy = sxy, major - sxx
    length = math.sqrt(dx * dx + dy * dy) or 1.0
    return dx / length, dy / length


def _chain_regions(regions: list[_Region]) -> list[_Region]:
    """Merge collinear regions into one."""
    work = list(regions)
    merged = True
    while merged:
        merged = False
        i = 0
        while i < len(work):
            a = work[i]
            ux, uy = _direction_of(a)
            j = len(work) - 1
            while j > i:
                b = work[j]
                bx, by = _direction_of(b)
                cross = abs(ux * by - uy * bx)
                lateral = abs(
                    (b.mean_x - a.mean_x) * -uy + (b.mean_y - a.mean_y) * ux
                )
                if cross <= _CHAIN_SIN and lateral <= _CHAIN_OFFSET:
                    candidate = _merge_regions(a, b)
                    if candidate.minor_ok():
                        work[i] = a = candidate
                        ux, uy = _direction_of(a)
                        del work[j]
                        merged = True
                j -= 1
            i += 1
    return work


def _segments_of(
    angles: list[list[float | None]], width: int, height: int
) -> list[Segment]:
    """Grow edge pixels into fitted line segments."""
    visited = [[False] * width for _ in range(height)]
    regions: list[_Region] = []
    segments: list[Segment] = []
    for start_y in range(1, height - 1):
        for start_x in range(1, width - 1):
            seed = angles[start_y][start_x]
            if seed is None or visited[start_y][start_x]:
                continue
            visited[start_y][start_x] = True
            pixels = [(start_x, start_y)]
            stack = [(start_x, start_y)]
            sum_x = math.cos(math.radians(seed))
            sum_y = math.sin(math.radians(seed))
            while stack:
                px, py = stack.pop()
                mean = math.degrees(math.atan2(sum_y, sum_x))
                for ox, oy in _NEIGHBORS:
                    nx, ny = px + ox, py + oy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    if visited[ny][nx]:
                        continue
                    value = angles[ny][nx]
                    if value is None or _angle_gap(value, mean) > _GROW_TOL:
                        continue
                    visited[ny][nx] = True
                    pixels.append((nx, ny))
                    stack.append((nx, ny))
                    sum_x += math.cos(math.radians(value))
                    sum_y += math.sin(math.radians(value))
            if len(pixels) < _MIN_REGION:
                continue
            region = _region_of(pixels)
            if _fit_region(region, width, height) is not None:
                regions.append(region)
    for region in _chain_regions(regions):
        fitted = _fit_region(region, width, height)
        if fitted is not None and abs(fitted.delta) <= DEV_LIMIT:
            segments.append(fitted)
    return segments


def suggest_candidates(
    gray: Sequence[Sequence[int]], limit: int = _MAX_CANDIDATES
) -> list[Candidate]:
    """Ranked leveling suggestions with the lines behind each."""
    height = len(gray)
    width = len(gray[0]) if height else 0
    if width < _MIN_SIZE or height < _MIN_SIZE:
        return []
    segments = _segments_of(_edge_angles(gray, width, height), width, height)
    if not segments:
        return []
    segments.sort(key=lambda seg: seg.delta)
    clusters: list[list[Segment]] = []
    for segment in segments:
        if clusters:
            members = clusters[-1]
            weight = sum(m.weight for m in members)
            mean = sum(m.delta * m.weight for m in members) / weight
            if abs(segment.delta - mean) <= _CLUSTER_TOL:
                members.append(segment)
                continue
        clusters.append([segment])
    total = sum(seg.weight for seg in segments)
    ranked = sorted(
        clusters, key=lambda members: -sum(m.weight for m in members)
    )
    out: list[Candidate] = []
    for members in ranked[:limit]:
        weight = sum(m.weight for m in members)
        if weight < total * _MIN_SHARE:
            continue
        heaviest = sorted(members, key=lambda m: -m.weight)
        out.append(
            Candidate(
                delta=sum(m.delta * m.weight for m in members) / weight,
                share=weight / total,
                segments=tuple(m.endpoints for m in heaviest[:_MARK_SEGMENTS]),
            )
        )
    return out


def suggest_deltas(
    gray: Sequence[Sequence[int]], limit: int = _MAX_CANDIDATES
) -> list[float]:
    """Ranked leveling angles only."""
    return [c.delta for c in suggest_candidates(gray, limit)]


def suggest_delta(gray: Sequence[Sequence[int]]) -> float | None:
    """The single best leveling angle, or None."""
    candidates = suggest_deltas(gray, limit=1)
    return candidates[0] if candidates else None
