"""Tests for the ashift-derived keystone fit."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from grawji import keystone

WIDTH, HEIGHT = 1200, 800


@dataclass(frozen=True)
class Line:
    """A synthetic segment, matching lsdetect's shape."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 1.5
    precision: float = 0.125

    @property
    def angle(self) -> float:
        """Direction in degrees, -90 to 90, 0 = horizontal."""
        raw = math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1))
        if raw > 90.0:
            raw -= 180.0
        if raw <= -90.0:
            raw += 180.0
        return raw

    @property
    def length(self) -> float:
        """Length in pixels."""
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


def apply_homography(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Send (x, y) points through a homography, plain coordinates."""
    homo = np.column_stack([points, np.ones(len(points))])
    warped = homo @ matrix.T
    return np.column_stack(
        [warped[:, 0] / warped[:, 2], warped[:, 1] / warped[:, 2]]
    )


def distorted(lines: list[Line], correction: keystone.Keystone) -> list[Line]:
    """Lines as a camera producing this correction would record them."""
    warp = np.linalg.inv(keystone.homography(correction, WIDTH, HEIGHT))
    out = []
    for line in lines:
        ends = np.array([[line.x1, line.y1], [line.x2, line.y2]])
        moved = apply_homography(warp, ends)
        out.append(Line(moved[0, 0], moved[0, 1], moved[1, 0], moved[1, 1]))
    return out


def verticals() -> list[Line]:
    """A fence of upright lines across the frame."""
    return [Line(x, 100.0, x, 700.0) for x in np.linspace(150.0, 1050.0, 12)]


def horizontals() -> list[Line]:
    """Level lines across the frame."""
    return [Line(100.0, y, 1100.0, y) for y in np.linspace(150.0, 650.0, 9)]


def test_homography_rotation_levels_a_leaning_line() -> None:
    """The homography rotation matches a plain rotation for shifts 0."""
    m = keystone.homography(keystone.Keystone(rotation=-6.0), WIDTH, HEIGHT)
    lean = 800.0 * math.tan(math.radians(-6.0))
    p = np.array([[200.0, 400.0], [1000.0, 400.0 + lean]])
    q = apply_homography(m, p)
    ang = math.degrees(math.atan2(q[1, 1] - q[0, 1], q[1, 0] - q[0, 0]))
    assert abs(ang) < 0.01


def test_fit_recovers_rotation_and_shifts() -> None:
    """A known keystone applied to straight lines is recovered."""
    truth = keystone.Keystone(
        rotation=-5.0, lensshift_v=-0.4, lensshift_h=0.15
    )
    lines = distorted(verticals() + horizontals(), truth)
    report = keystone.fit(lines, WIDTH, HEIGHT, mode="both")
    assert report is not None
    assert report.keystone.rotation == pytest.approx(-5.0, abs=0.3)
    assert report.keystone.lensshift_v == pytest.approx(-0.4, abs=0.05)
    assert report.keystone.lensshift_h == pytest.approx(0.15, abs=0.05)
    assert report.deviation_after < 0.3


def test_vertical_mode_uses_only_verticals() -> None:
    """Vertical mode fits rotation and the vertical shift."""
    truth = keystone.Keystone(rotation=-3.0, lensshift_v=-0.3)
    lines = distorted(verticals(), truth)
    report = keystone.fit(lines, WIDTH, HEIGHT, mode="vertical")
    assert report is not None
    assert report.keystone.lensshift_v == pytest.approx(-0.3, abs=0.05)


def test_straight_lines_need_no_correction() -> None:
    """A frame that is already straight fits to near-neutral."""
    report = keystone.fit(verticals() + horizontals(), WIDTH, HEIGHT)
    assert report is not None
    assert abs(report.keystone.rotation) < 0.3
    assert abs(report.keystone.lensshift_v) < 0.03
    assert abs(report.keystone.lensshift_h) < 0.03


def test_too_few_lines_yield_no_fit() -> None:
    """Two lines are not evidence; the fit declines."""
    lines = [
        Line(300.0, 100.0, 310.0, 700.0),
        Line(600.0, 100.0, 590.0, 700.0),
    ]
    assert keystone.fit(lines, WIDTH, HEIGHT) is None


def test_pure_clutter_is_rejected() -> None:
    """Randomly angled short lines give no reliable correction."""
    rng = np.random.default_rng(4)
    lines = []
    for _ in range(40):
        x, y = rng.uniform(100, 1100), rng.uniform(100, 700)
        ang = math.radians(rng.uniform(0, 180))
        lines.append(
            Line(x, y, x + 40 * math.cos(ang), y + 40 * math.sin(ang))
        )
    assert keystone.fit(lines, WIDTH, HEIGHT) is None


def test_neutral_keystone_reports_neutral() -> None:
    """The is_neutral flag matches an all-zero correction."""
    assert keystone.Keystone().is_neutral
    assert not keystone.Keystone(lensshift_v=0.2).is_neutral


def test_homography_is_resolution_independent() -> None:
    """The same correction straightens a half-size rendering too."""
    truth = keystone.Keystone(rotation=-5.0, lensshift_v=-0.3)
    lines = distorted(verticals(), truth)
    report = keystone.fit(lines, WIDTH, HEIGHT)
    assert report is not None
    fitted = report.keystone
    m_full = keystone.homography(fitted, WIDTH, HEIGHT)
    m_half = keystone.homography(fitted, WIDTH // 2, HEIGHT // 2)
    p_full = apply_homography(m_full, np.array([[600.0, 400.0]]))
    p_half = apply_homography(m_half, np.array([[300.0, 200.0]]))
    assert p_full[0, 0] / 2 == pytest.approx(p_half[0, 0], abs=1.0)
    assert p_full[0, 1] / 2 == pytest.approx(p_half[0, 1], abs=1.0)


def test_auto_crop_hides_the_warp_corners() -> None:
    """Every corner of the auto-crop lies inside the warped image."""
    fitted = keystone.Keystone(rotation=-5.0, lensshift_v=-0.3)
    x, y, w, h = keystone.auto_crop(fitted, WIDTH, HEIGHT)
    assert 0.0 < w < 1.0
    assert 0.0 < h < 1.0
    quad = keystone.valid_quad(fitted, WIDTH, HEIGHT)
    assert keystone.rect_in_quad((x, y, w, h), quad)


def test_rotated_keystone_crop_stays_in_quad() -> None:
    """A 90-degree turn keeps the auto crop inside the warp region."""
    from grawji.crop import rotate_rect_90

    fitted = keystone.Keystone(
        rotation=5.5, lensshift_v=-0.02, lensshift_h=0.23
    )
    rect = keystone.auto_crop(fitted, WIDTH, HEIGHT)
    quad = keystone.valid_quad(fitted, WIDTH, HEIGHT)
    for degrees in (0, 90, 180, 270):
        turned_rect = rotate_rect_90(rect, degrees)
        turned_quad = keystone.rotate_quad(quad, degrees)
        assert keystone.rect_in_quad(turned_rect, turned_quad)


def test_centre_fit_keeps_a_portrait_swap_inside_the_quad() -> None:
    """A portrait rect shrunk toward its center clears the warp corners."""
    fitted = keystone.Keystone(
        rotation=5.5, lensshift_v=-0.02, lensshift_h=0.23
    )
    quad = keystone.valid_quad(fitted, WIDTH, HEIGHT)
    portrait = (0.26, 0.0, 0.41, 1.0)
    assert not keystone.rect_in_quad(portrait, quad)
    cx = portrait[0] + portrait[2] / 2.0
    cy = portrait[1] + portrait[3] / 2.0

    low, high = 0.0, 1.0
    for _ in range(40):
        mid = (low + high) / 2.0
        blended = (
            cx + (portrait[0] - cx) * mid,
            cy + (portrait[1] - cy) * mid,
            portrait[2] * mid,
            portrait[3] * mid,
        )
        if keystone.rect_in_quad(blended, quad):
            low = mid
        else:
            high = mid
    fit = (
        cx + (portrait[0] - cx) * low,
        cy + (portrait[1] - cy) * low,
        portrait[2] * low,
        portrait[3] * low,
    )
    assert keystone.rect_in_quad(fit, quad)
    assert fit[2] / fit[3] == pytest.approx(portrait[2] / portrait[3])


def test_slide_into_quad_slides_without_snapping_back() -> None:
    """Moving a crop past the warped edge slides it in, keeping size."""
    fitted = keystone.Keystone(
        rotation=5.5, lensshift_v=-0.02, lensshift_h=0.23
    )
    quad = keystone.valid_quad(fitted, WIDTH, HEIGHT)
    inside = (0.35, 0.30, 0.30, 0.35)
    assert keystone.slide_into_quad(inside, quad) == inside
    shoved = (0.95, 0.30, 0.30, 0.35)
    slid = keystone.slide_into_quad(shoved, quad)
    assert keystone.rect_in_quad(slid, quad)
    assert slid[2] == pytest.approx(0.30)
    assert slid[3] == pytest.approx(0.35)
    assert slid[0] < shoved[0]


def test_auto_crop_without_warp_is_full() -> None:
    """No correction, no crop."""
    assert keystone.auto_crop(keystone.Keystone(), WIDTH, HEIGHT) == (
        0.0,
        0.0,
        1.0,
        1.0,
    )


def test_warp_with_neutral_keystone_is_identity() -> None:
    """A neutral correction returns the pixels unchanged."""
    gi = pytest.importorskip("gi")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    from grawji.imaging.perspective import warp_pixbuf

    rng = np.random.default_rng(3)
    pixels = rng.integers(0, 255, (40, 60, 3), dtype=np.uint8)
    source = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(pixels.tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        60,
        40,
        180,
    )
    warped = warp_pixbuf(source, keystone.Keystone())
    assert warped.get_pixels() == source.get_pixels()


def test_warp_cache_returns_the_same_object() -> None:
    """The cached warp is reused for the same pixbuf and correction."""
    gi = pytest.importorskip("gi")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    from grawji.imaging import perspective

    pixels = np.zeros((20, 30, 3), dtype=np.uint8)
    source = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(pixels.tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        30,
        20,
        90,
    )
    correction = keystone.Keystone(rotation=-3.0, lensshift_v=0.1)
    assert not perspective.is_cached(source, correction)
    first = perspective.warp_cached(source, correction)
    assert perspective.is_cached(source, correction)
    assert perspective.warp_cached(source, correction) is first


def test_bake_applies_the_warp() -> None:
    """A CropRotate with a keystone warps."""
    gi = pytest.importorskip("gi")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    from grawji.crop import CropRotate
    from grawji.imaging.render import bake_pixbuf

    rng = np.random.default_rng(9)
    pixels = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    source = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(pixels.tobytes()),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        80,
        60,
        240,
    )
    plain = bake_pixbuf(source, CropRotate())
    assert plain.get_pixels() == source.get_pixels()
    warped = bake_pixbuf(source, CropRotate(lensshift_v=0.3))
    assert warped.get_width() == 80
    assert warped.get_height() == 60
    assert warped.get_pixels() != source.get_pixels()


def test_crop_dict_round_trips_the_keystone() -> None:
    """Sidecar storage keeps the four params, and scrubs nonsense."""
    from grawji.crop import MAX_SHIFT, CropRotate

    stored = CropRotate(
        keystone_rotation=-5.0,
        lensshift_v=0.2,
        lensshift_h=-0.05,
        shear=0.03,
    ).to_dict()
    loaded = CropRotate.from_dict(stored)
    assert loaded.keystone_rotation == pytest.approx(-5.0)
    assert loaded.lensshift_v == pytest.approx(0.2)
    assert loaded.lensshift_h == pytest.approx(-0.05)
    assert loaded.shear == pytest.approx(0.03)
    assert loaded.has_warp
    assert not CropRotate().has_warp
    wild = CropRotate.from_dict({"lensshift_v": 9.0})
    assert wild.lensshift_v == pytest.approx(MAX_SHIFT)
    broken = CropRotate.from_dict({"lensshift_v": "x"})
    assert not broken.has_warp
