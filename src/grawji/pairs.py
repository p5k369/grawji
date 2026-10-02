"""The camera file a RAW+JPEG shot leaves beside its RAF."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

# The camera file spellings a camera or a card reader may leave behind.
COMPANION_SUFFIXES = (".JPG", ".jpg", ".JPEG", ".jpeg")


class Source(StrEnum):
    """What a RAW+JPEG shot is developed and exported from."""

    RAW = "raw"
    CAMERA = "camera"


def source_of(value: object) -> Source | None:
    """A stored source, or None when absent or unknown."""
    try:
        return Source(value) if isinstance(value, str) else None
    except ValueError:
        return None


def companion_label(path: str) -> str:
    """The short name a camera file goes by, its suffix: JPG, HIF."""
    return Path(path).suffix.lstrip(".").upper()


def is_companion_name(name: str) -> bool:
    """Whether a file name has a camera file suffix."""
    return name.endswith(COMPANION_SUFFIXES)


def companion_of(raf_path: Path | str) -> Path | None:
    """The camera file with the RAF's stem in the RAF's folder, if any."""
    raf = Path(raf_path)
    for suffix in COMPANION_SUFFIXES:
        candidate = raf.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def companions_by_stem(names: Iterable[str]) -> dict[str, str]:
    """The camera file names of one folder listing, by stem.

    When one stem has several spellings the first suffix in
    COMPANION_SUFFIXES wins, the same order companion_of tries.
    """
    found: dict[str, str] = {}
    rank = {suffix: index for index, suffix in enumerate(COMPANION_SUFFIXES)}
    for name in names:
        suffix = Path(name).suffix
        if suffix not in rank:
            continue
        stem = Path(name).stem
        held = found.get(stem)
        if held is None or rank[suffix] < rank[Path(held).suffix]:
            found[stem] = name
    return found
