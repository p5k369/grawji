"""The RAFs of a folder, ordered and narrowed."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from grawji.sidecar import edit_flags

# The RAF spellings a camera or a card reader may leave behind.
_PATTERNS = ("*.RAF", "*.raf")


def focal_mm(focal: str) -> float | None:
    """Parse a formatted focal length into millimeters."""
    try:
        return float(focal.split(maxsplit=1)[0])
    except (ValueError, IndexError):
        return None


@dataclass(frozen=True, slots=True)
class Entry:
    """One RAF in the folder, with what has been read about it."""

    path: str
    name: str
    has_crop: bool = False
    has_ev: bool = False
    model: str = ""
    lens: str = ""
    focal: str = ""
    # Exif spells capture time as "YYYY:MM:DD HH:MM:SS", which sorts
    # correctly as text, so it stays a string until someone needs more.
    shot: str = ""
    # Width over height of the frame as shown, known once a thumbnail
    # has been decoded.
    aspect: float = 0.0

    @property
    def edited(self) -> bool:
        """Whether the sidecar holds a crop or an exposure."""
        return self.has_crop or self.has_ev

    @property
    def focal_value(self) -> float | None:
        """The focal length in millimeters, if it is known."""
        return focal_mm(self.focal)

    @property
    def has_meta(self) -> bool:
        """Whether the thumbnail pass has read this frame yet.

        A fixed-lens body leaves the lens empty, so an empty field on
        its own says nothing. Only a frame with no metadata at all is
        still pending.
        """
        return bool(self.model or self.lens or self.focal)


class Order(StrEnum):
    """The orders a view can present the folder in."""

    NAME = "name"
    SHOT = "shot"
    CAMERA = "camera"
    LENS = "lens"
    FOCAL = "focal"


def _key(entry: Entry, order: Order) -> tuple[object, ...]:
    """The sort key of one entry, with the file name as tie breaker."""
    if order is Order.SHOT:
        return (entry.shot or "", entry.name)
    if order is Order.CAMERA:
        return (entry.model, entry.name)
    if order is Order.LENS:
        return (entry.lens, entry.name)
    if order is Order.FOCAL:
        # Unknown focal lengths sort last instead of raising.
        value = entry.focal_value
        return (value is None, value or 0.0, entry.name)
    return (entry.name,)


def sort_entries(
    entries: Iterable[Entry],
    order: Order = Order.NAME,
    *,
    reverse: bool = False,
) -> list[Entry]:
    """The entries in the given order."""
    return sorted(entries, key=lambda e: _key(e, order), reverse=reverse)


@dataclass(frozen=True, slots=True)
class Filter:
    """What the folder is narrowed down to."""

    camera: str | None = None
    lens: str | None = None
    focal: tuple[float, float] | None = None
    edited_only: bool = False

    @property
    def is_active(self) -> bool:
        """Whether this filter would hide anything at all."""
        return (
            self.camera is not None
            or self.lens is not None
            or self.focal is not None
            or self.edited_only
        )

    def matches(self, entry: Entry) -> bool:
        """Whether the entry passes.

        An entry whose metadata has not arrived yet passes every
        metadata rule, so a folder does not empty itself while the
        thumbnails are still being read.
        """
        if self.edited_only and not entry.edited:
            return False
        if not entry.has_meta:
            return True
        if self.camera is not None and entry.model != self.camera:
            return False
        if self.lens is not None and entry.lens != self.lens:
            return False
        if self.focal is not None:
            value = entry.focal_value
            low, high = self.focal
            if value is None or not low <= value <= high:
                return False
        return True


def scan(folder: Path | str) -> list[Entry]:
    """Every RAF of the folder, by file name, with its edit flags."""
    base = Path(folder)
    paths = sorted(
        {path for pattern in _PATTERNS for path in base.glob(pattern)}
    )
    entries = []
    for path in paths:
        has_crop, has_ev = edit_flags(path)
        entries.append(
            Entry(
                path=str(path),
                name=path.name,
                has_crop=has_crop,
                has_ev=has_ev,
            )
        )
    return entries


def with_meta(entry: Entry, model: str, lens: str, focal: str) -> Entry:
    """The entry again, with the metadata the thumbnail pass read."""
    return replace(entry, model=model, lens=lens, focal=focal)


def with_aspect(entry: Entry, aspect: float) -> Entry:
    """The entry again, now that the frame's shape is known."""
    return replace(entry, aspect=aspect)


def with_edits(entry: Entry, has_crop: bool, has_ev: bool) -> Entry:
    """The entry again, with the sidecar read afresh."""
    return replace(entry, has_crop=has_crop, has_ev=has_ev)


def apply(
    entries: Sequence[Entry],
    *,
    order: Order = Order.NAME,
    reverse: bool = False,
    entry_filter: Filter | None = None,
) -> list[Entry]:
    """The entries a view should show, filtered and ordered."""
    kept = entries
    if entry_filter is not None and entry_filter.is_active:
        kept = [entry for entry in entries if entry_filter.matches(entry)]
    return sort_entries(kept, order, reverse=reverse)


def cameras(entries: Iterable[Entry]) -> list[str]:
    """The camera models present, for the filter popover."""
    return sorted({entry.model for entry in entries if entry.model})


def lenses(entries: Iterable[Entry]) -> list[str]:
    """The lenses present, for the filter popover."""
    return sorted({entry.lens for entry in entries if entry.lens})


def focal_labels(entries: Iterable[Entry]) -> list[str]:
    """The focal lengths present, in numeric order, as written."""
    labels = {entry.focal for entry in entries if entry.focal}
    return sorted(
        labels,
        key=lambda label: (
            focal_mm(label) is None,
            focal_mm(label) or 0.0,
            label,
        ),
    )
