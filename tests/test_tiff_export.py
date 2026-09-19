"""Tests for the TIFF reader, writer and export plumbing."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from grawji.camera import capabilities, core
from grawji.crop import CropRotate
from grawji.imaging import export, tiff
from tests.image_support import tiff_bytes


def sample_frame(width=7, height=5, bits=16):
    """A small gradient frame at the given depth, as uint16 samples."""
    top = (1 << bits) - 1
    ys, xs = np.mgrid[0:height, 0:width]
    frame = np.dstack(
        [
            (xs * top // max(1, width - 1)),
            (ys * top // max(1, height - 1)),
            np.full_like(xs, top // 2),
        ]
    )
    return frame.astype(np.uint16)


def written(tmp_path, samples, bits):
    """Write samples as a TIFF and hand back the bytes."""
    path = tmp_path / "frame.tif"
    tiff.encode(samples, str(path), bits=bits)
    return path.read_bytes()


@pytest.mark.parametrize("bits", [8, 16])
def test_round_trip_is_exact(tmp_path, bits):
    """What the writer puts down the reader gets back unchanged."""
    samples = sample_frame(bits=bits)
    decoded = tiff.decode(written(tmp_path, samples, bits))
    assert decoded.bits == bits
    assert np.array_equal(decoded.samples, samples)
    assert decoded.samples.dtype == np.uint16


@pytest.mark.parametrize("bits", [8, 16])
def test_file_size_is_header_plus_one_strip(tmp_path, bits):
    """No padding and no second copy of the pixels."""
    samples = sample_frame(bits=bits)
    data = written(tmp_path, samples, bits)
    pixels = samples.size * (bits // 8)
    assert len(data) == len(tiff.header(7, 5, bits)) + pixels


def test_is_tiff_accepts_both_byte_orders():
    """Either byte-order mark opens a TIFF, anything else does not."""
    assert tiff.is_tiff(b"II\x2a\x00" + bytes(8))
    assert tiff.is_tiff(b"MM\x00\x2a" + bytes(8))
    assert not tiff.is_tiff(b"\xff\xd8\xff\xe1" + bytes(8))
    assert not tiff.is_tiff(b"II\x2b\x00" + bytes(8))
    assert not tiff.is_tiff(b"")


def test_is_complete_rejects_a_truncated_strip(tmp_path):
    """A download that stopped mid-strip is not a usable file."""
    data = written(tmp_path, sample_frame(), 16)
    assert tiff.is_complete(data)
    assert not tiff.is_complete(data[: len(data) - 10])
    assert not tiff.is_complete(b"nonsense")


def test_decode_refuses_what_the_engine_never_writes(tmp_path):
    """Compressed, planar or non-RGB files are refused, not guessed."""
    data = bytearray(written(tmp_path, sample_frame(), 16))
    # Compression sits in the fourth entry of the IFD written above.
    start = struct.unpack_from("<I", data, 4)[0]
    for index in range(struct.unpack_from("<H", data, start)[0]):
        entry = start + 2 + index * 12
        if struct.unpack_from("<H", data, entry)[0] == 259:
            struct.pack_into("<H", data, entry + 8, 5)
    with pytest.raises(tiff.TiffError, match="compressed"):
        tiff.decode(bytes(data))


def test_decode_refuses_an_odd_bit_depth(tmp_path):
    """Only the depths the engine produces are accepted."""
    data = bytearray(written(tmp_path, sample_frame(), 16))
    start = struct.unpack_from("<I", data, 4)[0]
    for index in range(struct.unpack_from("<H", data, start)[0]):
        entry = start + 2 + index * 12
        if struct.unpack_from("<H", data, entry)[0] == 258:
            at = struct.unpack_from("<I", data, entry + 8)[0]
            struct.pack_into("<3H", data, at, 12, 12, 12)
    with pytest.raises(tiff.TiffError, match="bits per sample"):
        tiff.decode(bytes(data))


def test_encode_refuses_an_unsupported_depth(tmp_path):
    """Nothing but 8 and 16 bits reaches the file."""
    with pytest.raises(tiff.TiffError, match="cannot write"):
        tiff.encode(sample_frame(), str(tmp_path / "x.tif"), bits=12)


def test_file_type_codes_reach_the_profile_slot():
    """Both TIFF depths land in profile slot 1 at offset 517."""
    base = bytes(632)
    assert (
        struct.unpack_from("<I", core.apply_file_type(base, "tiff8"), 517)[0]
        == 9
    )
    assert (
        struct.unpack_from("<I", core.apply_file_type(base, "tiff16"), 517)[0]
        == 11
    )


def test_camera_file_type_and_suffixes():
    """TIFF is rendered by the camera and named .tif at both depths."""
    assert export.camera_file_type("tiff8") == "tiff8"
    assert export.camera_file_type("tiff16") == "tiff16"
    assert export.camera_file_type("jxl") == "jpeg"
    assert export.format_suffix("tiff8") == ".tif"
    assert export.format_suffix("tiff16") == ".tif"


def test_delivered_format_notices_a_jpeg_fallback(tmp_path):
    """A body that cannot write TIFF quietly sends JPEG instead."""
    data = written(tmp_path, sample_frame(), 16)
    assert export.delivered_format(data, "tiff16") == "tiff16"
    assert export.delivered_format(b"\xff\xd8\xff\xe1jpeg", "tiff16") == "jpeg"


def test_available_formats_follow_the_body():
    """The engine's own file types gate what the export offers."""
    x100f = capabilities.capabilities_for_model("X100F")
    assert "tiff8" not in export.available_formats(x100f)
    assert "tiff16" not in export.available_formats(x100f)
    gfx50s = capabilities.capabilities_for_model("GFX 50S")
    assert "tiff8" in export.available_formats(gfx50s)
    assert "tiff16" not in export.available_formats(gfx50s)
    xe5 = capabilities.capabilities_for_model("X-E5")
    assert {"tiff8", "tiff16"} <= set(export.available_formats(xe5))


def test_passthrough_writes_the_camera_bytes(tmp_path):
    """An untouched TIFF export is the camera's own file."""
    data = written(tmp_path, sample_frame(), 16)
    out = tmp_path / "out.tif"
    export.write_passthrough(data, str(out), fmt="tiff16")
    assert out.read_bytes() == data


def test_passthrough_refuses_a_truncated_download(tmp_path):
    """Half a TIFF is not written out as if it were whole."""
    data = written(tmp_path, sample_frame(), 16)
    with pytest.raises(OSError, match="incomplete"):
        export.write_passthrough(
            data[: len(data) - 20], str(tmp_path / "out.tif"), fmt="tiff16"
        )


@pytest.mark.parametrize("bits", [8, 16])
def test_write_tiff_keeps_the_depth_through_a_crop(tmp_path, bits):
    """An edited export is re-encoded at the depth it arrived in."""
    samples = sample_frame(width=8, height=6, bits=bits)
    data = written(tmp_path, samples, bits)
    out = tmp_path / "edited.tif"
    export.write_tiff(
        data,
        str(out),
        geometry=export.Geometry(
            crop=CropRotate(rect=(0.0, 0.0, 0.5, 1.0)),
        ),
    )
    decoded = tiff.decode(out.read_bytes())
    assert decoded.bits == bits
    assert decoded.samples.shape[:2] == (6, 4)
    assert np.array_equal(decoded.samples, samples[:, :4])


def test_write_jpeg_routes_tiff_to_the_tiff_writer(tmp_path):
    """The shared entry point dispatches on the format."""
    data = written(tmp_path, sample_frame(), 16)
    out = tmp_path / "routed.tif"
    export.write_jpeg(
        data,
        str(out),
        quality=95,
        decode=lambda _: pytest.fail("the pixbuf path must not run"),
        fmt="tiff16",
        geometry=export.Geometry(crop=CropRotate()),
    )
    assert tiff.is_tiff(out.read_bytes())


def test_write_jpeg_needs_a_geometry_for_tiff(tmp_path):
    """Without a geometry there is nothing to bake, so it is refused."""
    with pytest.raises(ValueError, match="TIFF export needs"):
        export.write_jpeg(
            b"",
            str(tmp_path / "x.tif"),
            quality=95,
            decode=lambda _: None,
            fmt="tiff16",
        )


def test_scales_quality_knows_which_formats_care():
    """TIFF is uncompressed, so the quality setting means nothing."""
    assert export.scales_quality("jpeg")
    assert export.scales_quality("jxl")
    assert export.scales_quality("jxl16")
    assert export.scales_quality("heif")
    assert not export.scales_quality("tiff8")
    assert not export.scales_quality("tiff16")


def test_jxl16_is_rendered_as_tiff_and_named_jxl():
    """The camera sends 16-bit TIFF, the host packs it into JXL."""
    assert export.camera_file_type("jxl16") == "tiff16"
    assert export.format_suffix("jxl16") == ".jxl"


def test_jxl16_needs_a_capable_tool_and_the_body(monkeypatch):
    """A too-old cjxl or a body without 16-bit TIFF means no 16-bit JXL."""
    xe5 = capabilities.capabilities_for_model("X-E5")
    monkeypatch.setattr(export, "jxl16_available", lambda: True)
    assert "jxl16" in export.available_formats(xe5)
    monkeypatch.setattr(export, "jxl16_available", lambda: False)
    assert "jxl16" not in export.available_formats(xe5)
    monkeypatch.setattr(export, "jxl16_available", lambda: True)
    gfx50s = capabilities.capabilities_for_model("GFX 50S")
    assert "jxl16" not in export.available_formats(gfx50s)


def test_a_too_old_cjxl_still_leaves_plain_jxl(monkeypatch):
    """Only the 16-bit path needs the newer flags, the repack does not."""
    xe5 = capabilities.capabilities_for_model("X-E5")
    monkeypatch.setattr(export, "jxl_available", lambda: True)
    monkeypatch.setattr(export, "jxl16_available", lambda: False)
    formats = export.available_formats(xe5)
    assert "jxl" in formats
    assert "jxl16" not in formats
    assert "a newer cjxl for 16-bit JPEG XL" in export.missing_tools(formats)


def test_jxl16_notices_a_jpeg_fallback():
    """A body that ignored the TIFF request sent a JPEG instead."""
    assert export.delivered_format(b"\xff\xd8\xff\xe1jpeg", "jxl16") == "jpeg"


@pytest.mark.skipif(
    not export.jxl16_available(), reason="cjxl is too old or missing"
)
def test_jxl16_round_trips_through_cjxl(tmp_path):
    """A 16-bit TIFF becomes a 16-bit JXL of the cropped size."""
    samples = sample_frame(width=64, height=48, bits=16)
    data = written(tmp_path, samples, 16)
    out = tmp_path / "packed.jxl"
    export.write_jxl16(
        data,
        str(out),
        quality=100,
        geometry=export.Geometry(crop=CropRotate(rect=(0.0, 0.0, 0.5, 1.0))),
    )
    assert out.stat().st_size > 0
    assert out.read_bytes()[:2] != b"II"


@pytest.mark.skipif(
    not export.jxl16_available(), reason="cjxl is too old or missing"
)
def test_jxl16_passthrough_encodes_instead_of_copying(tmp_path):
    """There is no passing a TIFF through as a JXL, so it is encoded."""
    data = written(tmp_path, sample_frame(width=32, height=24), 16)
    out = tmp_path / "pass.jxl"
    export.write_passthrough(data, str(out), fmt="jxl16", quality=90)
    assert not tiff.is_tiff(out.read_bytes())
    assert out.stat().st_size > 0


def test_every_format_has_a_label_and_a_title():
    """A format without UI strings would crash the dialog on open."""
    from grawji.controllers.exports import _EXPORT_TITLES
    from grawji.views.preferences import _FORMAT_LABELS

    for fmt in export.FORMATS:
        assert fmt in _FORMAT_LABELS, fmt
        assert fmt in _EXPORT_TITLES, fmt
        assert fmt in export._SUFFIXES, fmt


def test_available_formats_only_ever_offers_known_formats():
    """Nothing reaches the dropdown that the rest cannot handle."""
    for model in ("X-E5", "GFX 50S", "X-Pro3", "X100V", "X-T3"):
        caps = capabilities.capabilities_for_model(model)
        assert set(export.available_formats(caps)) <= set(export.FORMATS)


def test_the_quality_row_follows_the_chosen_format():
    """Picking TIFF hides the slider, picking a codec brings it back."""
    gi = pytest.importorskip("gi")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    Adw.init()
    from grawji.settings import Settings
    from grawji.views.preferences import PreferencesDialog

    dialog = PreferencesDialog(
        settings=Settings(),
        on_change=lambda: None,
        capabilities=capabilities.capabilities_for_model("X-E5"),
    )
    seen = {}
    for index, fmt in enumerate(dialog._formats):
        dialog.format_row.set_selected(index)
        seen[fmt] = dialog.quality_row.get_visible()
    assert seen, "the dialog offered no format at all"
    for fmt, visible in seen.items():
        assert visible is export.scales_quality(fmt), fmt
    assert seen["jpeg"] is True


def test_heif_needs_the_decoder_as_well(monkeypatch):
    """Encoder alone is not enough, edited exports read the file back."""
    from grawji.imaging import heif_codec

    class FakeLib:
        def __init__(self, encode, decode):
            self._encode, self._decode = encode, decode

        def heif_have_encoder_for_format(self, _fmt):
            return self._encode

        def heif_have_decoder_for_format(self, _fmt):
            return self._decode

    monkeypatch.setattr(heif_codec, "_LOADED", True)
    for encode, decode, expected in (
        (1, 1, True),
        (1, 0, False),
        (0, 1, False),
        (0, 0, False),
    ):
        monkeypatch.setattr(heif_codec, "_LIB", FakeLib(encode, decode))
        assert heif_codec.available() is expected, (encode, decode)


def test_the_cjxl_probe_answers_no_when_the_tool_is_missing(monkeypatch):
    """No cjxl at all is simply no 16-bit JPEG XL, not a crash."""
    export.jxl16_available.cache_clear()
    monkeypatch.setattr(export.shutil, "which", lambda _name: None)
    try:
        assert export.jxl16_available() is False
    finally:
        export.jxl16_available.cache_clear()


@pytest.mark.skipif(not export.jxl_available(), reason="cjxl is not installed")
def test_the_cjxl_probe_runs_against_the_real_tool():
    """The probe is a real encode, so it answers for this machine."""
    assert isinstance(export.jxl16_available(), bool)


def test_icc_profile_is_read_back_from_a_tiff(tmp_path):
    """The profile the engine embeds has to survive the reader."""
    samples = sample_frame()
    path = tmp_path / "plain.tif"
    tiff.encode(samples, str(path), bits=16)
    # Nothing written it, nothing to find.
    assert tiff.icc_profile(path.read_bytes()) is None
    assert tiff.icc_profile(b"nonsense") is None


@pytest.mark.skipif(
    not export.jxl16_available(), reason="cjxl is too old or missing"
)
def test_jxl16_carries_the_color_space_across(tmp_path, monkeypatch):
    """A PPM has no color space, so the ICC must travel separately."""
    seen = {}
    real = export.encode_jxl

    def spy(samples, path, *, quality, exif, icc=None, bits=16):
        seen["icc"] = icc
        return real(
            samples, path, quality=quality, exif=exif, icc=icc, bits=bits
        )

    monkeypatch.setattr(export, "encode_jxl", spy)
    data = written(tmp_path, sample_frame(width=32, height=24), 16)
    export.write_jxl16(
        data,
        str(tmp_path / "out.jxl"),
        quality=90,
        geometry=export.Geometry(crop=CropRotate()),
    )
    assert "icc" in seen


def test_the_session_raises_the_container_limit():
    """The session lifts rawji's container limit on its own camera.

    A full-resolution 16-bit TIFF is larger than rawji allows by
    default, so without this no such export could be received.
    """
    from grawji.camera import core

    class FakeCamera:
        max_container_size = 512 * 1024 * 1024

        def connect(self):
            return False

    camera = FakeCamera()
    core._allow_large_containers(camera)
    # A 102 MP frame at 16 bit needs 586 MiB plus its metadata.
    assert camera.max_container_size >= 620 * 1024 * 1024


def test_raising_the_limit_tolerates_an_older_rawji():
    """A build without the attribute must not blow up on open."""
    from grawji.camera import core

    class Ancient:
        __slots__ = ()

    core._allow_large_containers(Ancient())


def test_decode_reports_the_orientation_tag():
    """A portrait frame from the camera says how it is stored."""
    samples = sample_frame()
    assert tiff.decode(tiff_bytes(samples, tag=8, bits=16)).orientation == 8
    assert tiff.decode(tiff_bytes(samples, tag=1, bits=16)).orientation == 1


def test_our_own_writer_leaves_the_frame_upright(tmp_path):
    """The writer omits the tag, which the reader takes as top left."""
    samples = sample_frame()
    assert tiff.decode(written(tmp_path, samples, 16)).orientation == 1


def test_an_edited_export_turns_the_frame_upright(tmp_path):
    """A sideways stored frame is cropped in the upright view."""
    samples = sample_frame(width=9, height=5)
    # Orientation 8 means the file is stored rotated 90 degrees clockwise
    data = tiff_bytes(
        np.ascontiguousarray(np.rot90(samples, -1)), tag=8, bits=16
    )
    path = tmp_path / "edited.tif"
    export.write_tiff(
        data, str(path), geometry=export.Geometry(crop=CropRotate())
    )
    written_back = tiff.decode(path.read_bytes())
    assert written_back.samples.shape == samples.shape
    assert np.array_equal(written_back.samples, samples)
