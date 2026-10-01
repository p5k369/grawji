"""The marks a person puts on an image."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

# The XMP convention: -1 rejects, 0 is unrated, 1 to 5 are stars.
REJECTED = -1
UNRATED = 0
MAX_RATING = 5


class Label(StrEnum):
    """The color labels, in the order every view shows them."""

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"


def _clamped_rating(value: object) -> int:
    """A stored rating inside the valid range, or unrated."""
    if isinstance(value, bool) or not isinstance(value, int):
        return UNRATED
    return max(REJECTED, min(MAX_RATING, value))


def _known_labels(value: object) -> frozenset[Label]:
    """The stored labels this version knows, unknown ones dropped."""
    if not isinstance(value, list):
        return frozenset()
    known = {label.value for label in Label}
    return frozenset(Label(item) for item in value if item in known)


@dataclass(frozen=True, slots=True)
class Marks:
    """Everything a person marked on one image."""

    rating: int = UNRATED
    labels: frozenset[Label] = frozenset()
    export: bool = False

    @property
    def rejected(self) -> bool:
        """Whether the image is marked as a reject."""
        return self.rating == REJECTED

    @property
    def stars(self) -> int:
        """The star count, zero for a reject."""
        return max(UNRATED, self.rating)

    @property
    def is_empty(self) -> bool:
        """Whether nothing is marked at all."""
        return self == Marks()

    @property
    def ordered_labels(self) -> list[Label]:
        """The labels in display order."""
        return [label for label in Label if label in self.labels]

    def with_rating(self, rating: int) -> Marks:
        """The marks with another rating, clamped to the valid range."""
        return replace(self, rating=_clamped_rating(rating))

    def with_label(self, label: Label, *, on: bool) -> Marks:
        """The marks with one color label set or cleared."""
        labels = self.labels | {label} if on else self.labels - {label}
        return replace(self, labels=frozenset(labels))

    def with_export(self, *, on: bool) -> Marks:
        """The marks with the export mark set or cleared."""
        return replace(self, export=on)

    def to_dict(self) -> dict[str, object]:
        """The stored form, leaving out whatever is unset."""
        data: dict[str, object] = {}
        if self.rating != UNRATED:
            data["rating"] = self.rating
        if self.labels:
            data["labels"] = [label.value for label in self.ordered_labels]
        if self.export:
            data["export"] = True
        return data

    @classmethod
    def from_dict(cls, data: object) -> Marks:
        """Read the stored form, ignoring anything malformed."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            rating=_clamped_rating(data.get("rating", UNRATED)),
            labels=_known_labels(data.get("labels")),
            export=data.get("export") is True,
        )
