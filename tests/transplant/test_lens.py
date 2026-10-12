"""Tests for the donor lens correction from the RAF's own tables."""

import io

import numpy as np
import pytest
from PIL import Image

from grawji.transplant import embed, lens, output, pipeline
from grawji.transplant.embed import Transplant, TransplantError

# The X-E5's XF23mmF2.8 R WR tables.
_KNOTS = (0.3535, 0.5, 0.6127, 0.707, 0.7908, 0.8662, 0.9352, 1.0, 1.0606)
_DISTORTION = (-0.32, -0.76, -1.21, -1.65, -2.09, -2.55, -3.03, -3.55, -4.07)
_VIGNETTING = (94.55, 88.24, 80.41, 73.75, 67.86, 62.74, 58.45, 53.74, 53.74)
_ZERO = (0.0,) * 9
_XF23 = lens.LensTables(
    knots=_KNOTS,
    distortion=_DISTORTION,
    vignetting=_VIGNETTING,
    chromatic_red=_ZERO,
    chromatic_blue=_ZERO,
)
_LENSLESS = lens.LensTables(
    knots=_KNOTS,
    distortion=_ZERO,
    vignetting=(100.0,) * 9,
    chromatic_red=_ZERO,
    chromatic_blue=_ZERO,
)


def _png(image) -> bytes:
    """Encode a PIL image losslessly for deterministic pixel checks."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _raw_tables(knots, distortion, vignetting, chromatic=None):
    """The three tables the way imagemeta hands them over: floats."""
    chromatic = chromatic or (0.0,) * len(knots)
    return (
        [4643 / 9, *knots, *distortion],
        [4643 / 9, *knots, *chromatic, *chromatic],
        [4643 / 9, *knots, *vignetting],
    )


def _fake_tables(monkeypatch, tables: dict[str, object]) -> None:
    """Stub the metadata read with per-path raw tables (None = absent)."""
    monkeypatch.setattr(
        lens.imagemeta, "fuji_lens_tables", lambda path: tables[path]
    )


def _xf23_raw():
    return _raw_tables(_KNOTS, _DISTORTION, _VIGNETTING)


def test_lens_tables_parse_the_three_tags(monkeypatch):
    """Knots and values come out of the rational strings as floats."""
    _fake_tables(monkeypatch, {"donor.RAF": _xf23_raw()})
    tables = lens.lens_tables("donor.RAF")
    assert tables is not None
    assert tables.knots == _KNOTS
    assert tables.distortion == _DISTORTION
    assert tables.vignetting == _VIGNETTING
    assert tables.chromatic_red == _ZERO
    assert tables.corrects


def test_lens_tables_refuse_an_unknown_layout(monkeypatch):
    """A table too short for its knot count is an error, not a guess."""
    short = ([4643 / 9, 0.5, 1.0, -1.0, -2.0], [4643 / 9], [4643 / 9])
    _fake_tables(monkeypatch, {"odd.RAF": short})
    with pytest.raises(TransplantError, match="unknown layout"):
        lens.lens_tables("odd.RAF")


def test_lens_tables_are_none_without_the_tags(monkeypatch):
    """A RAF without correction tables yields None, not an error."""
    _fake_tables(monkeypatch, {"other.RAF": None})
    assert lens.lens_tables("other.RAF") is None


def test_a_lensless_shot_carries_tables_that_correct_nothing():
    """Zero distortion and full transmission count as no correction."""
    assert not _LENSLESS.corrects


def test_vignetting_gain_is_unity_at_the_center_and_grows_outward():
    """100 over the transmission, with full transmission at the center."""
    gain = lens.vignetting_gain(_XF23, 101, 151)
    assert gain[50, 75] == pytest.approx(1.0)
    assert gain[0, 0] > gain[25, 37] > gain[50, 75]
    # at the corner the radius is 1.01 (RADIUS_SCALE), past the last knot
    assert gain[0, 0] == pytest.approx(100 / _VIGNETTING[-1], rel=1e-3)


def test_vignetting_gain_of_a_lensless_shot_is_one_everywhere():
    """Full transmission at every knot leaves the frame untouched."""
    gain = lens.vignetting_gain(_LENSLESS, 40, 60)
    assert np.allclose(gain, 1.0)


def test_distortion_pulls_the_corners_inward():
    """Barrel values below zero sample the corners from inside."""
    r = np.array([0.0, 0.5, 1.0])
    factor = lens.distortion_factor(_XF23, r)
    assert factor[0] == pytest.approx(
        (1 + _DISTORTION[0] / 100) * lens.FILL_SCALE
    )
    assert factor[2] < factor[1] < factor[0]
    assert factor[2] < 1.0


def test_distortion_maps_keep_the_center_in_place():
    """The center pixel samples itself; a corner samples inside."""
    map_x, map_y = lens.distortion_maps(_XF23, 41, 61)
    assert map_x.dtype == np.float32
    assert map_x[20, 30] == pytest.approx(30.0, abs=1e-3)
    assert map_y[20, 30] == pytest.approx(20.0, abs=1e-3)
    assert 0 < map_x[0, 0] < 30
    assert 0 < map_y[0, 0] < 20


def test_undo_vignetting_scales_only_the_visible_area():
    """The gain applies to black-subtracted visible pixels, not margins."""
    pixels = np.full((13, 17), 1022 + 1000, dtype=np.uint16)
    out = embed.undo_vignetting(pixels, 1022, (2, 0, 11, 17), _XF23)
    assert out.dtype == np.uint16
    assert np.all(out[:2] == 2022)  # the top margin stays
    assert out[2 + 5, 8] == 2022  # the exact center, gain 1
    assert out[2, 0] > out[2 + 5, 8]  # a corner is lifted


def test_undo_vignetting_clips_at_the_ceiling():
    """A corner that would exceed 14 bits clips instead of wrapping."""
    pixels = np.full((12, 16), 16000, dtype=np.uint16)
    out = embed.undo_vignetting(pixels, 1022, (0, 0, 12, 16), _XF23)
    assert out.max() == 16383


def test_donor_correction_rejects_a_lensed_carrier(monkeypatch):
    """A carrier shot through a lens is refused.

    The engine keys its correction on the carrier's lens ID (hardware
    verified on the X-E5), so a lensed carrier arrives pre-corrected
    and this module would double-correct it.
    """
    _fake_tables(
        monkeypatch, {"donor.RAF": _xf23_raw(), "carrier.RAF": _xf23_raw()}
    )
    with pytest.raises(TransplantError, match="without a lens"):
        lens.donor_correction_from_paths("donor.RAF", "carrier.RAF")


def test_donor_correction_accepts_a_lensless_carrier(monkeypatch):
    """A carrier whose tables correct nothing passes the donor's tables."""
    lensless = _raw_tables(_KNOTS, _ZERO, (100.0,) * 9)
    _fake_tables(
        monkeypatch, {"donor.RAF": _xf23_raw(), "carrier.RAF": lensless}
    )
    tables = lens.donor_correction_from_paths("donor.RAF", "carrier.RAF")
    assert tables.distortion == _DISTORTION


def test_donor_correction_requires_donor_tables(monkeypatch):
    """A donor without tables is refused."""
    _fake_tables(monkeypatch, {"donor.RAF": None, "carrier.RAF": None})
    with pytest.raises(TransplantError, match="no lens correction"):
        lens.donor_correction_from_paths("donor.RAF", "carrier.RAF")


def test_apply_donor_correction_keeps_size_and_moves_the_corners():
    """The remap keeps the frame size and fills the corners from inside."""
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 255, (64, 96, 3), dtype=np.uint8)
    pixels[:8, :8] = 255  # a bright block in the corner
    image = Image.fromarray(pixels, "RGB")
    corrected = lens.apply_donor_correction(image, _XF23, compensate=False)
    assert corrected.size == image.size
    out = np.asarray(corrected)
    assert out[8, 8].mean() > pixels[8, 8].mean() or out[0, 0].mean() == 255


def test_donor_correction_compensation_sharpens():
    """The default MTF compensation adds fine contrast over plain."""
    rng = np.random.default_rng(7)
    pixels = rng.integers(60, 196, (64, 64, 3), dtype=np.uint8)
    image = Image.fromarray(pixels, "RGB")

    def contrast(im):
        c = np.asarray(im.convert("L"), dtype=np.float32)
        lap = (
            c[1:-1, 1:-1] * 4
            - c[:-2, 1:-1]
            - c[2:, 1:-1]
            - c[1:-1, :-2]
            - c[1:-1, 2:]
        )
        return lap.var()

    plain = lens.apply_donor_correction(image, _XF23, compensate=False)
    comp = lens.apply_donor_correction(image, _XF23)
    assert contrast(comp) > contrast(plain)


def test_finish_jpeg_applies_donor_correction_after_the_crop(monkeypatch):
    """The correction runs on the cropped donor region, not the frame."""
    calls = []

    def record(image, tables, *, compensate=True):
        calls.append((image.size, tables, compensate))
        return image

    monkeypatch.setattr(lens, "apply_donor_correction", record)
    image = Image.new("RGB", (120, 120), (200, 30, 30))
    output.finish_jpeg(
        _png(image),
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        donor_lens=_XF23,
    )
    assert calls == [((60, 60), _XF23, True)]  # crop size, not 120


def test_finish_jpeg_can_skip_the_compensation(monkeypatch):
    """compensate=False reaches the correction (the preview path)."""
    calls = []

    def record(image, tables, *, compensate=True):
        calls.append(compensate)
        return image

    monkeypatch.setattr(lens, "apply_donor_correction", record)
    image = Image.new("RGB", (40, 40), (10, 200, 10))
    output.finish_jpeg(
        _png(image),
        flip=0,
        donor_size=(4, 4),
        carrier_size=(4, 4),
        donor_lens=_XF23,
        compensate=False,
    )
    assert calls == [False]


def test_finish_jpeg_without_donor_lens_skips_correction(monkeypatch):
    """No donor lens means the correction is never invoked."""
    called = []
    monkeypatch.setattr(
        lens, "apply_donor_correction", lambda *a, **k: called.append(1)
    )
    image = Image.new("RGB", (40, 40), (10, 10, 200))
    output.finish_jpeg(
        _png(image), flip=0, donor_size=(4, 4), carrier_size=(4, 4)
    )
    assert called == []


class _FakeCamera:
    """The camera protocol with canned results."""

    def __init__(self, jpeg: bytes) -> None:
        self._jpeg = jpeg
        self.calls: list[str] = []

    def send_raf(self, filepath):
        self.calls.append("send_raf")

    def get_profile(self):
        self.calls.append("get_profile")
        return bytes(629)

    def set_profile(self, profile):
        self.calls.append("set_profile")

    def trigger_conversion(self, full_resolution=True):
        self.calls.append("trigger")

    def wait_for_result(self, timeout=300, thumbnail=False):
        self.calls.append("wait")
        return self._jpeg


def test_develop_threads_donor_lens_to_finish(tmp_path, monkeypatch):
    """RenderOptions.donor_lens reaches finish_jpeg for the correction."""
    captured = {}

    def capture(jpeg, **kwargs):
        captured.update(kwargs)
        return b"finished"

    monkeypatch.setattr(pipeline, "finish_jpeg", capture)
    camera = _FakeCamera(_png(Image.new("RGB", (60, 60))))
    transplant = Transplant(
        raf=b"x",
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        offset=(0, 0),
    )

    pipeline.develop(
        camera,
        transplant,
        tmp_path,
        pipeline.RenderOptions(donor_lens=_XF23),
    )

    assert captured["donor_lens"] is _XF23
    assert captured["compensate"] is True  # full_resolution default


def test_develop_preview_skips_the_compensation(tmp_path, monkeypatch):
    """A preview render threads compensate=False to finish_jpeg."""
    captured = {}

    def capture(jpeg, **kwargs):
        captured.update(kwargs)
        return b"finished"

    monkeypatch.setattr(pipeline, "finish_jpeg", capture)
    camera = _FakeCamera(_png(Image.new("RGB", (60, 60))))
    transplant = Transplant(
        raf=b"x",
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        offset=(0, 0),
    )

    pipeline.develop(
        camera,
        transplant,
        tmp_path,
        pipeline.RenderOptions(donor_lens=_XF23, full_resolution=False),
    )

    assert captured["compensate"] is False
