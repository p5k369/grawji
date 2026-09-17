"""The libheif bindings.

The runtime ships libheif but no binding for it, so the camera's 10-bit files
have to reach the C library directly. ctypes is the price, and this module is
where it is paid.
"""

from __future__ import annotations

import ctypes
from typing import Any, NamedTuple

# libheif enum values.
_COMPRESSION_HEVC = 1
_COLORSPACE_RGB = 1
_CHROMA_INTERLEAVED_RRGGBB_LE = 14
_CHANNEL_INTERLEAVED = 10
CAMERA_BITS = 10
_CHANNELS = 3


class HeifError(RuntimeError):
    """A libheif call failed."""


class _Error(ctypes.Structure):
    """libheif's heif_error, returned by value from most calls."""

    _fields_ = (
        ("code", ctypes.c_int),
        ("subcode", ctypes.c_int),
        ("message", ctypes.c_char_p),
    )


_VOID = ctypes.c_void_p
_INT = ctypes.c_int
_SIZE = ctypes.c_size_t
_BYTES = ctypes.c_char_p
_OUT = ctypes.POINTER(_VOID)
_PLANE = ctypes.POINTER(ctypes.c_ubyte)
_Signatures = tuple[tuple[str, list[Any], Any], ...]

_SIGNATURES: _Signatures = (
    ("heif_context_alloc", [], _VOID),
    ("heif_context_free", [_VOID], None),
    ("heif_have_encoder_for_format", [_INT], _INT),
    ("heif_image_create", [_INT] * 4 + [_OUT], _Error),
    ("heif_image_add_plane", [_VOID] + [_INT] * 4, _Error),
    ("heif_image_get_plane", [_VOID, _INT, ctypes.POINTER(_INT)], _PLANE),
    ("heif_image_release", [_VOID], None),
    ("heif_context_get_encoder_for_format", [_VOID, _INT, _OUT], _Error),
    ("heif_encoder_set_lossy_quality", [_VOID, _INT], _Error),
    ("heif_encoder_release", [_VOID], None),
    ("heif_context_encode_image", [_VOID] * 4 + [_OUT], _Error),
    ("heif_context_add_exif_metadata", [_VOID, _VOID, _BYTES, _INT], _Error),
    ("heif_context_write_to_file", [_VOID, _BYTES], _Error),
    ("heif_context_read_from_memory", [_VOID, _BYTES, _SIZE, _VOID], _Error),
    ("heif_context_get_primary_image_handle", [_VOID, _OUT], _Error),
    ("heif_image_handle_release", [_VOID], None),
    ("heif_decode_image", [_VOID, _OUT, _INT, _INT, _VOID], _Error),
    ("heif_image_get_width", [_VOID, _INT], _INT),
    ("heif_image_get_height", [_VOID, _INT], _INT),
    (
        "heif_image_get_plane_readonly",
        [_VOID, _INT, ctypes.POINTER(_INT)],
        _PLANE,
    ),
)


def _open() -> ctypes.CDLL | None:
    """Open libheif."""
    try:
        return ctypes.CDLL("libheif.so.1")
    except OSError:
        return None


def _declare(lib: ctypes.CDLL | None, signatures: _Signatures) -> bool:
    """Type the given calls, reporting whether all of them exist."""
    if lib is None:
        return False
    for name, argtypes, restype in signatures:
        try:
            func = getattr(lib, name)
        except AttributeError:
            return False
        func.argtypes = argtypes
        if restype is not None:
            func.restype = restype
    return True


_LIB = _open()
_LOADED = _declare(_LIB, _SIGNATURES)


def available() -> bool:
    """Whether HEIF exports can be written on this system."""
    if _LIB is None or not _LOADED:
        return False
    return bool(_LIB.heif_have_encoder_for_format(_COMPRESSION_HEVC))


def _check(error: _Error, what: str) -> None:
    """Raise HeifError unless the call succeeded."""
    if error.code == 0:
        return
    message = error.message.decode(errors="replace") if error.message else ""
    raise HeifError(f"{what} failed: {message} ({error.code}.{error.subcode})")


def _fill_plane(lib: ctypes.CDLL, image: _VOID, samples: Any) -> None:
    """Copy 16-bit RGB samples into the image's plane, row by row."""
    height, width = samples.shape[0], samples.shape[1]
    stride = ctypes.c_int()
    plane = lib.heif_image_get_plane(
        image, _CHANNEL_INTERLEAVED, ctypes.byref(stride)
    )
    if not plane:
        raise HeifError("libheif returned no pixel plane")
    dest = ctypes.cast(
        plane, ctypes.POINTER(ctypes.c_ubyte * (stride.value * height))
    ).contents
    row_bytes = width * _CHANNELS * 2
    data = samples.astype("<u2", copy=False).tobytes()
    for y in range(height):
        offset = y * stride.value
        start = y * row_bytes
        dest[offset : offset + row_bytes] = data[start : start + row_bytes]


def encode(
    samples: Any,
    path: str,
    *,
    bits: int = CAMERA_BITS,
    quality: int,
    exif: bytes | None = None,
) -> None:
    """Encode 16-bit interleaved RGB samples as HEIF at path."""
    lib = _LIB
    if lib is None or not available():
        raise HeifError("libheif with an HEVC encoder is not available")
    height, width = samples.shape[0], samples.shape[1]
    context = lib.heif_context_alloc()
    image = _VOID()
    encoder = _VOID()
    handle = _VOID()
    try:
        _check(
            lib.heif_image_create(
                width,
                height,
                _COLORSPACE_RGB,
                _CHROMA_INTERLEAVED_RRGGBB_LE,
                ctypes.byref(image),
            ),
            "heif_image_create",
        )
        _check(
            lib.heif_image_add_plane(
                image, _CHANNEL_INTERLEAVED, width, height, bits
            ),
            "heif_image_add_plane",
        )
        _fill_plane(lib, image, samples)
        _check(
            lib.heif_context_get_encoder_for_format(
                context, _COMPRESSION_HEVC, ctypes.byref(encoder)
            ),
            "heif_context_get_encoder_for_format",
        )
        _check(
            lib.heif_encoder_set_lossy_quality(encoder, quality),
            "heif_encoder_set_lossy_quality",
        )
        _check(
            lib.heif_context_encode_image(
                context, image, encoder, None, ctypes.byref(handle)
            ),
            "heif_context_encode_image",
        )
        if exif:
            _check(
                lib.heif_context_add_exif_metadata(
                    context, handle, exif, len(exif)
                ),
                "heif_context_add_exif_metadata",
            )
        _check(
            lib.heif_context_write_to_file(context, str(path).encode()),
            "heif_context_write_to_file",
        )
    finally:
        if handle:
            lib.heif_image_handle_release(handle)
        if encoder:
            lib.heif_encoder_release(encoder)
        if image:
            lib.heif_image_release(image)
        lib.heif_context_free(context)


class DecodedImage(NamedTuple):
    """Decoded HEIF pixels at their native bit depth."""

    pixels: bytes
    width: int
    height: int
    stride: int
    bits: int


def decode(data: bytes) -> DecodedImage | None:
    """Decode HEIF bytes to interleaved 16-bit RGB."""
    lib = _LIB
    if lib is None or not _LOADED or not data:
        return None
    context = lib.heif_context_alloc()
    handle = _VOID()
    image = _VOID()
    try:
        _check(
            lib.heif_context_read_from_memory(context, data, len(data), None),
            "heif_context_read_from_memory",
        )
        _check(
            lib.heif_context_get_primary_image_handle(
                context, ctypes.byref(handle)
            ),
            "heif_context_get_primary_image_handle",
        )
        _check(
            lib.heif_decode_image(
                handle,
                ctypes.byref(image),
                _COLORSPACE_RGB,
                _CHROMA_INTERLEAVED_RRGGBB_LE,
                None,
            ),
            "heif_decode_image",
        )
        width = lib.heif_image_get_width(image, _CHANNEL_INTERLEAVED)
        height = lib.heif_image_get_height(image, _CHANNEL_INTERLEAVED)
        stride = ctypes.c_int()
        plane = lib.heif_image_get_plane_readonly(
            image, _CHANNEL_INTERLEAVED, ctypes.byref(stride)
        )
        if not plane or width <= 0 or height <= 0:
            return None
        pixels = ctypes.string_at(plane, stride.value * height)
        return DecodedImage(pixels, width, height, stride.value, CAMERA_BITS)
    finally:
        if image:
            lib.heif_image_release(image)
        if handle:
            lib.heif_image_handle_release(handle)
        lib.heif_context_free(context)
