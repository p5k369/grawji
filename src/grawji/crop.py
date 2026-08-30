"""Crop and fine-rotate geometry.

The camera engine has no crop or rotate, so grawji applies geometry to
the jpeg the camera returns. A CropRotate describes that step in a
resolution-independent way: the crop rect is normalized to the bounding
box of the fine-rotated image, so the same value applies to the small
preview render and the full-resolution export.

The pipeline order is: exif orientation, then the coarse 90-degree
orientation, then the fine angle, then the crop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Rect = tuple[float, float, float, float]

FULL_RECT: Rect = (0.0, 0.0, 1.0, 1.0)
MAX_ANGLE = 45.0
MIN_SIZE = 0.05


@dataclass(frozen=True)
class CropRotate:
    """Geometry applied to a rendered jpeg, all resolution-independent."""

    orientation: int = 0
    angle: float = 0.0
    rect: Rect = FULL_RECT
    aspect: str = "Original"
    aspect_swapped: bool = False

    @property
    def is_identity(self) -> bool:
        """Whether this geometry changes nothing."""
        return (
            self.orientation == 0
            and self.angle == 0.0
            and self.rect == FULL_RECT
        )

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for the sidecar storage."""
        return {
            "orientation": self.orientation,
            "angle": self.angle,
            "rect": list(self.rect),
            "aspect": self.aspect,
            "aspect_swapped": self.aspect_swapped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CropRotate:
        """Build a CropRotate from a stored dict."""
        orientation = data.get("orientation", 0)
        if orientation not in (0, 90, 180, 270):
            orientation = 0
        angle = data.get("angle", 0.0)
        if not isinstance(angle, (int, float)):
            angle = 0.0
        angle = max(-MAX_ANGLE, min(MAX_ANGLE, float(angle)))
        rect = data.get("rect")
        aspect = data.get("aspect", "Free")
        if not isinstance(aspect, str):
            aspect = "Free"
        return cls(
            orientation=int(orientation),
            angle=angle,
            rect=_sane_rect(rect),
            aspect=aspect,
            aspect_swapped=bool(data.get("aspect_swapped", False)),
        )


_RECT_LEN = 4
_TINY = 1e-12


def _sane_rect(value: object) -> Rect:
    """Validate a stored rect, falling back to the full frame."""
    if not isinstance(value, (list, tuple)) or len(value) != _RECT_LEN:
        return FULL_RECT
    if not all(isinstance(v, (int, float)) for v in value):
        return FULL_RECT
    x, y, w, h = (float(v) for v in value)
    if w < MIN_SIZE or h < MIN_SIZE:
        return FULL_RECT
    x = max(0.0, min(x, 1.0 - MIN_SIZE))
    y = max(0.0, min(y, 1.0 - MIN_SIZE))
    w = max(MIN_SIZE, min(w, 1.0 - x))
    h = max(MIN_SIZE, min(h, 1.0 - y))
    return (x, y, w, h)


def rotated_size(w: float, h: float, angle: float) -> tuple[float, float]:
    """Bounding-box size of a image rotated by angle degrees."""
    rad = math.radians(angle)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    return w * c + h * s, w * s + h * c


def fits(w: float, h: float, angle: float, rect: Rect) -> bool:
    """Whether rect lies on pixels."""
    bw, bh = rotated_size(w, h, angle)
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    tol = 1e-6 * (w + h)
    x, y, rw, rh = rect
    for fx in (x, x + rw):
        for fy in (y, y + rh):
            px = fx * bw - bw / 2
            py = fy * bh - bh / 2
            ix = px * c + py * s
            iy = -px * s + py * c
            if abs(ix) > w / 2 + tol or abs(iy) > h / 2 + tol:
                return False
    return True


def largest_fit(w: float, h: float, angle: float, ratio: float) -> Rect:
    """Largest centered crop of the given pixel aspect that fits."""
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    aw, ah = ratio, 1.0
    scale = math.inf
    for cy in (ah, -ah):
        ix = abs(aw * c + cy * s)
        iy = abs(-aw * s + cy * c)
        if ix > _TINY:
            scale = min(scale, (w / 2) / ix)
        if iy > _TINY:
            scale = min(scale, (h / 2) / iy)
    bw, bh = rotated_size(w, h, angle)
    cw = 2 * scale * aw / bw
    ch = 2 * scale * ah / bh
    cw, ch = min(cw, 1.0), min(ch, 1.0)
    return ((1.0 - cw) / 2, (1.0 - ch) / 2, cw, ch)


def shrink_to_fit(w: float, h: float, angle: float, rect: Rect) -> Rect:
    """Scale rect about its own center until it fits on pixels."""
    if fits(w, h, angle, rect):
        return rect
    x, y, rw, rh = rect
    cx, cy = x + rw / 2, y + rh / 2

    def scaled(t: float) -> Rect:
        return (cx - rw * t / 2, cy - rh * t / 2, rw * t, rh * t)

    if not fits(w, h, angle, scaled(0.0)):
        bw, bh = rotated_size(w, h, angle)
        return largest_fit(w, h, angle, (rw * bw) / (rh * bh))
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if fits(w, h, angle, scaled(mid)):
            lo = mid
        else:
            hi = mid
    return scaled(lo)


def _origin_constraints(
    w: float, h: float, angle: float, rw: float, rh: float
) -> list[tuple[float, float, float]]:
    """Half-planes for legal rect origins."""
    bw, bh = rotated_size(w, h, angle)
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    constraints: list[tuple[float, float, float]] = []
    for gx, gy, half in ((bw * c, bh * s, w / 2), (-bw * s, bh * c, h / 2)):
        biases = [
            gx * (ax - 0.5) + gy * (ay - 0.5)
            for ax in (0.0, rw)
            for ay in (0.0, rh)
        ]
        constraints.append((gx, gy, half - max(biases)))
        constraints.append((-gx, -gy, half + min(biases)))
    return constraints


def _project_origin(
    constraints: list[tuple[float, float, float]],
    tx: float,
    ty: float,
    fallback: tuple[float, float],
    tol: float,
) -> tuple[float, float]:
    """The point of the constraint polygon closest to."""

    def ok(px: float, py: float) -> bool:
        return all(nx * px + ny * py <= d + tol for nx, ny, d in constraints)

    if ok(tx, ty):
        return tx, ty
    best = fallback
    best_d2 = math.inf
    for nx, ny, d in constraints:
        nn = nx * nx + ny * ny
        if nn < _TINY:
            continue
        t = (nx * tx + ny * ty - d) / nn
        px, py = tx - t * nx, ty - t * ny
        d2 = (px - tx) ** 2 + (py - ty) ** 2
        if d2 < best_d2 and ok(px, py):
            best, best_d2 = (px, py), d2
    for i, (n1x, n1y, d1) in enumerate(constraints):
        for n2x, n2y, d2c in constraints[i + 1 :]:
            det = n1x * n2y - n1y * n2x
            if abs(det) < _TINY:
                continue
            px = (d1 * n2y - d2c * n1y) / det
            py = (n1x * d2c - n2x * d1) / det
            dd = (px - tx) ** 2 + (py - ty) ** 2
            if dd < best_d2 and ok(px, py):
                best, best_d2 = (px, py), dd
    return best


def clamp_move(
    w: float, h: float, angle: float, rect: Rect, dx: float, dy: float
) -> Rect:
    """Move rect by, stopped exactly at the rotated image.

    The target origin is projected onto the parallelogram of legal
    origins, so the rect reaches every position the pixels allow and
    glides along tilted edges instead of stopping early.
    """
    x, y, rw, rh = rect
    constraints = _origin_constraints(w, h, angle, rw, rh)
    tol = 1e-7 * (w + h)
    ox, oy = _project_origin(constraints, x + dx, y + dy, (x, y), tol)
    return (ox, oy, rw, rh)


def constrain_drag(  # noqa: PLR0913
    w: float,
    h: float,
    angle: float,
    rect: Rect,
    zone: str,
    *,
    dx: float,
    dy: float,
    ratio: float | None = None,
) -> Rect:
    """Apply a drag to rect, limited to stay on rotated image pixels."""
    if not fits(w, h, angle, rect):
        rect = shrink_to_fit(w, h, angle, rect)
    candidate = apply_drag(rect, zone, dx, dy, ratio=ratio)
    if fits(w, h, angle, candidate):
        return candidate
    if zone == "move":
        return clamp_move(w, h, angle, rect, dx, dy)
    if ratio is None and len(zone) == 2:  # noqa: PLR2004
        part = _max_drag(w, h, angle, rect, zone, dx=dx, dy=0.0, ratio=None)
        return _max_drag(w, h, angle, part, zone, dx=0.0, dy=dy, ratio=None)
    return _max_drag(w, h, angle, rect, zone, dx=dx, dy=dy, ratio=ratio)


def _max_drag(  # noqa: PLR0913
    w: float,
    h: float,
    angle: float,
    rect: Rect,
    zone: str,
    *,
    dx: float,
    dy: float,
    ratio: float | None,
) -> Rect:
    """The largest achievable fraction of a drag applied to rect.

    A candidate counts as achievable if it fits outright, or fits
    after sliding the rect minimally along the tilted image edge.
    Without the slide, a crop hugging the border at a near-zero angle
    is blocked by fractions of a pixel. With it, growth continues
    until the size genuinely no longer fits anywhere.
    """
    tol = 1e-7 * (w + h)

    def fitted(t: float) -> Rect | None:
        cand = apply_drag(rect, zone, dx * t, dy * t, ratio=ratio)
        if fits(w, h, angle, cand):
            return cand
        x, y, rw, rh = cand
        constraints = _origin_constraints(w, h, angle, rw, rh)
        ox, oy = _project_origin(constraints, x, y, (x, y), tol)
        moved = (ox, oy, rw, rh)
        return moved if fits(w, h, angle, moved) else None

    full = fitted(1.0)
    if full is not None:
        return full
    best = rect
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        cand = fitted(mid)
        if cand is not None:
            best, lo = cand, mid
        else:
            hi = mid
    return best


def swap_rect(w: float, h: float, angle: float, rect: Rect) -> Rect:
    """The crop rect with its pixel width and height exchanged.

    Turns a landscape selection into the portrait of the same pixel
    size, kept at its center and shrunk to stay on pixels.
    """
    bw, bh = rotated_size(w, h, angle)
    x, y, rw, rh = rect
    nw, nh = (rh * bh) / bw, (rw * bw) / bh
    scale = min(1.0, 1.0 / nw, 1.0 / nh)
    nw, nh = nw * scale, nh * scale
    nx = min(max(x + rw / 2 - nw / 2, 0.0), 1.0 - nw)
    ny = min(max(y + rh / 2 - nh / 2, 0.0), 1.0 - nh)
    return shrink_to_fit(w, h, angle, (nx, ny, nw, nh))


def rotate_rect_90(rect: Rect, degrees: int) -> Rect:
    """Carry a normalized crop rect through a 90-degree step rotation."""
    x, y, w, h = rect
    step = degrees % 360
    if step == 90:  # noqa: PLR2004
        return (1.0 - y - h, x, h, w)
    if step == 180:  # noqa: PLR2004
        return (1.0 - x - w, 1.0 - y - h, w, h)
    if step == 270:  # noqa: PLR2004
        return (y, 1.0 - x - w, h, w)
    return rect


def hit_zone(  # noqa: PLR0911
    rect: Rect, x: float, y: float, mx: float, my: float
) -> str | None:
    """The drag zone under: a corner, an edge, move or None.

    Args:
        rect: The crop rect, normalized.
        x: Pointer x, normalized.
        y: Pointer y, normalized.
        mx: Horizontal grab margin, normalized.
        my: Vertical grab margin, normalized.
    """
    rx, ry, rw, rh = rect
    left, right = rx, rx + rw
    top, bottom = ry, ry + rh
    near_l, near_r = abs(x - left) <= mx, abs(x - right) <= mx
    near_t, near_b = abs(y - top) <= my, abs(y - bottom) <= my
    in_x = left - mx <= x <= right + mx
    in_y = top - my <= y <= bottom + my
    if near_l and near_t:
        return "nw"
    if near_r and near_t:
        return "ne"
    if near_l and near_b:
        return "sw"
    if near_r and near_b:
        return "se"
    if near_t and in_x:
        return "n"
    if near_b and in_x:
        return "s"
    if near_l and in_y:
        return "w"
    if near_r and in_y:
        return "e"
    if left <= x <= right and top <= y <= bottom:
        return "move"
    return None


def apply_drag(  # noqa: PLR0913
    rect: Rect,
    zone: str,
    dx: float,
    dy: float,
    *,
    ratio: float | None = None,
    min_w: float = MIN_SIZE,
    min_h: float = MIN_SIZE,
    bounds: Rect = FULL_RECT,
) -> Rect:
    """The crop rect after dragging zone by, normalized."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    if zone == "move":
        return (
            max(bx, min(x + dx, bx + bw - w)),
            max(by, min(y + dy, by + bh - h)),
            w,
            h,
        )
    left, right = x, x + w
    top, bottom = y, y + h
    if "w" in zone:
        left = max(bx, min(left + dx, right - min_w))
    if "e" in zone:
        right = max(left + min_w, min(right + dx, bx + bw))
    if "n" in zone:
        top = max(by, min(top + dy, bottom - min_h))
    if "s" in zone:
        bottom = max(top + min_h, min(bottom + dy, by + bh))
    if ratio is not None:
        left, top, right, bottom = _apply_ratio(
            zone,
            ratio,
            (left, top, right, bottom),
            (w, h),
            (min_w, min_h),
            bounds,
        )
    return (left, top, right - left, bottom - top)


def _apply_ratio(
    zone: str,
    ratio: float,
    box: tuple[float, float, float, float],
    ref: tuple[float, float],
    mins: tuple[float, float],
    bounds: Rect,
) -> tuple[float, float, float, float]:
    """Re-shape a dragged box to the given normalized aspect ratio."""
    left, top, right, bottom = box
    w, h = _ratio_size(
        zone, ratio, (right - left, bottom - top), ref, mins, bounds
    )
    if zone in ("w", "e"):
        cy = (top + bottom) / 2
        top, bottom = cy - h / 2, cy + h / 2
    elif zone in ("n", "s"):
        cx = (left + right) / 2
        left, right = cx - w / 2, cx + w / 2
    if "n" in zone:
        top = bottom - h
    elif "s" in zone:
        bottom = top + h
    if "w" in zone:
        left = right - w
    elif "e" in zone:
        right = left + w
    return _slide_into((left, top, right, bottom), bounds)


def _ratio_size(
    zone: str,
    ratio: float,
    size: tuple[float, float],
    ref: tuple[float, float],
    mins: tuple[float, float],
    bounds: Rect,
) -> tuple[float, float]:
    """The re-shaped of a ratio-bound drag."""
    w, h = size
    ref_w, ref_h = ref
    min_w, min_h = mins
    width_leads = zone in ("w", "e") or (
        zone not in ("n", "s") and abs(w - ref_w) >= abs(h - ref_h) * ratio
    )
    if width_leads:
        w = max(min_w, min(w, bounds[2], bounds[3] * ratio))
        return w, w / ratio
    h = max(min_h, min(h, bounds[3], bounds[2] / ratio))
    return h * ratio, h


def _slide_into(
    box: tuple[float, float, float, float], bounds: Rect
) -> tuple[float, float, float, float]:
    """Slide a box inside bounds without changing its shape."""
    left, top, right, bottom = box
    bx, by, bw, bh = bounds
    if left < bx:
        right += bx - left
        left = bx
    if right > bx + bw:
        left -= right - (bx + bw)
        right = bx + bw
    if top < by:
        bottom += by - top
        top = by
    if bottom > by + bh:
        top -= bottom - (by + bh)
        bottom = by + bh
    return (
        max(bx, left),
        max(by, top),
        min(bx + bw, right),
        min(by + bh, bottom),
    )


def level_delta(dx: float, dy: float) -> float:
    """Angle to add to level a dragged line.

    The line runs (dx, dy) in display coordinates (y down). Whichever of
    horizontal or vertical is nearer becomes the target, darktable-style.
    """
    if dx == 0.0 and dy == 0.0:
        return 0.0
    theta = math.degrees(math.atan2(dy, dx))
    folded = (theta + 45.0) % 90.0 - 45.0
    return -folded


Line = tuple[float, float, float, float]

# 1/phi: the golden-section split positions are at 1-_PHI_INV and _PHI_INV.
_PHI_INV = 0.6180339887498949
_GRID_STEPS = 8


def guide_lines(
    kind: str,
    w: float,
    h: float,
    *,
    flip_h: bool = False,
    flip_v: bool = False,
) -> list[Line]:
    """Composition guide segments for a crop."""
    lines = _guide_lines(kind, w, h)
    if flip_h:
        lines = [(w - x1, y1, w - x2, y2) for x1, y1, x2, y2 in lines]
    if flip_v:
        lines = [(x1, h - y1, x2, h - y2) for x1, y1, x2, y2 in lines]
    return lines


def _guide_lines(kind: str, w: float, h: float) -> list[Line]:
    """The unmirrored guide segments for a crop."""
    fractions = {
        "Thirds": (1 / 3, 2 / 3),
        "Golden": (1 - _PHI_INV, _PHI_INV),
        "Center": (0.5,),
        "Grid": tuple(i / _GRID_STEPS for i in range(1, _GRID_STEPS)),
    }.get(kind)
    if fractions is not None:
        return _section_lines(w, h, fractions)
    if kind == "Diagonals":
        m = min(w, h)
        return [
            (0.0, 0.0, m, m),
            (w, 0.0, w - m, m),
            (0.0, h, m, h - m),
            (w, h, w - m, h - m),
        ]
    if kind == "Triangles":
        return _triangle_lines(w, h)
    if kind == "Spiral":
        return _spiral_lines(w, h)
    return []


def _section_lines(
    w: float, h: float, fractions: tuple[float, ...]
) -> list[Line]:
    """Vertical and horizontal lines at the given width/height fractions."""
    lines: list[Line] = []
    for f in fractions:
        lines.append((w * f, 0.0, w * f, h))
        lines.append((0.0, h * f, w, h * f))
    return lines


def _triangle_lines(w: float, h: float) -> list[Line]:
    """Harmonious triangles: the diagonal plus two perpendiculars."""
    dd = w * w + h * h
    if dd <= 0:
        return []
    lines: list[Line] = [(0.0, 0.0, w, h)]
    for px, py in ((w, 0.0), (0.0, h)):
        t = (px * w + py * h) / dd
        lines.append((px, py, t * w, t * h))
    return lines


def _spiral_lines(w: float, h: float) -> list[Line]:
    """The golden spiral, stretched to the crop."""
    if h > w:
        return [(y1, x1, y2, x2) for x1, y1, x2, y2 in _spiral_lines(h, w)]
    phi = 1.0 / _PHI_INV
    x, y, rw, rh = 0.0, 0.0, phi, 1.0
    points: list[tuple[float, float]] = []
    for k in range(9):
        phase = k % 4
        if phase == 0:
            s, cx, cy, start = rh, x + rh, y + rh, 180.0
            nxt = (x + s, y, rw - s, rh)
        elif phase == 1:
            s, cx, cy, start = rw, x, y + rw, 270.0
            nxt = (x, y + s, rw, rh - s)
        elif phase == 2:  # noqa: PLR2004
            s, cx, cy, start = rh, x + rw - rh, y, 0.0
            nxt = (x, y, rw - s, rh)
        else:
            s, cx, cy, start = rw, x + rw, y + rh - rw, 90.0
            nxt = (x, y, rw, rh - s)
        for i in range(17):
            ang = math.radians(start + 90.0 * i / 16)
            points.append((cx + s * math.cos(ang), cy + s * math.sin(ang)))
        x, y, rw, rh = nxt
    sx, sy = w / phi, h
    return [
        (
            points[i][0] * sx,
            points[i][1] * sy,
            points[i + 1][0] * sx,
            points[i + 1][1] * sy,
        )
        for i in range(len(points) - 1)
    ]
