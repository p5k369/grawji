"""Controller for file operations started from the UI."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from grawji import fileops
from grawji.settings import Settings
from grawji.views.export import initial_folder
from grawji.views.filmstrip import FilmStrip

_log = logging.getLogger("grawji")


class FileOpsController:
    """Copy, move and trash RAFs on behalf of the window."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        parent: Gtk.Window,
        settings: Settings,
        save_settings: Callable[[], None],
        filmstrip: Callable[[], FilmStrip],
        current_raf: Callable[[], Path | None],
        begin_export: Callable[[list[str]], str | None],
        set_status: Callable[[str], None],
        add_toast: Callable[[Adw.Toast], None],
    ) -> None:
        """Wire the controller to its collaborators.

        Args:
            parent: Window the dialogs attach to.
            settings: Live settings, save_settings persists them after change.
            save_settings: Persists settings after an edit.
            filmstrip: Returns the filmstrip.
            current_raf: Returns the open image's path, or None.
            begin_export: Starts a batch export of paths, returning a
                complaint to show or None when it started.
            set_status: Shows a status-bar message.
            add_toast: Presents a toast.
        """
        self._parent = parent
        self._settings = settings
        self._save_settings = save_settings
        self._filmstrip = filmstrip
        self._current_raf = current_raf
        self._begin_export = begin_export
        self._set_status = set_status
        self._add_toast = add_toast
        self._undo_moves: list[tuple[str, str]] = []

    def on_file_action(self, action: str, paths: list[str]) -> None:
        """Run a filmstrip context-menu file operation."""
        if action == "export":
            complaint = self._begin_export(paths)
            if complaint is not None:
                self._set_status(complaint)
        elif action == "copy":
            self.copy_paths(paths)
        elif action == "move":
            self.move_paths(paths)
        elif action == "trash":
            self.trash_paths(paths, confirm=len(paths) > 1)

    def on_tree_drop(
        self, paths: list[str], folder: str, copy: bool | None
    ) -> None:
        """Move or copy images dropped onto a folder-tree row."""
        if copy is None:
            copy = self._settings.drag_action == "copy"
        self._run(("copy" if copy else "move"), paths, folder)

    def copy_paths(self, paths: list[str]) -> None:
        """Pick a destination folder and copy paths there."""
        self._pick_folder("Copy to folder", "copy", paths)

    def move_paths(self, paths: list[str]) -> None:
        """Pick a destination folder and move paths there."""
        self._pick_folder("Move to folder", "move", paths)

    def handle_delete(self) -> bool:
        """Delete-key policy: the marked images, else the open one."""
        strip = self._filmstrip()
        paths = strip.selected_paths
        if paths:
            self.trash_paths(paths, confirm=len(paths) > 1)
            return True
        if strip.in_select_mode:
            return False
        if self._current_raf() is not None:
            self.trash_current()
            return True
        return False

    def trash_paths(self, paths: list[str], *, confirm: bool) -> None:
        """Trash paths, optionally asking first."""
        if not paths:
            return
        if not confirm:
            self._run("trash", paths, None)
            return
        dialog = Adw.AlertDialog(
            heading=f"Move {len(paths)} images to Trash?",
            body="They can be restored from the file manager's Trash.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("trash", "Move to Trash")
        dialog.set_response_appearance(
            "trash", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect(
            "response",
            lambda _d, response: (
                self._run("trash", paths, None)
                if response == "trash"
                else None
            ),
        )
        dialog.present(self._parent)

    def trash_current(self) -> None:
        """Trash the open image and advance to its neighbor."""
        current = self._current_raf()
        if current is None:
            return
        path = str(current)
        strip = self._filmstrip()
        strip_paths = strip.paths
        following = None
        if path in strip_paths:
            index = strip_paths.index(path)
            remaining = strip_paths[index + 1 :] + strip_paths[:index][::-1]
            following = remaining[0] if remaining else None
        self._run("trash", [path], None)
        if following is not None:
            strip.select_path(following)

    def _pick_folder(self, title: str, kind: str, paths: list[str]) -> None:
        """Folder dialog for a copy/move, remembering the destination."""
        if not paths:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(title)
        start = initial_folder(self._settings.last_fileop_dir)
        if start is not None:
            dialog.set_initial_folder(start)

        def on_response(dlg: Any, result: Any) -> None:
            try:
                gfile = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            folder = gfile.get_path()
            if folder is None:
                return
            self._settings.last_fileop_dir = folder
            self._save_settings()
            self._run(kind, paths, folder)

        dialog.select_folder(self._parent, None, on_response)

    def _run(self, kind: str, paths: list[str], dest: str | None) -> None:
        """Run a file operation off the main thread and toast the result."""

        def work() -> None:
            moves: list[tuple[str, str]] = []
            failed = 0
            for path in paths:
                try:
                    if kind == "copy":
                        fileops.copy_raf(path, dest or "")
                    elif kind == "move":
                        target = fileops.move_raf(path, dest or "")
                        moves.append((path, str(target)))
                    else:
                        fileops.trash_raf(path)
                except (OSError, GLib.Error) as exc:
                    _log.warning("%s failed for %s: %s", kind, path, exc)
                    failed += 1
            done = len(paths) - failed
            GLib.idle_add(self._on_done, kind, done, failed, moves)

        threading.Thread(
            target=work, name="grawji-fileops", daemon=True
        ).start()

    def _on_done(
        self,
        kind: str,
        done: int,
        failed: int,
        moves: list[tuple[str, str]],
    ) -> bool:
        """Toast the outcome."""
        noun = "image" if done == 1 else "images"
        text = {
            "copy": f"Copied {done} {noun}.",
            "move": f"Moved {done} {noun}.",
            "trash": f"Moved {done} {noun} to Trash.",
        }[kind]
        if failed:
            text += f" {failed} failed."
        toast = Adw.Toast.new(text)
        if kind == "move" and moves:
            self._undo_moves = moves
            toast.set_button_label("Undo")
            toast.connect("button-clicked", self._on_undo_moves)
        self._add_toast(toast)
        return GLib.SOURCE_REMOVE

    def _on_undo_moves(self, _toast: Any) -> None:
        """Move the last batch of moved images back where they were."""
        moves, self._undo_moves = self._undo_moves, []

        def work() -> None:
            for source, target in moves:
                try:
                    fileops.move_raf(target, str(Path(source).parent))
                except (OSError, GLib.Error) as exc:
                    _log.warning("undo move failed for %s: %s", target, exc)
            GLib.idle_add(self._on_undo_done)

        threading.Thread(
            target=work, name="grawji-fileops", daemon=True
        ).start()

    def _on_undo_done(self) -> bool:
        """Report the undone move."""
        self._add_toast(Adw.Toast.new("Move undone."))
        return GLib.SOURCE_REMOVE
