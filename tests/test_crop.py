"""Tests for the pure crop/rotate geometry and its sidecar IO."""

from __future__ import annotations

import itertools
import math

import pytest

from grawji import crop
from grawji.crop import (
    FULL_RECT,
    CropRotate,
    apply_drag,
    clamp_move,
    constrain_drag,
    fits,
    guide_lines,
    hit_zone,
    largest_fit,
    level_delta,
    rotate_rect_90,
    rotated_size,
    shrink_to_fit,
    swap_rect,
)


def test_identity() -> None:
    """A default CropRotate changes nothing."""
    assert CropRotate().is_identity
    assert not CropRotate(orientation=90).is_identity
    assert not CropRotate(angle=1.0).is_identity
    assert not CropRotate(rect=(0.1, 0.1, 0.8, 0.8)).is_identity


def test_dict_round_trip() -> None:
    """to_dict/from_dict preserve values and ignore extras."""
    value = CropRotate(
        orientation=180,
        angle=-3.5,
        rect=(0.1, 0.2, 0.5, 0.4),
        aspect="1:1",
        aspect_swapped=True,
    )
    data = value.to_dict()
    data["future_field"] = "ignored"
    assert CropRotate.from_dict(data) == value


def test_from_dict_sanitizes() -> None:
    """Broken stored values fall back to safe defaults."""
    assert CropRotate.from_dict({"orientation": 45}).orientation == 0
    assert CropRotate.from_dict({"orientation": "90"}).orientation == 0
    assert CropRotate.from_dict({"angle": 90.0}).angle == 45.0
    assert CropRotate.from_dict({"angle": "bad"}).angle == 0.0
    assert CropRotate.from_dict({"rect": [0, 0, 1]}).rect == FULL_RECT
    assert CropRotate.from_dict({"rect": [0, 0, 0.001, 1]}).rect == FULL_RECT
    assert CropRotate.from_dict({"rect": "bad"}).rect == FULL_RECT
    assert CropRotate.from_dict({"aspect": 3}).aspect == "Free"


def test_aspect_defaults() -> None:
    """Untouched images say Original; aspect-less sidecars say Free."""
    assert CropRotate().aspect == "Original"
    assert not CropRotate().aspect_swapped
    old = CropRotate.from_dict({"rect": [0.1, 0.1, 0.5, 0.5]})
    assert old.aspect == "Free"


def test_rotated_size() -> None:
    """Bounding boxes match hand-computed values."""
    assert rotated_size(600, 400, 0.0) == (600, 400)
    bw, bh = rotated_size(600, 400, 90.0)
    assert bw == pytest.approx(400)
    assert bh == pytest.approx(600)
    bw, bh = rotated_size(100, 100, 45.0)
    assert bw == pytest.approx(100 * math.sqrt(2))
    assert bh == pytest.approx(100 * math.sqrt(2))


def test_fits_full_rect() -> None:
    """The full rect fits only when there is no fine angle."""
    assert fits(600, 400, 0.0, FULL_RECT)
    assert not fits(600, 400, 3.0, FULL_RECT)


def test_largest_fit_no_angle() -> None:
    """At zero angle the native aspect fills the whole frame."""
    rect = largest_fit(600, 400, 0.0, 1.5)
    assert rect == pytest.approx((0.0, 0.0, 1.0, 1.0))


def test_largest_fit_square_on_landscape() -> None:
    """A square crop on a 3:2 frame is height-limited and centered."""
    rect = largest_fit(600, 400, 0.0, 1.0)
    x, y, w, h = rect
    assert w * 600 == pytest.approx(400)
    assert h * 400 == pytest.approx(400)
    assert x == pytest.approx((1 - w) / 2)
    assert y == pytest.approx(0.0)


def test_largest_fit_is_maximal() -> None:
    """The fitted rect fits, a slightly larger one does not."""
    for angle in (2.0, 7.5, -12.0, 30.0):
        rect = largest_fit(600, 400, angle, 1.5)
        assert fits(600, 400, angle, rect)
        x, y, w, h = rect
        grown = (
            x - 0.01 * w,
            y - 0.01 * h,
            w * 1.02,
            h * 1.02,
        )
        assert not fits(600, 400, angle, grown)


def test_shrink_to_fit() -> None:
    """An oversized rect shrinks about its center until it fits."""
    rect = shrink_to_fit(600, 400, 5.0, FULL_RECT)
    assert fits(600, 400, 5.0, rect)
    x, y, w, h = rect
    assert x + w / 2 == pytest.approx(0.5, abs=1e-6)
    assert y + h / 2 == pytest.approx(0.5, abs=1e-6)
    # An already-fitting rect is returned unchanged.
    small = (0.4, 0.4, 0.2, 0.2)
    assert shrink_to_fit(600, 400, 5.0, small) == small


def test_constrain_drag_move() -> None:
    """Moving toward the void stops at the rotated image edge."""
    start = (0.1, 0.4, 0.2, 0.2)
    assert fits(600, 400, 5.0, start)
    result = constrain_drag(600, 400, 5.0, start, "move", dx=0.69, dy=0.0)
    assert fits(600, 400, 5.0, result)
    assert start[0] < result[0] < start[0] + 0.69
    assert result[2:] == start[2:]  # a move never resizes
    small = (0.45, 0.45, 0.1, 0.1)
    moved = constrain_drag(600, 400, 5.0, small, "move", dx=0.01, dy=0.0)
    assert moved == apply_drag(small, "move", 0.01, 0.0)


def test_constrain_drag_move_slides_along_edges() -> None:
    """A blocked axis does not stop movement along the other axis."""
    start = (0.4, 0.4, 0.2, 0.2)
    result = constrain_drag(600, 400, 5.0, start, "move", dx=0.05, dy=-0.8)
    assert fits(600, 400, 5.0, result)
    assert abs(result[0] - 0.45) < 0.08  # x stayed near the pointer
    assert result[1] < 0.1  # y hugs the tilted top edge


def test_constrain_drag_move_reaches_targets_exactly() -> None:
    """Any legal target position is reached, no refusals."""
    start = (0.4, 0.4, 0.2, 0.2)
    result = constrain_drag(600, 400, 5.0, start, "move", dx=0.1, dy=-0.15)
    assert result == pytest.approx((0.5, 0.25, 0.2, 0.2))


def test_constrain_drag_conforms_stale_rect() -> None:
    """A rect that no longer fits is conformed instead of freezing."""
    stale = FULL_RECT  # the full rect never fits once there is an angle
    assert not fits(600, 400, 5.0, stale)
    grown = constrain_drag(600, 400, 5.0, stale, "e", dx=0.1, dy=0.0)
    assert fits(600, 400, 5.0, grown)
    moved = constrain_drag(600, 400, 5.0, stale, "move", dx=0.01, dy=0.0)
    assert fits(600, 400, 5.0, moved)
    assert moved[0] + moved[2] / 2 == pytest.approx(0.51, abs=0.02)


def test_constrain_drag_resize_axes_are_independent() -> None:
    """A blocked axis never holds a free-aspect corner's other axis."""
    start = (0.3, 0.25, 0.2, 0.2)
    diag = constrain_drag(600, 400, 5.0, start, "ne", dx=0.25, dy=-0.4)
    pure_x = constrain_drag(600, 400, 5.0, start, "ne", dx=0.25, dy=0.0)
    assert fits(600, 400, 5.0, diag)
    assert diag[2] == pytest.approx(pure_x[2], abs=1e-9)
    # Sweeping the blocked vertical back never changes the width.
    widths = {
        round(
            constrain_drag(
                600, 400, 5.0, start, "ne", dx=0.25, dy=-0.4 + i * 0.01
            )[2],
            9,
        )
        for i in range(41)
    }
    assert len(widths) == 1


def test_constrain_drag_resize_escapes_micro_pinch() -> None:
    """A border-hugging crop still scales at near-zero angles."""
    angle = -0.02
    hug = clamp_move(6000, 4000, angle, (0.01, 0.2, 0.3, 0.3), -1.0, 0.0)
    assert fits(6000, 4000, angle, hug)
    grown = constrain_drag(6000, 4000, angle, hug, "s", dx=0.0, dy=0.4)
    assert fits(6000, 4000, angle, grown)
    assert grown[3] > hug[3] + 0.35  # grew essentially the full drag
    assert abs(grown[0] - hug[0]) < 0.001  # the nudge is invisible


def test_clamp_move_full_reach() -> None:
    """Corners of the tilted image are reachable, not just its middle."""
    start = (0.45, 0.45, 0.1, 0.1)
    corner = clamp_move(600, 400, 5.0, start, -0.6, -0.6)
    assert fits(600, 400, 5.0, corner)
    assert corner[1] < 0.05
    again = constrain_drag(
        600, 400, 5.0, start, "move", dx=corner[0] - 0.45, dy=corner[1] - 0.45
    )
    assert again == pytest.approx(corner)


def test_constrain_drag_resize() -> None:
    """A resize grows as far as the rotated image allows, no further."""
    start = (0.1, 0.4, 0.2, 0.2)
    result = constrain_drag(600, 400, 5.0, start, "e", dx=1.0, dy=0.0)
    assert fits(600, 400, 5.0, result)
    assert result[2] > start[2]  # it grew
    assert result[3] == pytest.approx(start[3])
    again = constrain_drag(600, 400, 5.0, start, "e", dx=1.0, dy=0.0)
    assert again == result


def test_rotate_rect_90_round_trips() -> None:
    """90-degree carries compose back to the original rect."""
    rect = (0.1, 0.2, 0.5, 0.3)
    once = rotate_rect_90(rect, 90)
    assert once == pytest.approx((0.5, 0.1, 0.3, 0.5))
    back = rotate_rect_90(once, -90)
    assert back == pytest.approx(rect)
    twice = rotate_rect_90(rotate_rect_90(rect, 180), 180)
    assert twice == pytest.approx(rect)
    four = rect
    for _ in range(4):
        four = rotate_rect_90(four, 90)
    assert four == pytest.approx(rect)


def test_swap_rect_inverts_aspect() -> None:
    """Swapping exchanges the crop's pixel width and height."""
    out = swap_rect(600, 400, 0.0, FULL_RECT)
    x, _y, w, h = out
    pw, ph = w * 600, h * 400
    assert pw / ph == pytest.approx(2 / 3)
    assert ph == pytest.approx(400)
    assert x == pytest.approx((1 - w) / 2)
    small = swap_rect(600, 400, 0.0, (0.4, 0.375, 0.2, 0.25))
    px, py, sw, sh = small
    assert sw * 600 == pytest.approx(100)
    assert sh * 400 == pytest.approx(120)
    assert px + sw / 2 == pytest.approx(0.5)
    assert py + sh / 2 == pytest.approx(0.5)
    tilted = swap_rect(600, 400, 7.0, largest_fit(600, 400, 7.0, 1.5))
    assert fits(600, 400, 7.0, tilted)
    _tx, _ty, tw, th = tilted
    bw, bh = rotated_size(600, 400, 7.0)
    assert (tw * bw) / (th * bh) == pytest.approx(2 / 3)


def test_hit_zone() -> None:
    """Corners, edges, the inside and the outside are told apart."""
    rect = (0.2, 0.2, 0.6, 0.6)
    m = 0.02
    assert hit_zone(rect, 0.2, 0.2, m, m) == "nw"
    assert hit_zone(rect, 0.8, 0.2, m, m) == "ne"
    assert hit_zone(rect, 0.2, 0.8, m, m) == "sw"
    assert hit_zone(rect, 0.8, 0.8, m, m) == "se"
    assert hit_zone(rect, 0.5, 0.2, m, m) == "n"
    assert hit_zone(rect, 0.5, 0.8, m, m) == "s"
    assert hit_zone(rect, 0.2, 0.5, m, m) == "w"
    assert hit_zone(rect, 0.8, 0.5, m, m) == "e"
    assert hit_zone(rect, 0.5, 0.5, m, m) == "move"
    assert hit_zone(rect, 0.05, 0.05, m, m) is None


def test_apply_drag_move_clamps() -> None:
    """Moving the rect stops at the canvas border."""
    rect = (0.2, 0.2, 0.6, 0.6)
    moved = apply_drag(rect, "move", 0.5, -0.5)
    assert moved == (0.4, 0.0, 0.6, 0.6)


def test_apply_drag_edges_and_min_size() -> None:
    """Edge drags resize one side and respect the minimum size."""
    rect = (0.2, 0.2, 0.6, 0.6)
    grown = apply_drag(rect, "e", 0.3, 0.0)
    assert grown == pytest.approx((0.2, 0.2, 0.8, 0.6))
    collapsed = apply_drag(rect, "e", -1.0, 0.0, min_w=0.05)
    assert collapsed[2] == pytest.approx(0.05)


def test_apply_drag_corner_with_ratio() -> None:
    """A corner drag under a fixed aspect keeps that aspect."""
    rect = (0.2, 0.2, 0.6, 0.6)
    out = apply_drag(rect, "se", -0.1, -0.2, ratio=1.5)
    x, y, w, h = out
    assert w / h == pytest.approx(1.5)
    assert (x, y) == (0.2, 0.2)
    assert h == pytest.approx(0.4)


def test_apply_drag_corner_ratio_single_axis() -> None:
    """A purely horizontal corner drag still scales under a ratio."""
    rect = (0.2, 0.2, 0.3, 0.2)
    grown = apply_drag(rect, "se", 0.3, 0.0, ratio=1.5)
    assert grown == pytest.approx((0.2, 0.2, 0.6, 0.4))
    shrunk = apply_drag(rect, "se", -0.15, 0.0, ratio=1.5)
    assert shrunk == pytest.approx((0.2, 0.2, 0.15, 0.1))
    taller = apply_drag(rect, "se", 0.0, 0.2, ratio=1.5)
    assert taller == pytest.approx((0.2, 0.2, 0.6, 0.4))


def test_apply_drag_edge_ratio_survives_the_canvas() -> None:
    """A wide east/west drag caps instead of breaking the ratio."""
    rect = (0.3, 0.35, 0.2, 0.3)
    out = apply_drag(rect, "e", 0.8, 0.0, ratio=2 / 3)
    _x, y, w, h = out
    assert w / h == pytest.approx(2 / 3)
    assert w == pytest.approx(2 / 3)  # capped so h fits exactly
    assert h == pytest.approx(1.0)
    assert y >= 0.0
    west = apply_drag(rect, "w", -0.8, 0.0, ratio=2 / 3)
    assert west[2] / west[3] == pytest.approx(2 / 3)
    tall = apply_drag((0.4, 0.1, 0.45, 0.3), "s", 0.9, 0.9, ratio=2 / 3)
    assert tall[2] / tall[3] == pytest.approx(2 / 3)
    assert tall[2] <= 1.0 and tall[3] <= 1.0


def test_apply_drag_corner_ratio_survives_the_canvas() -> None:
    """Corner drags keep the format when the follower hits the canvas."""
    out = apply_drag((0.4, 0.4, 0.2, 0.3), "se", 0.6, 0.0, ratio=2 / 3)
    x, y, w, h = out
    assert w / h == pytest.approx(2 / 3)
    assert x == pytest.approx(0.4)
    assert x + w == pytest.approx(1.0)
    assert y >= 0.0
    assert y + h <= 1.0 + 1e-9


def test_level_delta() -> None:
    """The straighten delta levels lines to the nearest axis."""
    assert level_delta(100.0, 0.0) == pytest.approx(0.0)
    dy = math.tan(math.radians(3.0)) * 100.0
    assert level_delta(100.0, dy) == pytest.approx(-3.0)
    assert level_delta(100.0, -dy) == pytest.approx(3.0)
    assert abs(level_delta(2.0, 100.0)) < 45.0
    assert level_delta(0.0, 0.0) == 0.0


def test_guide_lines() -> None:
    """Composition guides produce the expected segments."""
    assert guide_lines("None", 300, 200) == []
    assert guide_lines("Bogus", 300, 200) == []
    thirds = guide_lines("Thirds", 300, 200)
    assert len(thirds) == 4
    assert (100.0, 0.0, 100.0, 200.0) in thirds
    assert (0.0, 100.0, 300.0, 100.0) not in thirds  # 2/3 is at ~133
    assert len(guide_lines("Grid", 300, 200)) == 14
    assert len(guide_lines("Center", 300, 200)) == 2
    diagonals = guide_lines("Diagonals", 300, 200)
    assert len(diagonals) == 4
    assert (0.0, 0.0, 200.0, 200.0) in diagonals  # 45 degrees, min side
    golden = guide_lines("Golden", 300, 200)
    xs = sorted({line[0] for line in golden if line[0] == line[2]})
    assert xs[1] / 300 == pytest.approx(0.618, abs=1e-3)
    triangles = guide_lines("Triangles", 300, 200)
    assert len(triangles) == 3
    for x1, y1, x2, y2 in triangles[1:]:
        dot = (x2 - x1) * 300 + (y2 - y1) * 200
        assert dot == pytest.approx(0.0, abs=1e-9)


def test_guide_lines_spiral() -> None:
    """The golden spiral is continuous and stays inside the crop."""
    lines = guide_lines("Spiral", 300, 200)
    assert len(lines) > 100
    eps = 1e-6
    for (_, _, x2, y2), (nx1, ny1, _, _) in itertools.pairwise(lines):
        assert abs(x2 - nx1) < eps
        assert abs(y2 - ny1) < eps
    for x1, y1, x2, y2 in lines:
        for px, py in ((x1, y1), (x2, y2)):
            assert -eps <= px <= 300 + eps
            assert -eps <= py <= 200 + eps
    assert lines[0][0] == pytest.approx(0.0)
    assert lines[0][1] == pytest.approx(200.0)


def test_guide_lines_spiral_portrait_and_mirror() -> None:
    """Portrait crops get a transposed spiral; flips mirror it."""
    eps = 1e-6
    portrait = guide_lines("Spiral", 200, 300)
    assert len(portrait) > 100
    for x1, y1, x2, y2 in portrait:
        for px, py in ((x1, y1), (x2, y2)):
            assert -eps <= px <= 200 + eps
            assert -eps <= py <= 300 + eps
    assert portrait[0][0] == pytest.approx(200.0)
    assert portrait[0][1] == pytest.approx(0.0)
    mirrored = guide_lines("Spiral", 300, 200, flip_h=True)
    assert mirrored[0][0] == pytest.approx(300.0)
    assert mirrored[0][1] == pytest.approx(200.0)
    flipped = guide_lines("Spiral", 300, 200, flip_v=True)
    assert flipped[0][1] == pytest.approx(0.0)


class TestReframePoint:
    """Mapping crop-frame points across rotation states."""

    def test_identity_without_rotation(self):
        """Full rect, no angle: frame coords are box coords."""
        got = crop.reframe_point(
            (0.25, 0.75), 400, 300, 0.0, crop.FULL_RECT, 0.0
        )
        assert got == pytest.approx((0.25, 0.75))

    def test_center_is_invariant(self):
        """The image center maps to the box center at any angle."""
        got = crop.reframe_point(
            (0.5, 0.5), 400, 300, 0.0, crop.FULL_RECT, 7.5
        )
        assert got == pytest.approx((0.5, 0.5))

    def test_roundtrip_through_rotation(self):
        """Mapping there and back with full rects is the identity."""
        there = crop.reframe_point(
            (0.2, 0.4), 400, 300, 3.0, crop.FULL_RECT, -5.0
        )
        back = crop.reframe_point(there, 400, 300, -5.0, crop.FULL_RECT, 3.0)
        assert back == pytest.approx((0.2, 0.4), abs=1e-9)

    def test_crop_offset_applies(self):
        """A point in a sub-rect lands inside that rect's box region."""
        rect = (0.25, 0.25, 0.5, 0.5)
        got = crop.reframe_point((0.0, 0.0), 400, 300, 0.0, rect, 0.0)
        assert got == pytest.approx((0.25, 0.25))
        got = crop.reframe_point((1.0, 1.0), 400, 300, 0.0, rect, 0.0)
        assert got == pytest.approx((0.75, 0.75))
