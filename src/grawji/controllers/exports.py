"""Controllers for exporting."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk

from grawji import mainloop, sidecar
from grawji.camera.core import (
    CameraSession,
    ForeignRafError,
    recipe_from_profile,
)
from grawji.camera.preview import CameraWorker
from grawji.crop import CropRotate
from grawji.imaging.export import (
    SetBusy,
    baked_pixbuf,
    camera_file_type,
    corrected_path,
    delivered_format,
    export_basename,
    export_format,
    framing_active,
    geometry_for,
    initial_folder,
    recipe_for_format,
    resize_active,
    sidecar_decode,
    with_border,
    with_max_edge,
    write_jpeg,
    write_passthrough,
)
from grawji.imaging.heif_codec import HeifError
from grawji.imaging.tiff import TiffError
from grawji.recipe import Recipe
from grawji.settings import Settings
from grawji.views.batch_export import BatchExportDialog
from grawji.views.preview_view import oriented_pixbuf

_LOG = logging.getLogger("grawji")


@dataclass(frozen=True)
class _ExportJob:
    """What a single export needs, read before the worker starts."""

    path: str
    recipe: Recipe
    crop: CropRotate
    comment: str
    wanted: str
    identity: bool
    dropped: str = ""


_EXPORT_TITLES = {
    "jpeg": "Export JPEG",
    "jxl": "Export JPEG XL",
    "jxl8": "Export JPEG XL",
    "jxl16": "Export JPEG XL",
    "heif": "Export HEIF",
    "tiff8": "Export TIFF",
    "tiff16": "Export TIFF",
}


class SingleExportController:
    """Drives a single full-resolution export: dialog, render, write."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        parent: Gtk.Widget,
        worker: CameraWorker,
        session: CameraSession,
        settings: Settings,
        save_settings: Callable[[], None],
        get_recipe: Callable[[], Recipe],
        get_provenance: Callable[[], str],
        get_current_raf: Callable[[], str | None],
        base_identity: Callable[[], bool],
        get_crop: Callable[[], CropRotate],
        set_busy: SetBusy,
        on_error: Callable[[Exception], None],
        on_status_link: Callable[[str, str], None],
    ) -> None:
        """Wire the controller to the window's session and callbacks.

        Args:
            parent: The window the save dialog attaches to.
            worker: Queues the full-resolution render.
            session: The open camera session the render runs on.
            settings: Live settings (quality, last export folder).
            save_settings: Persists settings after an edit.
            get_recipe: The recipe currently in the panel.
            get_provenance: A short description of the recipe/source
                the export was made with.
            get_current_raf: The open RAF's path for the default name.
            base_identity: Whether the current geometry would change no
                pixels.
            get_crop: The geometry currently committed in the preview,
                for the export paths that apply it to raw samples.
            set_busy: Toggles the busy spinner with a status line.
            on_error: Reports a camera error.
            on_status_link: Shows the clickable "Exported to" status.
        """
        self._parent = parent
        self._worker = worker
        self._session = session
        self._settings = settings
        self._save_settings = save_settings
        self._get_recipe = get_recipe
        self._get_provenance = get_provenance
        self._get_current_raf = get_current_raf
        self._base_identity = base_identity
        self._get_crop = get_crop
        self._set_busy = set_busy
        self._on_error = on_error
        self._on_status_link = on_status_link

    def begin(self) -> None:
        """Show a save dialog for a full-resolution export."""
        fmt = export_format(self._settings)
        dialog = Gtk.FileDialog()
        dialog.set_title(_EXPORT_TITLES[fmt])
        dialog.set_initial_name(
            export_basename(
                self._get_current_raf() or "grawji-export", fmt=fmt
            )
        )
        start = initial_folder(self._settings.last_export_dir)
        if start is not None:
            dialog.set_initial_folder(start)
        dialog.save(self._parent, None, self._on_response)

    def _on_response(self, dialog: Any, result: Any) -> None:
        """Render at full resolution and write to the chosen path."""
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        self._settings.last_export_dir = str(Path(path).parent)
        self._save_settings()
        self._set_busy(busy=True, status="Rendering full-resolution export…")
        wanted = export_format(self._settings)
        recipe, dropped = recipe_for_format(wanted, self._get_recipe())
        if dropped:
            _LOG.info("export: %s", dropped)
        job = _ExportJob(
            path=path,
            recipe=recipe,
            crop=self._get_crop(),
            comment=self._get_provenance(),
            wanted=wanted,
            identity=self._base_identity(),
            dropped=dropped,
        )
        self._worker.submit(
            partial(self._render_and_write, job),
            on_done=self._on_written,
            on_error=self._on_error,
        )

    def _render_and_write(self, job: _ExportJob) -> tuple[str, str, str]:
        """Render and write the export."""
        rendered = self._session.render(
            job.recipe,
            full_resolution=True,
            file_type=camera_file_type(job.wanted),
        )
        # The body may have ignored a HEIF or TIFF request and sent
        # JPEG, so the file is named for what actually arrived.
        fmt = delivered_format(rendered, job.wanted)
        path = corrected_path(job.path, fmt)
        needs_pixels = (
            not job.identity
            or framing_active(self._settings)
            or resize_active(self._settings)
        )
        if not needs_pixels:
            write_passthrough(
                rendered,
                path,
                artist=self._settings.export_artist,
                rights=self._settings.export_copyright,
                comment=job.comment,
                fmt=fmt,
                quality=self._settings.jpeg_quality,
            )
        else:
            write_jpeg(
                rendered,
                path,
                quality=self._settings.jpeg_quality,
                decode=with_border(
                    with_max_edge(
                        partial(baked_pixbuf, crop=job.crop), self._settings
                    ),
                    self._settings,
                ),
                artist=self._settings.export_artist,
                rights=self._settings.export_copyright,
                comment=job.comment,
                fmt=fmt,
                geometry=geometry_for(job.crop, self._settings),
            )
        return path, fmt, job.dropped

    def _on_written(self, written: tuple[str, str, str]) -> None:
        """Report the finished export."""
        path, fmt, dropped = written
        wanted = export_format(self._settings)
        if fmt != wanted:
            self._set_busy(
                busy=False,
                status=(
                    f"This body cannot render {wanted.upper()}, "
                    "exported JPEG instead."
                ),
            )
        else:
            self._set_busy(busy=False, status=f"Exported. {dropped}".strip())
        self._on_status_link(f"Exported to {path}", path)


class BatchController:
    """Drives a batch export."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        parent: Gtk.Widget,
        worker: CameraWorker,
        session: CameraSession,
        settings: Settings,
        get_paths: Callable[[], list[str]],
        get_recipe: Callable[[], Recipe],
        get_provenance: Callable[[], str],
        get_current_raf: Callable[[], str | None],
        set_busy: SetBusy,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_finished: Callable[[], None] | None = None,
        on_status_link: Callable[[str, str], None] | None = None,
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
            get_provenance: A short description of the recipe/source
                the batch renders with.
            get_current_raf: Returns the currently open RAF, or None.
            set_busy: The window's busy/status setter, taking the
                keyword arguments busy and status.
            on_status: Sets the status line without the busy plumbing.
            on_error: Receives a camera failure.
            on_finished: Called after a run completes (not on camera
                failure), e.g. to leave batch-select mode.
            on_status_link: Sets a status line whose text opens the
                given path on click.
        """
        self._parent = parent
        self._worker = worker
        self._session = session
        self._settings = settings
        self._get_paths = get_paths
        self._get_recipe = get_recipe
        self._get_provenance = get_provenance
        self._get_current_raf = get_current_raf
        self._set_busy = set_busy
        self._on_status = on_status
        self._on_error = on_error
        self._on_finished = on_finished
        self._on_status_link = on_status_link
        self._dialog: BatchExportDialog | None = None
        self._cancel: threading.Event | None = None
        self._pending: list[str] = []
        self._out_dir: str | None = None
        self._dropped = ""

    def begin(self, paths: list[str] | None = None) -> str | None:
        """Start the flow with a folder pick."""
        resolved = list(paths) if paths is not None else self._get_paths()
        if not resolved:
            return "No images selected to export."
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
        self._settings.last_export_dir = out_dir
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
        self._out_dir = out_dir
        paths = self._pending
        recipe = self._get_recipe()
        comment = self._get_provenance()
        total = len(paths)
        current = self._get_current_raf()
        cancel = threading.Event()
        self._cancel = cancel
        self._set_busy(busy=True, status=f"Batch export: 0/{total}…")

        wanted = export_format(self._settings)
        recipe, dropped = recipe_for_format(wanted, recipe)
        if dropped:
            _LOG.info("batch export: %s", dropped)
        self._dropped = dropped

        def task() -> dict[str, int]:
            tally = {"exported": 0, "existing": 0, "foreign": 0, "failed": 0}
            for done, raf_file in enumerate(paths, start=1):
                if cancel.is_set():
                    tally["cancelled"] = 1
                    break
                out_path = Path(out_dir, export_basename(raf_file, fmt=wanted))
                if not overwrite and out_path.exists():
                    tally["existing"] += 1
                else:
                    self._export_one(
                        raf_file,
                        out_path,
                        recipe,
                        comment,
                        skip_foreign,
                        tally,
                    )
                mainloop.call(self._progress, done, total, Path(raf_file).name)
            if current is not None:
                self._session.open(current)
            return tally

        self._worker.submit(
            task, on_done=self._on_done, on_error=self._on_error
        )

    def _export_one(
        self,
        raf_file: str,
        out_path: Path,
        recipe: Recipe,
        comment: str,
        skip_foreign: bool,
        tally: dict[str, int],
    ) -> None:
        """Convert one RAF into out_path."""
        try:
            self._session.open(raf_file)
            fmt = export_format(self._settings)
            rendered = self._session.render(
                replace(recipe, exposure=self._image_exposure(raf_file)),
                full_resolution=True,
                file_type=camera_file_type(fmt),
            )
        except ForeignRafError:
            if not skip_foreign:
                raise
            tally["foreign"] += 1
            return
        decode = sidecar_decode(raf_file)
        needs_pixels = framing_active(self._settings) or resize_active(
            self._settings
        )
        if decode is None and needs_pixels:
            decode = oriented_pixbuf
        if decode is not None:
            decode = with_border(
                with_max_edge(decode, self._settings), self._settings
            )
        fmt = delivered_format(rendered, fmt)
        out_path = Path(corrected_path(str(out_path), fmt))
        try:
            if decode is None:
                write_passthrough(
                    rendered,
                    str(out_path),
                    artist=self._settings.export_artist,
                    rights=self._settings.export_copyright,
                    comment=comment,
                    fmt=fmt,
                    quality=self._settings.jpeg_quality,
                )
            else:
                write_jpeg(
                    rendered,
                    str(out_path),
                    quality=self._settings.jpeg_quality,
                    decode=decode,
                    artist=self._settings.export_artist,
                    rights=self._settings.export_copyright,
                    comment=comment,
                    fmt=fmt,
                    geometry=geometry_for(
                        sidecar.load_crop(raf_file), self._settings
                    ),
                )
        except (GLib.Error, OSError, HeifError, TiffError) as exc:
            logging.getLogger("grawji").warning(
                "batch export could not write %s: %s", out_path, exc
            )
            tally["failed"] += 1
        else:
            tally["exported"] += 1

    def _image_exposure(self, raf_file: str) -> float:
        """The EV to render raf_file."""
        stored = sidecar.load_exposure(raf_file)
        if stored is not None:
            return stored
        profile = self._session.profile
        if profile is None:
            return 0.0
        return recipe_from_profile(profile).exposure

    def _on_cancel(self) -> None:
        """Ask the running batch to stop after the current image."""
        if self._cancel is not None:
            self._cancel.set()

    def _progress(self, done: int, total: int, name: str) -> int:
        """Advance the dialog's progress bar."""
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
        if self._dropped:
            parts.append(self._dropped)
        summary = " ".join(parts)
        self._set_busy(busy=False, status=summary)
        if exported and self._out_dir and self._on_status_link is not None:
            self._on_status_link(summary, self._out_dir)
        if self._dialog is not None:
            self._dialog.finish(summary)
        if self._on_finished is not None:
            self._on_finished()
