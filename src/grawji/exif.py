"""Format basic EXIF tags for display."""

from __future__ import annotations

from collections.abc import Callable

_Formatter = Callable[[str], str]


def _ratio(raw: str) -> float | None:
    """Parse an EXIF rational "a/b" (or plain number) into a float."""
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            denominator = float(den)
            return float(num) / denominator if denominator else None
        return float(raw)
    except ValueError:
        return None


def _aperture(raw: str) -> str:
    """Format an f-number rational, e.g. "280/100" -> "f/2.8"."""
    value = _ratio(raw)
    return f"f/{value:g}" if value is not None else raw


def _shutter(raw: str) -> str:
    """Format a shutter speed, e.g. "10/3400" -> "1/340 s"."""
    value = _ratio(raw)
    if value is None or value <= 0:
        return raw
    if value >= 1:
        return f"{value:g} s"
    return f"1/{round(1 / value)} s"


def _focal(raw: str) -> str:
    """Format a focal length, e.g. "3500/100" -> "35 mm"."""
    value = _ratio(raw)
    return f"{value:g} mm" if value is not None else raw


def _iso(raw: str) -> str:
    """Prefix an ISO value, e.g. "320" -> "ISO 320"."""
    return f"ISO {raw}"


# (display label, EXIF tag, optional formatter) in display order.
EXIF_FIELDS: list[tuple[str, str, _Formatter | None]] = [
    ("Camera", "Exif.Image.Model", None),
    ("Lens", "Exif.Photo.LensModel", None),
    ("Focal length", "Exif.Photo.FocalLength", _focal),
    ("Aperture", "Exif.Photo.FNumber", _aperture),
    ("Shutter", "Exif.Photo.ExposureTime", _shutter),
    ("ISO", "Exif.Photo.ISOSpeedRatings", _iso),
    ("Taken", "Exif.Photo.DateTimeOriginal", None),
]


def format_exif(raw: dict[str, str]) -> list[tuple[str, str]]:
    """Turn raw EXIF tag strings into display label/value pairs.

    Args:
        raw: Mapping of EXIF tag name to its raw string value. Missing or
            empty tags are skipped.

    Returns:
        A list of (label, value) pairs in display order.
    """
    rows: list[tuple[str, str]] = []
    for label, tag, formatter in EXIF_FIELDS:
        value = raw.get(tag)
        if not value:
            continue
        rows.append((label, formatter(value) if formatter else value))
    return rows
