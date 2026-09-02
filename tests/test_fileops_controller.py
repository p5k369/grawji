"""Tests for FileOpsController."""

from __future__ import annotations

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import grawji.controllers.fileops as module
from grawji.controllers.fileops import FileOpsController
from grawji.settings import Settings


class ImmediateThread:
    """threading.Thread stand-in that runs the target synchronously."""

    def __init__(self, *, target, name=None, daemon=None, args=()):
        """Record the target like threading.Thread would."""
        self._target = target
        self._args = args

    def start(self):
        """Run the target in place of spawning a thread."""
        self._target(*self._args)


class FakeStrip:
    """The slice of FilmStrip the controller talks to."""

    def __init__(self, *, paths=(), selected=(), select_mode=False):
        """Set up the strip state a test needs."""
        self.paths = list(paths)
        self.selected_paths = list(selected)
        self.in_select_mode = select_mode
        self.selected_after_trash: list[str] = []

    def select_path(self, path):
        """Record the auto-advance selection."""
        self.selected_after_trash.append(path)
        return True


@pytest.fixture
def immediate(monkeypatch):
    """Run worker threads and idle callbacks synchronously."""
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(module.GLib, "idle_add", lambda fn, *a: fn(*a))


def make_controller(
    *,
    strip=None,
    settings=None,
    current=None,
    complaints=None,
):
    """A controller with recording fakes for every collaborator."""
    state: dict[str, list[object]] = {
        "toasts": [],
        "statuses": [],
        "exports": [],
    }
    strip = strip or FakeStrip()

    def begin_export(paths):
        state["exports"].append(paths)
        return complaints

    controller = FileOpsController(
        parent=None,
        settings=settings or Settings(),
        save_settings=lambda: None,
        filmstrip=lambda: strip,
        current_raf=lambda: current,
        begin_export=begin_export,
        set_status=state["statuses"].append,
        add_toast=state["toasts"].append,
    )
    return controller, strip, state


def test_open_with_launches_the_app_chooser(monkeypatch):
    """Open With asks the portal chooser for exactly that file."""
    launched: list[tuple[str, bool]] = []

    class FakeLauncher:
        def __init__(self, *, file):
            self._file = file
            self._ask = False

        def set_always_ask(self, ask):
            self._ask = ask

        def launch(self, _parent, _cancellable, _done):
            launched.append((self._file.get_path(), self._ask))

    monkeypatch.setattr(module.Gtk, "FileLauncher", FakeLauncher)
    controller, _strip, _state = make_controller()
    controller.on_file_action("open-with", ["/pics/a.RAF", "/pics/b.RAF"])
    assert launched == [("/pics/a.RAF", True)]


def test_export_action_routes_to_batch():
    """The context menu's export starts a batch with those paths."""
    controller, _strip, state = make_controller()
    controller.on_file_action("export", ["a.RAF"])
    assert state["exports"] == [["a.RAF"]]
    assert state["statuses"] == []


def test_export_complaint_reaches_the_status_line():
    """A batch complaint is surfaced, not swallowed."""
    controller, _strip, state = make_controller(complaints="No camera.")
    controller.on_file_action("export", ["a.RAF"])
    assert state["statuses"] == ["No camera."]


def test_copy_and_move_route_to_the_folder_picker(monkeypatch):
    """Copy/move context actions open the destination picker."""
    controller, _strip, _state = make_controller()
    picked = []
    monkeypatch.setattr(
        controller,
        "_pick_folder",
        lambda title, kind, paths: picked.append((title, kind, paths)),
    )
    controller.on_file_action("copy", ["a"])
    controller.on_file_action("move", ["b"])
    assert picked == [
        ("Copy to folder", "copy", ["a"]),
        ("Move to folder", "move", ["b"]),
    ]


def test_tree_drop_uses_the_configured_default(immediate, monkeypatch):
    """An unmodified drop follows the drag_action setting."""
    moves: list[str] = []
    copies: list[str] = []

    def fake_move(path, _dest):
        moves.append(path)
        return Path(path)

    def fake_copy(path, _dest):
        copies.append(path)
        return Path(path)

    monkeypatch.setattr(module.fileops, "move_raf", fake_move)
    monkeypatch.setattr(module.fileops, "copy_raf", fake_copy)
    settings = Settings()
    controller, _strip, _state = make_controller(settings=settings)
    controller.on_tree_drop(["a"], "/dest", None)
    assert moves == ["a"] and copies == []
    settings.drag_action = "copy"
    controller.on_tree_drop(["b"], "/dest", None)
    assert copies == ["b"]
    controller.on_tree_drop(["c"], "/dest", False)
    assert moves == ["a", "c"]


def test_delete_prefers_the_marked_images(immediate, monkeypatch):
    """Delete trashes the marked set."""
    trashed = []
    monkeypatch.setattr(module.fileops, "trash_raf", trashed.append)
    strip = FakeStrip(selected=["m.RAF"])
    controller, _strip, state = make_controller(strip=strip)
    assert controller.handle_delete() is True
    assert trashed == ["m.RAF"]
    assert "Trash" in state["toasts"][0].get_title()


def test_delete_in_empty_select_mode_does_nothing(immediate):
    """Batch-select mode with nothing selected leaves Delete alone."""
    strip = FakeStrip(select_mode=True)
    controller, _strip, _state = make_controller(strip=strip)
    assert controller.handle_delete() is False


def test_delete_trashes_the_open_image_and_advances(immediate, monkeypatch):
    """Without marks, Delete trashes the open image and moves on."""
    trashed = []
    monkeypatch.setattr(module.fileops, "trash_raf", trashed.append)
    strip = FakeStrip(paths=["a.RAF", "b.RAF", "c.RAF"])
    controller, _strip, _state = make_controller(
        strip=strip, current=Path("b.RAF")
    )
    assert controller.handle_delete() is True
    assert trashed == ["b.RAF"]
    assert strip.selected_after_trash == ["c.RAF"]


def test_failed_operations_are_tallied_in_the_toast(immediate, monkeypatch):
    """Failures count into the toast instead of raising."""

    def explode(_p, _d):
        raise OSError("disk full")

    monkeypatch.setattr(module.fileops, "copy_raf", explode)
    controller, _strip, state = make_controller()
    controller._run("copy", ["a", "b"], "/dest")
    assert "0 images" in state["toasts"][0].get_title()
    assert "2 failed" in state["toasts"][0].get_title()


def test_move_offers_undo_and_undo_moves_back(immediate, monkeypatch):
    """A move toast carries Undo."""
    moves = []

    def fake_move(path, dest):
        moves.append((str(path), str(dest)))
        return Path(dest) / Path(path).name

    monkeypatch.setattr(module.fileops, "move_raf", fake_move)
    controller, _strip, state = make_controller()
    controller._run("move", ["/src/a.RAF"], "/dest")
    toast = state["toasts"][0]
    assert toast.get_button_label() == "Undo"
    controller._on_undo_moves(toast)
    assert moves == [
        ("/src/a.RAF", "/dest"),
        ("/dest/a.RAF", "/src"),
    ]
    assert state["toasts"][-1].get_title() == "Move undone."
