"""Full-resolution export: JPEG writing and the batch-export controller."""

from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, GLib, Gtk

from grawji.core import CameraSession, ForeignRafError
from grawji.preview import CameraWorker
from grawji.recipe import Recipe
from grawji.settings import Settings
from grawji.views import imagemeta
from grawji.views.batch_export import BatchExportDialog

SetBusy = Callable[..., None]


def export_basename(raf_path: Path | str) -> str:
    """Build an export filename from the RAF stem."""
    return f"{Path(raf_path).stem}.jpg"


def initial_folder(path: str) -> Gio.File | None:
    """A Gio.File for path if it is an existing directory, else None.

    Used to open an export dialog at the last-used export folder.
    """
    if path and Path(path).is_dir():
        return Gio.File.new_for_path(path)
    return None


def write_jpeg(
    jpeg: bytes,
    path: str,
    *,
    quality: int,
    decode: Callable[[bytes], Any],
) -> None:
    """Write jpeg to path with orientation and rotation baked in.

    decode turns the camera JPEG into the pixbuf to encode (the caller
    supplies it so the preview's manual rotation is applied). Encoding
    and the EXIF transplant happen on a temp file first, then the
    finished bytes are written to the chosen path in one go: that path
    may be an XDG document-portal proxy (Flatpak), which exiv2 cannot
    rewrite in place - doing so leaves a 0-byte file.

    Raises GLib.Error or OSError on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pixbuf = decode(jpeg)
        pixbuf.savev(tmp_path, "jpeg", ["quality"], [str(quality)])
        # GdkPixbuf re-encoding drops all metadata, so copy the camera's
        # EXIF back on (orientation is now baked into the pixels).
        imagemeta.copy_exif(jpeg, tmp_path)
        Path(path).write_bytes(Path(tmp_path).read_bytes())
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class BatchController:
    """Drives a batch export: folder pick, options dialog, worker task."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        parent: Gtk.Widget,
        worker: CameraWorker,
        session: CameraSession,
        settings: Settings,
        get_paths: Callable[[], list[str]],
        get_recipe: Callable[[], Recipe],
        get_current_raf: Callable[[], str | None],
        set_busy: SetBusy,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """Wire the controller to the window's session and callbacks.

        Args:
            parent: The window the dialogs attach to.
            worker: The camera worker the batch task runs on.
            session: The camera session the task drives directly.
            settings: Read and remember the overwrite choice.
            get_paths: Returns the RAF paths to export when begin() is
                called without an explicit list.
            get_recipe: Returns the recipe to render with.
            get_current_raf: Returns the currently open RAF (restored
                after the batch), or None.
            set_busy: The window's busy/status setter, taking the
                keyword arguments busy and status.
            on_status: Sets the status line without the busy plumbing
                (used for per-image progress).
            on_error: Receives a camera failure (on the main loop).
            on_finished: Called after a run completes (not on camera
                failure), e.g. to leave batch-select mode.
        """
        self._parent = parent
        self._worker = worker
        self._session = session
        self._settings = settings
        self._get_paths = get_paths
        self._get_recipe = get_recipe
        self._get_current_raf = get_current_raf
        self._set_busy = set_busy
        self._on_status = on_status
        self._on_error = on_error
        self._on_finished = on_finished
        self._dialog: BatchExportDialog | None = None
        self._cancel: threading.Event | None = None
        self._pending: list[str] = []

    def begin(self, paths: list[str] | None = None) -> str | None:
        """Start the flow with a folder pick; returns a status complaint.

        Args:
            paths: The RAFs to export. Defaults to the whole folder via
                the get_paths callback when omitted.

        Returns None when the folder dialog was shown, or a message for
        the status line when there is nothing to export.
        """
        resolved = list(paths) if paths is not None else self._get_paths()
        if not resolved:
            return "No images selected to export."
        # Capture now, so the run is fixed even if the selection changes.
        self._pending = resolved
        dialog = Gtk.FileDialog()
        dialog.set_title("Export to folder")
        start = initial_folder(self._settings.last_export_dir)
        if start is not None:
            dialog.set_initial_folder(start)
        dialog.select_folder(self._parent, None, self._on_folder_response)
        return None

    def abort(self) -> None:
        """Unstick the dialog after a camera failure killed the batch."""
        if self._cancel is not None:
            self._cancel = None
            if self._dialog is not None:
                self._dialog.force_close()

    def _on_folder_response(self, dialog: Any, result: Any) -> None:
        """Open the batch options dialog for the chosen folder."""
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        out_dir = gfile.get_path()
        if out_dir is None:
            return
        self._settings.last_export_dir = out_dir  # persisted on window close
        self._dialog = BatchExportDialog(
            count=len(self._pending),
            overwrite=self._settings.batch_overwrite,
            on_start=partial(self._start, out_dir),
            on_cancel=self._on_cancel,
        )
        self._dialog.connect("closed", self._on_dialog_closed)
        self._dialog.present(self._parent)

    def _on_dialog_closed(self, _dialog: Any) -> None:
        """Drop the batch dialog reference once it is dismissed."""
        self._dialog = None

    def _start(
        self, out_dir: str, overwrite: bool, skip_foreign: bool
    ) -> None:
        """Render the pending RAFs with the current recipe."""
        self._settings.batch_overwrite = overwrite
        paths = self._pending
        recipe = self._get_recipe()
        total = len(paths)
        current = self._get_current_raf()
        cancel = threading.Event()
        self._cancel = cancel
        self._set_busy(busy=True, status=f"Batch export: 0/{total}…")

        def task() -> dict[str, int]:
            tally = {"exported": 0, "existing": 0, "foreign": 0, "failed": 0}
            for done, raf_file in enumerate(paths, start=1):
                if cancel.is_set():
                    tally["cancelled"] = 1
                    break
                out_path = Path(out_dir, export_basename(raf_file))
                if not overwrite and out_path.exists():
                    tally["existing"] += 1
                else:
                    self._export_one(
                        raf_file, out_path, recipe, skip_foreign, tally
                    )
                GLib.idle_add(self._progress, done, total, Path(raf_file).name)
            if current is not None:
                self._session.open(current)  # restore the open image
            return tally

        self._worker.submit(
            task, on_done=self._on_done, on_error=self._on_error
        )

    def _export_one(
        self,
        raf_file: str,
        out_path: Path,
        recipe: Recipe,
        skip_foreign: bool,
        tally: dict[str, int],
    ) -> None:
        """Convert one RAF into out_path.

        A foreign RAF is skipped (when allowed) and a file-write error is
        counted so the batch carries on.
        """
        try:
            self._session.open(raf_file)
            jpeg = self._session.render(recipe, full_resolution=True)
        except ForeignRafError:
            if not skip_foreign:
                raise
            tally["foreign"] += 1
            return
        try:
            out_path.write_bytes(jpeg)
        except OSError as exc:
            logging.getLogger("grawji").warning(
                "batch export could not write %s: %s", out_path, exc
            )
            tally["failed"] += 1
        else:
            tally["exported"] += 1

    def _on_cancel(self) -> None:
        """Ask the running batch to stop after the current image."""
        if self._cancel is not None:
            self._cancel.set()

    def _progress(self, done: int, total: int, name: str) -> int:
        """Advance the dialog's progress bar (on the main loop)."""
        self._on_status(f"Batch export: {done}/{total}…")
        if self._dialog is not None:
            self._dialog.update(done, total, name)
        return GLib.SOURCE_REMOVE

    def _on_done(self, tally: dict[str, int]) -> None:
        """Report batch completion and show the dialog summary."""
        self._cancel = None
        exported = tally["exported"]
        lead = "Cancelled after" if tally.get("cancelled") else "Exported"
        parts = [f"{lead} {exported} image(s)."]
        if tally["existing"]:
            parts.append(f"Skipped {tally['existing']} already present.")
        if tally["foreign"]:
            parts.append(f"Skipped {tally['foreign']} from another camera.")
        if tally["failed"]:
            parts.append(f"{tally['failed']} failed.")
        summary = " ".join(parts)
        self._set_busy(busy=False, status=summary)
        if self._dialog is not None:
            self._dialog.finish(summary)
        if self._on_finished is not None:
            self._on_finished()
