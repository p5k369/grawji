"""Tests for marking images through the controller."""

from __future__ import annotations

from pathlib import Path

from grawji.catalog import Entry, with_marks
from grawji.controllers.marks import MarksController
from grawji.marks import REJECTED, Label, Marks
from grawji.sidecar import load_marks


class FakeFolder:
    """The folder model, reduced to what marking touches."""

    def __init__(self, folder: Path, names: list[str]) -> None:
        """Create one empty RAF per name."""
        self.entries: dict[str, Entry] = {}
        for name in names:
            path = folder / name
            path.write_bytes(b"raf")
            self.entries[str(path)] = Entry(path=str(path), name=name)
        self.published: list[str] = []

    def entry_for(self, path: str) -> Entry | None:
        """The entry behind a path."""
        return self.entries.get(path)

    def set_marks(self, path: str, marks: Marks) -> None:
        """Take new marks and remember who was published."""
        self.entries[path] = with_marks(self.entries[path], marks)
        self.published.append(path)


def make(tmp_path: Path, *names: str):
    """A controller over a fake folder, aimed at the first image."""
    folder = FakeFolder(tmp_path, list(names))
    paths = list(folder.entries)
    targets = [paths[0]]
    changed: list[list[str]] = []
    controller = MarksController(
        folder=folder, targets=lambda: list(targets), on_changed=changed.append
    )
    return controller, folder, paths, targets, changed


def test_a_rating_is_stored_and_published(tmp_path: Path) -> None:
    """The sidecar, the folder model and the listener all hear of it."""
    controller, folder, paths, _targets, changed = make(tmp_path, "a.RAF")
    controller.rate(3)
    assert load_marks(paths[0]).rating == 3
    assert folder.entries[paths[0]].marks.rating == 3
    assert changed == [[paths[0]]]


def test_the_same_rating_again_clears_it(tmp_path: Path) -> None:
    """Pressing the current rating toggles it off."""
    controller, folder, paths, _targets, _changed = make(tmp_path, "a.RAF")
    controller.rate(4)
    controller.rate(4)
    assert folder.entries[paths[0]].marks.rating == 0
    assert load_marks(paths[0]) == Marks()


def test_a_toggle_sets_a_mixed_selection_uniformly(tmp_path: Path) -> None:
    """The first target decides, every target follows."""
    controller, folder, paths, targets, _changed = make(
        tmp_path, "a.RAF", "b.RAF"
    )
    targets[:] = paths
    controller.toggle_label(Label.RED, [paths[1]])
    controller.toggle_label(Label.RED)
    assert all(Label.RED in folder.entries[p].marks.labels for p in paths)
    controller.toggle_label(Label.RED)
    assert not any(Label.RED in folder.entries[p].marks.labels for p in paths)


def test_taking_a_reject_back_keeps_other_stars(tmp_path: Path) -> None:
    """Only rejects lose their rating when the reject is lifted."""
    controller, folder, paths, _targets, _changed = make(
        tmp_path, "a.RAF", "b.RAF"
    )
    controller.set_rating([paths[1]], 4)
    controller.toggle_reject([paths[0]])
    assert folder.entries[paths[0]].marks.rating == REJECTED
    controller.set_rejected(paths, on=False)
    assert folder.entries[paths[0]].marks.rating == 0
    assert folder.entries[paths[1]].marks.rating == 4


def test_the_export_mark_toggles(tmp_path: Path) -> None:
    """Export is its own mark, independent of stars and labels."""
    controller, folder, paths, _targets, _changed = make(tmp_path, "a.RAF")
    controller.toggle_export()
    assert folder.entries[paths[0]].marks.export
    controller.toggle_export()
    assert not folder.entries[paths[0]].marks.export


def test_menu_choices_set_the_wanted_state(tmp_path: Path) -> None:
    """Menu items carry the state, so repeating one changes nothing."""
    controller, folder, paths, _targets, changed = make(
        tmp_path, "a.RAF", "b.RAF"
    )
    controller.from_menu("rate", 5, paths)
    controller.from_menu("rate", 5, paths)
    controller.from_menu("label", (Label.BLUE, True), paths)
    controller.from_menu("export", True, [paths[0]])
    controller.from_menu("reject", True, [paths[1]])
    first = folder.entries[paths[0]].marks
    second = folder.entries[paths[1]].marks
    assert first == Marks(
        rating=5, labels=frozenset({Label.BLUE}), export=True
    )
    assert second.rejected and second.labels == {Label.BLUE}
    assert changed[0] == paths
    assert changed[1] == paths


def test_unknown_menu_choices_are_ignored(tmp_path: Path) -> None:
    """A malformed value never reaches the sidecar."""
    controller, folder, paths, _targets, changed = make(tmp_path, "a.RAF")
    controller.from_menu("rate", "five", paths)
    controller.from_menu("label", ("red", True), paths)
    controller.from_menu("bogus", True, paths)
    assert folder.entries[paths[0]].marks == Marks()
    assert changed == []


def test_no_target_does_nothing(tmp_path: Path) -> None:
    """With no open image a shortcut is a quiet no-op."""
    controller, folder, _paths, targets, changed = make(tmp_path, "a.RAF")
    targets.clear()
    controller.rate(2)
    controller.toggle_reject()
    controller.toggle_export()
    controller.toggle_label(Label.GREEN)
    assert changed == []
    assert folder.published == []
