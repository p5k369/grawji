"""Tests for the TIFF reader, writer and export plumbing."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from grawji.camera import capabilities, core
from grawji.crop import CropRotate
from grawji.imaging import export, tiff


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
