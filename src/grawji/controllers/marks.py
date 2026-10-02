"""Controller for marking images."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from grawji import sidecar
from grawji.catalog import Entry
from grawji.marks import REJECTED, UNRATED, Label, Marks

Edit = Callable[[Marks], Marks]


class MarkedFolder(Protocol):
    """The part of the folder model marking needs."""

    def entry_for(self, path: str) -> Entry | None:
        """The entry behind a path."""

    def set_marks(self, path: str, marks: Marks) -> None:
        """Take an image's new marks into the folder."""


class MarksController:
    """Apply marks to the images they target, and store them."""

    def __init__(
        self,
        *,
        folder: MarkedFolder,
        targets: Callable[[], list[str]],
        on_changed: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Wire the controller to the folder.

        Args:
            folder: Where the entries live.
            targets: The images a keyboard or bar mark applies to.
            on_changed: Called with the paths whose marks changed.
        """
        self._folder = folder
        self._targets = targets
        self._on_changed = on_changed

    def marks_of(self, path: str | None) -> Marks:
        """The marks of one image, none when it is unknown."""
        entry = self._folder.entry_for(path) if path else None
        return entry.marks if entry is not None else Marks()

    def marked_for_export(self, paths: list[str]) -> list[str]:
        """The paths marked for export, in their order."""
        return [path for path in paths if self.marks_of(path).export]

    def rejected(self, paths: list[str]) -> list[str]:
        """The paths marked as rejects, in their order."""
        return [path for path in paths if self.marks_of(path).rejected]

    def without_rejects(self, paths: list[str]) -> tuple[list[str], int]:
        """The paths a batch should export, and how many rejects it skips."""
        kept = [path for path in paths if not self.marks_of(path).rejected]
        return kept, len(paths) - len(kept)

    def rate(self, rating: int, paths: list[str] | None = None) -> None:
        """Set a star rating. Repeating the current rating clears it."""
        targets = self._resolve(paths)
        if self.marks_of(targets[0] if targets else None).rating == rating:
            rating = UNRATED
        self.apply(targets, lambda m: m.with_rating(rating))

    def toggle_reject(self, paths: list[str] | None = None) -> None:
        """Reject the targets, or take a reject back."""
        targets = self._resolve(paths)
        reject = not self.marks_of(targets[0] if targets else None).rejected
        self.set_rejected(targets, on=reject)

    def toggle_label(
        self, label: Label, paths: list[str] | None = None
    ) -> None:
        """Set or clear one color label."""
        targets = self._resolve(paths)
        first = self.marks_of(targets[0] if targets else None)
        self.set_label(targets, label, on=label not in first.labels)

    def toggle_export(self, paths: list[str] | None = None) -> None:
        """Mark the targets for export, or unmark them."""
        targets = self._resolve(paths)
        first = self.marks_of(targets[0] if targets else None)
        self.set_export(targets, on=not first.export)

    def set_rating(self, paths: list[str], rating: int) -> None:
        """Give every path the same rating."""
        self.apply(paths, lambda m: m.with_rating(rating))

    def set_rejected(self, paths: list[str], *, on: bool) -> None:
        """Reject every path, or clear a reject without touching stars."""

        def edit(marks: Marks) -> Marks:
            if on:
                return marks.with_rating(REJECTED)
            return marks.with_rating(UNRATED) if marks.rejected else marks

        self.apply(paths, edit)

    def set_label(self, paths: list[str], label: Label, *, on: bool) -> None:
        """Set or clear one color label on every path."""
        self.apply(paths, lambda m: m.with_label(label, on=on))

    def set_export(self, paths: list[str], *, on: bool) -> None:
        """Set or clear the export mark on every path."""
        self.apply(paths, lambda m: m.with_export(on=on))

    def from_menu(self, kind: str, value: object, paths: list[str]) -> None:
        """Apply a card menu's mark choice to the paths it was opened on."""
        if kind == "rate" and isinstance(value, int):
            self.set_rating(paths, value)
        elif kind == "reject" and isinstance(value, bool):
            self.set_rejected(paths, on=value)
        elif kind == "export" and isinstance(value, bool):
            self.set_export(paths, on=value)
        elif kind == "label" and isinstance(value, tuple):
            label, on = value
            if isinstance(label, Label) and isinstance(on, bool):
                self.set_label(paths, label, on=on)

    def apply(self, paths: list[str], edit: Edit) -> None:
        """Change, store and publish the marks of every path."""
        changed = []
        for path in paths:
            entry = self._folder.entry_for(path)
            if entry is None:
                continue
            marks = edit(entry.marks)
            if marks == entry.marks:
                continue
            sidecar.save_marks(path, marks)
            self._folder.set_marks(path, marks)
            changed.append(path)
        if changed and self._on_changed is not None:
            self._on_changed(changed)

    def _resolve(self, paths: list[str] | None) -> list[str]:
        """The given paths, or the current targets."""
        return list(paths) if paths is not None else self._targets()
