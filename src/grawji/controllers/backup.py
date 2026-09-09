"""Controller for the camera settings backup and restore UI flow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from grawji.camera import camera_backup
from grawji.controllers.camera_ops import CameraOpsController
from grawji.imaging.export import initial_folder
from grawji.settings import Settings

_log = logging.getLogger("grawji")


class BackupController:
    """Drive the camera settings backup and restore dialogs."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        parent: Gtk.Window,
        camera_ops: CameraOpsController,
        settings: Settings,
        save_settings: Callable[[], None],
        set_busy: Callable[[bool, str], None],
        set_status: Callable[[str], None],
        add_toast: Callable[[Adw.Toast], None],
    ) -> None:
        """Wire the controller to its collaborators.

        Args:
            parent: Window the dialogs attach to.
            camera_ops: Worker plumbing for the download/restore transfers.
            settings: last_backup_dir is read and updated.
            save_settings: Persists settings after an edit.
            set_busy: Toggles the busy spinner with a status message.
            set_status: Shows a status-bar message.
            add_toast: Presents a toast.
        """
        self._parent = parent
        self._camera_ops = camera_ops
        self._settings = settings
        self._save_settings = save_settings
        self._set_busy = set_busy
        self._set_status = set_status
        self._add_toast = add_toast

    def backup(self) -> None:
        """Download the camera settings blob and save it to a file."""
        self._set_busy(True, "Downloading camera settings…")
        self._camera_ops.download_settings(
            self._on_downloaded,
            lambda exc: self._on_failed("Backup", exc),
        )

    def _on_downloaded(self, blob: bytes) -> bool:
        """Ask where to save the downloaded settings blob."""
        self._set_busy(False, "Camera settings downloaded.")
        model = camera_backup.model_from_blob(blob) or "fujifilm"
        stamp = GLib.DateTime.new_now_local().format("%Y%m%d-%H%M")
        dialog = Gtk.FileDialog()
        dialog.set_title("Save camera settings backup")
        dialog.set_initial_name(f"{model}-settings-{stamp}.bin")
        start = initial_folder(self._settings.last_backup_dir)
        if start is not None:
            dialog.set_initial_folder(start)

        def saved(_dialog: Any, result: Any) -> None:
            self._save_backup(blob, dialog, result)

        dialog.save(self._parent, None, saved)
        return GLib.SOURCE_REMOVE

    def _save_backup(self, blob: bytes, dialog: Any, result: Any) -> None:
        """Write the blob to the chosen file."""
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        self._settings.last_backup_dir = str(Path(path).parent)
        self._save_settings()
        try:
            Path(path).write_bytes(blob)
        except OSError as exc:
            self._set_status(f"Backup failed: {exc}")
            return
        self._add_toast(Adw.Toast.new("Camera settings backed up."))
        self._set_status(
            "Backup saved (it contains the camera serial, treat it as"
            " private)."
        )

    def restore(self) -> None:
        """Pick a settings backup file and restore it to the camera."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Restore camera settings")
        backups = Gtk.FileFilter()
        backups.set_name("Camera settings backups (*.bin)")
        backups.add_suffix("bin")
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backups)
        filters.append(everything)
        dialog.set_filters(filters)
        dialog.set_default_filter(backups)
        start = initial_folder(self._settings.last_backup_dir)
        if start is not None:
            dialog.set_initial_folder(start)
        dialog.open(self._parent, None, self._on_picked)

    def _on_picked(self, dialog: Any, result: Any) -> None:
        """Confirm before writing the picked blob to the camera."""
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        try:
            blob = Path(path).read_bytes()
        except OSError as exc:
            self._set_status(f"Restore failed: {exc}")
            return
        model = camera_backup.model_from_blob(blob)
        if model is None:
            self._set_status(
                "Restore refused: not a Fujifilm settings backup."
            )
            return
        self._settings.last_backup_dir = str(Path(path).parent)
        self._save_settings()
        self._confirm_restore(blob, model, Path(path).name)

    def _confirm_restore(self, blob: bytes, model: str, name: str) -> None:
        """Present the destructive-restore confirmation dialog."""
        confirm = Adw.AlertDialog(
            heading=f"Restore {model} settings?",
            body=(
                "This overwrites EVERY setting on the connected camera"
                f" with the backup from {name}. The model must match and"
                " the camera validates the file before accepting it."
            ),
        )
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("restore", "Restore")
        confirm.set_response_appearance(
            "restore", Adw.ResponseAppearance.DESTRUCTIVE
        )
        confirm.connect(
            "response",
            lambda _d, response: (
                self._run_restore(blob) if response == "restore" else None
            ),
        )
        confirm.present(self._parent)

    def _run_restore(self, blob: bytes) -> None:
        """Write the blob to the camera via the camera-ops worker."""
        self._set_busy(True, "Restoring camera settings…")
        self._camera_ops.restore_settings(
            blob,
            self._on_restored,
            lambda exc: self._on_failed("Restore", exc),
        )

    def _on_restored(self, model: str) -> bool:
        """Report a finished restore."""
        self._set_busy(False, f"{model} settings restored.")
        self._add_toast(Adw.Toast.new(f"{model} settings restored."))
        return GLib.SOURCE_REMOVE

    def _on_failed(self, what: str, exc: Exception) -> bool:
        """Report a failed backup or restore."""
        _log.warning("%s failed: %s", what, exc)
        self._set_busy(False, f"{what} failed: {exc}")
        return GLib.SOURCE_REMOVE
