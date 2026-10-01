"""The camera JPEG a RAW+JPEG shot leaves beside its RAF."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# The JPEG spellings a camera or a card reader may leave behind.
JPEG_SUFFIXES = (".JPG", ".jpg", ".JPEG", ".jpeg")


def is_jpeg_name(name: str) -> bool:
    """Whether a file name has a JPEG suffix."""
    return name.endswith(JPEG_SUFFIXES)


def paired_jpeg(raf_path: Path | str) -> Path | None:
    """The JPEG with the RAF's stem in the RAF's folder, if any."""
    raf = Path(raf_path)
    for suffix in JPEG_SUFFIXES:
        candidate = raf.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def jpegs_by_stem(names: Iterable[str]) -> dict[str, str]:
    """The JPEG names of one folder listing, by stem.

    When one stem has several spellings the first suffix in
    JPEG_SUFFIXES wins, the same order paired_jpeg tries.
    """
    found: dict[str, str] = {}
    rank = {suffix: index for index, suffix in enumerate(JPEG_SUFFIXES)}
    for name in names:
        suffix = Path(name).suffix
        if suffix not in rank:
            continue
        stem = Path(name).stem
        held = found.get(stem)
        if held is None or rank[suffix] < rank[Path(held).suffix]:
            found[stem] = name
    return found
