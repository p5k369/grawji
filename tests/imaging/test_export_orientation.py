"""Every export format agrees on how a rotated frame is framed."""

from __future__ import annotations

import shutil
import subprocess
from functools import partial
from pathlib import Path

import numpy as np
import pytest

from grawji.crop import CropRotate
from grawji.imaging import export, heif_codec, render16, tiff
from grawji.imaging.export import (
    baked_pixbuf,
    geometry_for,
    with_border,
    with_max_edge,
)
from grawji.settings import Settings
from tests.image_support import (
    jpeg_bytes,
    quadrants,
    shown,
    stored_as,
    tiff_bytes,
    widen,
)

TAG = 8
# Room for re-encoding, well under any transform going wrong.
TOLERANCE = 24
# The HEIF encoder pads an odd edge, so the export trims one line
_EVEN_EDGE = 1
_GEOMETRIES = {
    "crop": CropRotate(rect=(0.1, 0.1, 0.6, 0.7)),
    "crop and turn": CropRotate(orientation=90, rect=(0.05, 0.1, 0.7, 0.6)),
    "turn only": CropRotate(orientation=270),
    "straighten": CropRotate(angle=6.5, rect=(0.12, 0.12, 0.6, 0.6)),
}


def _heif_source(tmp_path: Path) -> bytes:
    """A HEIF holding the upright frame.

    libheif turns a rotated camera HEIF upright while decoding it, so the
    rotation never reaches our code and a rotated source would only test
    the library. What is ours is everything after that.
    """
    path = tmp_path / "camera.heif"
    frame = widen(quadrants(), heif_codec.CAMERA_BITS)
    heif_codec.encode(
        frame, str(path), bits=heif_codec.CAMERA_BITS, quality=95
    )
    return path.read_bytes()


def _sources(tmp_path: Path) -> dict[str, bytes]:
    """One camera-like file per format, all holding the same frame."""
    stored = stored_as(quadrants(), TAG)
    jpeg = jpeg_bytes(stored, str(tmp_path / "camera.jpg"), tag=TAG)
    sources = {
        "jpeg": jpeg,
        "jxl": jpeg,
        "tiff8": tiff_bytes(stored, tag=TAG, bits=8),
        "tiff16": tiff_bytes(widen(stored), tag=TAG, bits=16),
        "jxl16": tiff_bytes(widen(stored), tag=TAG, bits=16),
    }
    if heif_codec.available():
        sources["heif"] = _heif_source(tmp_path)
    return sources


def _write(data: bytes, path: Path, fmt: str, crop: CropRotate) -> None:
    """Export exactly the way the controller does."""
    settings = Settings()
    export.write_jpeg(
        data,
        str(path),
        quality=92,
        decode=with_border(
            with_max_edge(partial(baked_pixbuf, crop=crop), settings), settings
        ),
        fmt=fmt,
        geometry=geometry_for(crop, settings),
    )


def _displayed(path: Path, fmt: str) -> np.ndarray:
    """The export as a viewer puts it on screen."""
    if fmt in ("tiff8", "tiff16"):
        decoded = tiff.decode(path.read_bytes())
        frame = render16.exif_orient(decoded.samples, decoded.orientation)
        return (
            frame.astype(np.float64) * 255 / ((1 << decoded.bits) - 1)
        ).round()
    if fmt == "heif":
        image = heif_codec.decode(path.read_bytes())
        assert image is not None
        frame = render16.as_array(image).astype(np.float64)
        return (frame * 255 / ((1 << image.bits) - 1)).round()
    if fmt in ("jxl", "jxl16"):
        ppm = path.with_suffix(".ppm")
        subprocess.run(
            [shutil.which("djxl") or "djxl", str(path), str(ppm)],
            check=True,
            capture_output=True,
        )
        with ppm.open("rb") as handle:
            assert handle.readline().strip() == b"P6"
            width, height = (int(item) for item in handle.readline().split())
            top = int(handle.readline())
            dtype = np.uint8 if top < 256 else ">u2"
            raw = np.frombuffer(handle.read(), dtype)
        frame = raw.reshape(height, width, 3).astype(np.float64)
        return (frame * 255 / top).round()
    return shown(str(path)).astype(np.float64)


def _skip_unless_available(fmt: str) -> None:
    """Formats that need an outside tool only run where it exists."""
    if fmt == "heif" and not heif_codec.available():
        pytest.skip("libheif with an HEVC encoder and decoder is missing")
    if fmt in ("jxl", "jxl16") and shutil.which("djxl") is None:
        pytest.skip("djxl is needed to read the export back")
    if fmt == "jxl" and not export.jxl_available():
        pytest.skip("cjxl is not installed")
    if fmt == "jxl16" and not export.jxl16_available():
        pytest.skip("this cjxl cannot write 16-bit JPEG XL")


@pytest.mark.parametrize("fmt", ["jxl", "heif", "tiff8", "tiff16", "jxl16"])
@pytest.mark.parametrize("name", sorted(_GEOMETRIES))
def test_every_format_frames_a_rotated_shot_alike(tmp_path, fmt, name):
    """The JPEG path is the reference, the others must match it."""
    _skip_unless_available(fmt)
    crop = _GEOMETRIES[name]
    sources = _sources(tmp_path)
    reference_path = tmp_path / "reference.jpg"
    _write(sources["jpeg"], reference_path, "jpeg", crop)
    reference = _displayed(reference_path, "jpeg")

    path = tmp_path / f"export-{fmt}"
    _write(sources[fmt], path, fmt, crop)
    frame = _displayed(path, fmt)

    # HEIF needs even edges, so its export may lose a last odd line
    assert abs(frame.shape[0] - reference.shape[0]) <= _EVEN_EDGE
    assert abs(frame.shape[1] - reference.shape[1]) <= _EVEN_EDGE
    rows = min(frame.shape[0], reference.shape[0])
    columns = min(frame.shape[1], reference.shape[1])
    difference = frame[:rows, :columns] - reference[:rows, :columns]
    assert np.abs(difference).mean() < TOLERANCE


def test_an_unrotated_shot_is_left_alone(tmp_path):
    """A frame that is already upright must not be turned."""
    upright = quadrants()
    data = tiff_bytes(upright, tag=1)
    path = tmp_path / "upright.tif"
    _write(data, path, "tiff8", CropRotate())
    frame = _displayed(path, "tiff8")
    assert frame.shape == upright.shape
    assert np.abs(frame - upright.astype(np.float64)).mean() < TOLERANCE
