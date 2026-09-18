"""Export building blocks."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio

from grawji import sidecar
from grawji.camera.capabilities import Capabilities
from grawji.crop import CropRotate
from grawji.imaging import heif, heif_codec, imagemeta, render16, tiff
from grawji.imaging.render import (
    add_border,
    bake_pixbuf,
    parse_aspect,
    parse_color,
    scale_to_edge,
)
from grawji.imaging.render16 import Samples
from grawji.recipe import Recipe
from grawji.settings import Settings
from grawji.views.preview_view import oriented_pixbuf

SetBusy = Callable[..., None]


def baked_pixbuf(jpeg: bytes, *, crop: CropRotate) -> Any:
    """Decode camera bytes and bake a geometry into them."""
    return bake_pixbuf(oriented_pixbuf(jpeg), crop)


def sidecar_decode(raf_path: str) -> Callable[[bytes], Any] | None:
    """A decode callback applying the RAF's sidecar geometry."""
    geometry = sidecar.load_crop(raf_path)
    if geometry.is_identity:
        return None
    return lambda jpeg: bake_pixbuf(oriented_pixbuf(jpeg), geometry)


def framing_active(settings: Settings) -> bool:
    """Whether the export border/padding would change any pixels."""
    return settings.export_border_enabled and (
        settings.export_border_percent > 0
        or parse_aspect(settings.export_border_aspect) is not None
    )


def resize_active(settings: Settings) -> bool:
    """Whether the export size limit would change any pixels."""
    return settings.export_max_edge > 0


def with_max_edge(
    decode: Callable[[bytes], Any], settings: Settings
) -> Callable[[bytes], Any]:
    """Wrap decode with the configured long-edge limit."""
    if not resize_active(settings):
        return decode
    max_edge = settings.export_max_edge
    return lambda jpeg: scale_to_edge(decode(jpeg), max_edge)


def with_border(
    decode: Callable[[bytes], Any], settings: Settings
) -> Callable[[bytes], Any]:
    """Wrap decode with the configured export border."""
    if not framing_active(settings):
        return decode
    percent = settings.export_border_percent
    color = settings.export_border_color
    aspect = parse_aspect(settings.export_border_aspect)
    return lambda jpeg: add_border(decode(jpeg), percent, color, aspect)


# Export formats, in the order the preferences list them.
FORMATS = ("jpeg", "jxl", "heif", "jxl16", "tiff8", "tiff16")
# Every JPEG ends with an end-of-image marker.
_JPEG_END = b"\xff\xd9"
_SUFFIXES = {
    "jpeg": ".jpg",
    "jxl": ".jxl",
    "heif": ".heif",
    "jxl16": ".jxl",
    "tiff8": ".tif",
    "tiff16": ".tif",
}
# What the camera has to render for each export format.
_CAMERA_TYPES = {
    "heif": "heif",
    "tiff8": "tiff8",
    "tiff16": "tiff16",
    "jxl16": "tiff16",
}
# The formats that arrive from the camera as a TIFF.
_FROM_TIFF = ("tiff8", "tiff16", "jxl16")
_TIFF_BITS = {"tiff8": 8, "tiff16": 16}
# Formats whose file size and fidelity answer to the quality setting.
_SCALES_QUALITY = ("jpeg", "jxl", "jxl16", "heif")
# A four-pixel frame and the smallest valid Exif stream, used once to
# ask cjxl what it can really do.
_PROBE_PPM = b"P6\n2 2\n65535\n" + bytes(24)
_PROBE_EXIF = (
    b"II*\x00\x08\x00\x00\x00\x01\x00"
    b"\x0f\x01\x02\x00\x02\x00\x00\x00\x1a\x00\x00\x00"
    b"\x00\x00\x00\x00A\x00"
)


@lru_cache(maxsize=1)
def jxl_available() -> bool:
    """Whether the cjxl tool for JPEG XL exports is installed."""
    return shutil.which("cjxl") is not None


@lru_cache(maxsize=1)
def jxl16_available() -> bool:
    """Whether cjxl can do everything a 16-bit JPEG XL export needs."""
    cjxl = shutil.which("cjxl")
    if cjxl is None:
        return False
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "probe.jxl"
        exif = Path(folder) / "probe.bin"
        exif.write_bytes(_PROBE_EXIF)
        try:
            proc = subprocess.run(  # noqa: S603
                [
                    cjxl,
                    "-q",
                    "90",
                    "--compress_boxes=0",
                    "-x",
                    f"exif={exif}",
                    "-",
                    str(out),
                ],
                input=_PROBE_PPM,
                capture_output=True,
                check=False,
            )
        except OSError:
            return False
        # Accepting the flags is not enough, the Exif box has to be
        # in the file. Version 0.7 accepts and then writes a bare
        # codestream with no container at all. Open for better solutions.
        if proc.returncode != 0 or not out.exists():
            return False
        return b"Exif" in out.read_bytes()


def heif_available() -> bool:
    """Whether HEIF exports can be written on this system."""
    return heif_codec.available()


def available_formats(
    capabilities: Capabilities | None = None,
) -> tuple[str, ...]:
    """The export formats that would actually work right now."""
    usable = ["jpeg"]
    if jxl_available():
        usable.append("jxl")
    body_can_heif = capabilities is None or capabilities.has_heif
    if heif_available() and body_can_heif:
        usable.append("heif")
    body_can_tiff16 = capabilities is None or capabilities.has_tiff16
    if jxl16_available() and body_can_tiff16:
        usable.append("jxl16")
    if capabilities is None or capabilities.has_tiff8:
        usable.append("tiff8")
    if body_can_tiff16:
        usable.append("tiff16")
    return tuple(usable)


def missing_tools(formats: tuple[str, ...]) -> tuple[str, ...]:
    """Names of the tools that would add a format to formats."""
    missing = []
    if "jxl" not in formats and not jxl_available():
        missing.append("cjxl")
    if "heif" not in formats and not heif_codec.available():
        missing.append("libheif with an HEVC encoder and decoder")
    if "jxl16" not in formats and jxl_available() and not jxl16_available():
        missing.append("a newer cjxl for 16-bit JPEG XL")
    return tuple(missing)


def export_format(
    settings: Settings, capabilities: Capabilities | None = None
) -> str:
    """The format exports should be written in right now."""
    fmt = settings.export_format
    return fmt if fmt in available_formats(capabilities) else "jpeg"


def camera_file_type(fmt: str) -> str:
    """The conversion output format the camera must render for fmt."""
    return _CAMERA_TYPES.get(fmt, "jpeg")


def scales_quality(fmt: str) -> bool:
    """Whether the quality setting changes anything for this format."""
    return fmt in _SCALES_QUALITY


def recipe_for_format(fmt: str, recipe: Recipe) -> tuple[Recipe, str]:
    """The recipe the camera can render in this format, and what it lost."""
    if fmt == "heif" and recipe.clarity:
        return (
            replace(recipe, clarity=0),
            "Clarity is not available in HEIF, exported without it.",
        )
    return recipe, ""


def delivered_format(data: bytes, fmt: str) -> str:
    """The format the bytes really are."""
    if fmt == "heif" and not heif.is_heif(data):
        return "jpeg"
    if fmt in _FROM_TIFF and not tiff.is_tiff(data):
        return "jpeg"
    return fmt


def repack_jxl(jpeg: bytes, path: str) -> None:
    """Losslessly repack a finished JPEG into a .jxl at path."""
    cjxl = shutil.which("cjxl")
    if cjxl is None:
        raise OSError("cjxl not found. Cannot write JPEG XL")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(jpeg)
        result = subprocess.run(  # noqa: S603
            [cjxl, "--lossless_jpeg=1", str(tmp_path), path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(f"cjxl failed: {result.stderr.strip()}")
    finally:
        tmp_path.unlink(missing_ok=True)


def _exif_stream(
    source: bytes,
    *,
    artist: str,
    rights: str,
    comment: str,
    size: tuple[int, int],
) -> bytes | None:
    """The camera's metadata as a bare TIFF stream for a JXL Exif box."""
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        carrier = Path(tmp.name)
    try:
        tiff.encode(np.zeros((1, 1, 3), dtype=np.uint16), str(carrier), bits=8)
        imagemeta.copy_exif(
            source,
            str(carrier),
            artist=artist,
            rights=rights,
            comment=comment,
            size=size,
        )
        return carrier.read_bytes()
    except (OSError, tiff.TiffError):
        return None
    finally:
        carrier.unlink(missing_ok=True)


def encode_jxl(
    samples: Samples,
    path: str,
    *,
    quality: int,
    exif: bytes | None,
    icc: bytes | None = None,
) -> None:
    """Encode 16-bit samples to JPEG XL, metadata included."""
    cjxl = shutil.which("cjxl")
    if cjxl is None:
        raise OSError("cjxl not found. Cannot write JPEG XL")
    height, width = samples.shape[:2]
    command = [cjxl, "-q", str(quality), "--compress_boxes=0"]
    with tempfile.TemporaryDirectory() as folder:
        block = Path(folder) / "exif.bin"
        profile = Path(folder) / "color.icc"
        if exif:
            block.write_bytes(exif)
            command += ["-x", f"exif={block}"]
        if icc:
            profile.write_bytes(icc)
            command += ["-x", f"icc_pathname={profile}"]
        command += ["-", path]
        with subprocess.Popen(  # noqa: S603
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        ) as proc:
            assert proc.stdin is not None  # noqa: S101
            # PPM is big-endian by definition, the samples are not.
            proc.stdin.write(b"P6\n%d %d\n65535\n" % (width, height))
            proc.stdin.write(samples.astype(">u2").tobytes())
            proc.stdin.close()
            failed = proc.wait() != 0
    if failed:
        raise OSError("cjxl failed to encode the 16-bit frame")


def _check_complete(data: bytes, fmt: str) -> None:
    """Refuse a render the camera did not finish sending."""
    if fmt == "heif":
        whole = heif.is_complete(data)
    elif fmt in _FROM_TIFF:
        whole = tiff.is_complete(data)
    else:
        whole = data.endswith(_JPEG_END)
    if not whole:
        raise OSError(
            f"the camera's {fmt.upper()} arrived incomplete "
            f"({len(data)} bytes), so the export was not written. "
            "Try it again."
        )


def _heif_with_credits(
    data: bytes, *, artist: str, rights: str, comment: str
) -> bytes:
    """Stamp the export credits into a HEIF, leaving its pixels alone."""
    if not (artist or rights or comment):
        return data
    block = heif.exif_block(data)
    if block is None:
        return data
    stamped = imagemeta.stamp_exif_block(
        block, artist=artist, rights=rights, comment=comment
    )
    return heif.replace_exif(data, stamped) or data


def write_passthrough(  # noqa: PLR0913
    data: bytes,
    path: str,
    *,
    artist: str = "",
    rights: str = "",
    comment: str = "",
    fmt: str = "jpeg",
    quality: int = 95,
) -> None:
    """Write the camera's own bytes, credits stamped, no re-encode."""
    _check_complete(data, fmt)
    if fmt == "jxl16":
        write_jxl16(
            data,
            path,
            quality=quality,
            geometry=Geometry(crop=CropRotate()),
            artist=artist,
            rights=rights,
            comment=comment,
        )
        return
    if fmt in _TIFF_BITS:
        Path(path).write_bytes(data)
        imagemeta.stamp_file(
            path, artist=artist, rights=rights, comment=comment
        )
        return
    if fmt == "heif":
        Path(path).write_bytes(
            _heif_with_credits(
                data, artist=artist, rights=rights, comment=comment
            )
        )
        return
    stamped = imagemeta.with_credits(
        data, artist=artist, rights=rights, comment=comment
    )
    if fmt == "jxl":
        repack_jxl(stamped, path)
    else:
        Path(path).write_bytes(stamped)


def format_suffix(fmt: str) -> str:
    """The file extension for an export format."""
    return _SUFFIXES.get(fmt, ".jpg")


def export_basename(raf_path: Path | str, *, fmt: str = "jpeg") -> str:
    """Build an export filename from the RAF stem."""
    return f"{Path(raf_path).stem}{format_suffix(fmt)}"


def corrected_path(path: str, fmt: str) -> str:
    """Repoint an export path at the format that was really produced."""
    suffix = format_suffix(fmt)
    if Path(path).suffix.lower() == suffix:
        return path
    return str(Path(path).with_suffix(suffix))


def initial_folder(path: str) -> Gio.File | None:
    """A Gio.File for path if it is an existing directory."""
    if path and Path(path).is_dir():
        return Gio.File.new_for_path(path)
    return None


@dataclass(frozen=True)
class Geometry:
    """Everything an export does to the camera's pixels."""

    crop: CropRotate
    max_edge: int = 0
    border_percent: float = 0.0
    border_color: str = "#ffffff"
    border_aspect: float | None = None


def geometry_for(crop: CropRotate, settings: Settings) -> Geometry:
    """Collect the geometry an export should apply."""
    framing = framing_active(settings)
    return Geometry(
        crop=crop,
        max_edge=settings.export_max_edge,
        border_percent=(settings.export_border_percent if framing else 0.0),
        border_color=settings.export_border_color,
        border_aspect=(
            parse_aspect(settings.export_border_aspect) if framing else None
        ),
    )


def write_heif(  # noqa: PLR0913
    data: bytes,
    path: str,
    *,
    quality: int,
    geometry: Geometry,
    artist: str = "",
    rights: str = "",
    comment: str = "",
) -> None:
    """Re-encode an edited camera HEIF, keeping its depth and metadata."""
    _check_complete(data, "heif")
    image = heif_codec.decode(data)
    if image is None:
        raise heif_codec.HeifError("libheif is not available to decode HEIF")
    samples = render16.bake(render16.as_array(image), geometry.crop)
    samples = render16.scale_to_edge(samples, geometry.max_edge)
    samples = render16.add_border(
        samples,
        geometry.border_percent,
        render16.scale_color(parse_color(geometry.border_color), image.bits),
        geometry.border_aspect,
    )
    # Trim before the metadata: the encoder pads an odd edge, so the
    # trimmed shape is the one the file will really have.
    samples = render16.trim_even(samples)
    exif = heif.exif_block(data)
    if exif:
        exif = imagemeta.stamp_exif_block(
            exif,
            artist=artist,
            rights=rights,
            comment=comment,
            size=(samples.shape[1], samples.shape[0]),
        )
    heif_codec.encode(
        samples, path, bits=image.bits, quality=quality, exif=exif
    )


def write_tiff(
    data: bytes,
    path: str,
    *,
    geometry: Geometry,
    artist: str = "",
    rights: str = "",
    comment: str = "",
) -> None:
    """Re-encode an edited camera TIFF, keeping its depth and metadata."""
    _check_complete(data, "tiff16")
    decoded = tiff.decode(data)
    samples = render16.bake(decoded.samples, geometry.crop)
    samples = render16.scale_to_edge(samples, geometry.max_edge)
    samples = render16.add_border(
        samples,
        geometry.border_percent,
        render16.scale_color(parse_color(geometry.border_color), decoded.bits),
        geometry.border_aspect,
    )
    tiff.encode(samples, path, bits=decoded.bits)
    imagemeta.copy_exif(
        data,
        path,
        artist=artist,
        rights=rights,
        comment=comment,
        size=(samples.shape[1], samples.shape[0]),
    )


def write_jxl16(  # noqa: PLR0913
    data: bytes,
    path: str,
    *,
    quality: int,
    geometry: Geometry,
    artist: str = "",
    rights: str = "",
    comment: str = "",
) -> None:
    """Pack an edited camera TIFF into JPEG XL at its own bit depth."""
    _check_complete(data, "jxl16")
    decoded = tiff.decode(data)
    samples = render16.bake(decoded.samples, geometry.crop)
    samples = render16.scale_to_edge(samples, geometry.max_edge)
    samples = render16.add_border(
        samples,
        geometry.border_percent,
        render16.scale_color(parse_color(geometry.border_color), decoded.bits),
        geometry.border_aspect,
    )
    encode_jxl(
        samples,
        path,
        quality=quality,
        icc=tiff.icc_profile(data),
        exif=_exif_stream(
            data,
            artist=artist,
            rights=rights,
            comment=comment,
            size=(samples.shape[1], samples.shape[0]),
        ),
    )


def write_jpeg(  # noqa: PLR0913
    jpeg: bytes,
    path: str,
    *,
    quality: int,
    decode: Callable[[bytes], Any],
    artist: str = "",
    rights: str = "",
    comment: str = "",
    fmt: str = "jpeg",
    geometry: Geometry | None = None,
) -> None:
    """Write jpeg to path with orientation and rotation baked in."""
    if fmt == "jxl16":
        if geometry is None:
            raise ValueError("a 16-bit JPEG XL export needs its geometry")
        write_jxl16(
            jpeg,
            path,
            quality=quality,
            geometry=geometry,
            artist=artist,
            rights=rights,
            comment=comment,
        )
        return
    if fmt in _TIFF_BITS:
        if geometry is None:
            raise ValueError("a TIFF export needs its geometry")
        write_tiff(
            jpeg,
            path,
            geometry=geometry,
            artist=artist,
            rights=rights,
            comment=comment,
        )
        return
    if fmt == "heif":
        if geometry is None:
            raise ValueError("a HEIF export needs its geometry")
        write_heif(
            jpeg,
            path,
            quality=quality,
            geometry=geometry,
            artist=artist,
            rights=rights,
            comment=comment,
        )
        return
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pixbuf = decode(jpeg)
        pixbuf.savev(tmp_path, "jpeg", ["quality"], [str(quality)])
        # GdkPixbuf re-encoding drops all metadata, so copy the camera's
        # EXIF back on (orientation is now baked into the pixels).
        imagemeta.copy_exif(
            jpeg,
            tmp_path,
            artist=artist,
            rights=rights,
            comment=comment,
            size=(pixbuf.get_width(), pixbuf.get_height()),
        )
        if fmt == "jxl":
            repack_jxl(Path(tmp_path).read_bytes(), path)
        else:
            Path(path).write_bytes(Path(tmp_path).read_bytes())
    finally:
        Path(tmp_path).unlink(missing_ok=True)
