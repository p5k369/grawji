"""Main application window (layout in ui/grawji.ui via Gtk.Template)."""

from __future__ import annotations

from functools import partial
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import rawji  # noqa: E402
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from grawji import raf  # noqa: E402
from grawji.core import CameraSession, ForeignRafError  # noqa: E402
from grawji.filmstrip import FilmStrip  # noqa: E402
from grawji.preview import CameraWorker  # noqa: E402
from grawji.recipe import Recipe  # noqa: E402

# Film-simulation names offered in the dropdown (rawji's enum order).
_FILM_SIMULATIONS = [e.name for e in rawji.FilmSimulation]

# Manual rotation (degrees clockwise) -> GdkPixbuf rotation.
_ROTATIONS = {
    90: GdkPixbuf.PixbufRotation.CLOCKWISE,
    180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
    270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
}

# The layout lives in grawji.ui (shipped as package data, designed in
# Cambalache); Gtk.Template binds it to this class at runtime.
_UI = (
    resources.files("grawji")
    .joinpath("ui", "grawji.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class MainWindow(Adw.ApplicationWindow):
    """grawji main window: browse RAFs, tune recipe, preview, export."""

    __gtype_name__ = "MainWindow"

    picture = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    status = Gtk.Template.Child()
    film_dropdown = Gtk.Template.Child()
    rotate_left = Gtk.Template.Child()
    rotate_right = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    filmstrip_slot = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire up the camera worker, dropdown model and filmstrip."""
        super().__init__(**kwargs)
        self._session = CameraSession()
        self._worker = CameraWorker(self._session, dispatch=GLib.idle_add)
        self._worker.start()
        self.connect("close-request", self._on_close_request)

        self._rotation = 0
        self._last_jpeg: bytes | None = None

        self.film_dropdown.set_model(Gtk.StringList.new(_FILM_SIMULATIONS))
        self._filmstrip = FilmStrip(on_select=self._on_raf_selected)
        self.filmstrip_slot.append(self._filmstrip)

    def _current_recipe(self) -> Recipe:
        """Build a recipe from the current dropdown selection."""
        name = _FILM_SIMULATIONS[self.film_dropdown.get_selected()]
        return Recipe(film_simulation=name)

    @Gtk.Template.Callback()
    def _on_open_folder_clicked(self, _button: Any) -> None:
        """Show a folder picker to populate the filmstrip."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Open folder of RAFs")
        dialog.select_folder(self, None, self._on_folder_response)

    def _on_folder_response(self, dialog: Any, result: Any) -> None:
        """Scan the chosen folder, or do nothing if cancelled."""
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is not None:
            self._filmstrip.scan(path)
            self.status.set_label("Select an image from the filmstrip.")

    def _on_raf_selected(self, raf_path: str) -> None:
        """Show the embedded preview instantly, then open the RAF."""
        self._rotation = 0
        try:
            self._show_jpeg(raf.embedded_jpeg(raf_path))
        except (ValueError, OSError):
            self._last_jpeg = None
        self.set_title(f"grawji — {Path(raf_path).name}")
        self._set_busy(busy=True, status="Loading RAF…")
        self._worker.open(
            raf_path, on_done=self._on_opened, on_error=self._on_error
        )

    def _on_opened(self, _result: object) -> None:
        """Render the first preview once the RAF is loaded."""
        self._render_preview()

    @Gtk.Template.Callback()
    def _on_film_changed(self, _dropdown: Any, _param: Any) -> None:
        """Re-render the preview when the film simulation changes."""
        if self._session.is_open:
            self._render_preview()

    def _render_preview(self) -> None:
        """Queue a fast (non-full-resolution) preview render."""
        self._set_busy(busy=True, status="Rendering preview…")
        self._worker.render(
            self._current_recipe(),
            full_resolution=False,
            on_done=self._on_preview,
            on_error=self._on_error,
        )

    def _on_preview(self, jpeg: bytes) -> None:
        """Display a finished preview render."""
        self._show_jpeg(jpeg)
        self._set_busy(busy=False, status="Ready.")

    @Gtk.Template.Callback()
    def _on_rotate_left(self, _button: Any) -> None:
        """Rotate the displayed image 90 degrees counter-clockwise."""
        self._rotate(-90)

    @Gtk.Template.Callback()
    def _on_rotate_right(self, _button: Any) -> None:
        """Rotate the displayed image 90 degrees clockwise."""
        self._rotate(90)

    def _rotate(self, degrees: int) -> None:
        """Update the rotation and redisplay the current image."""
        self._rotation = (self._rotation + degrees) % 360
        if self._last_jpeg is not None:
            self._show_jpeg(self._last_jpeg)

    @Gtk.Template.Callback()
    def _on_export_clicked(self, _button: Any) -> None:
        """Show a save dialog for a full-resolution export."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export JPEG")
        dialog.set_initial_name("grawji-export.jpg")
        dialog.save(self, None, self._on_export_response)

    def _on_export_response(self, dialog: Any, result: Any) -> None:
        """Render at full resolution and write to the chosen path."""
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        self._set_busy(busy=True, status="Rendering full-resolution export…")
        self._worker.render(
            self._current_recipe(),
            full_resolution=True,
            on_done=partial(self._on_exported, path),
            on_error=self._on_error,
        )

    def _on_exported(self, path: str, jpeg: bytes) -> None:
        """Save the exported JPEG with orientation and rotation baked in."""
        try:
            pixbuf = self._pixbuf_from_jpeg(jpeg)
            pixbuf.savev(path, "jpeg", ["quality"], ["95"])
        except (GLib.Error, OSError) as exc:
            self._set_busy(busy=False, status=f"Export failed: {exc}")
            return
        self._set_busy(busy=False, status=f"Exported to {path}")

    def _pixbuf_from_jpeg(self, jpeg: bytes) -> Any:
        """Decode JPEG bytes, applying EXIF orientation and rotation."""
        loader = GdkPixbuf.PixbufLoader()
        loader.write(jpeg)
        loader.close()
        pixbuf = loader.get_pixbuf().apply_embedded_orientation()
        rotation = _ROTATIONS.get(self._rotation)
        return pixbuf.rotate_simple(rotation) if rotation else pixbuf

    def _show_jpeg(self, jpeg: bytes) -> None:
        """Display JPEG bytes in the preview and remember them."""
        self._last_jpeg = jpeg
        try:
            pixbuf = self._pixbuf_from_jpeg(jpeg)
        except GLib.Error as exc:
            self._set_busy(busy=False, status=f"Cannot display image: {exc}")
            return
        self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        self.rotate_left.set_sensitive(True)
        self.rotate_right.set_sensitive(True)

    def _set_busy(self, *, busy: bool, status: str) -> None:
        """Toggle the spinner and recipe controls, and set the status."""
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        enabled = self._session.is_open and not busy
        self.film_dropdown.set_sensitive(enabled)
        self.export_button.set_sensitive(enabled)
        self.status.set_label(status)

    def _on_error(self, exc: Exception) -> None:
        """Surface a camera error in a dialog and reset the busy state."""
        if isinstance(exc, ForeignRafError):
            detail = (
                "This RAF was shot by a different camera body. Fuji "
                "cameras only convert their own RAFs."
            )
        else:
            detail = str(exc)
        self._set_busy(busy=False, status="Error.")
        alert = Gtk.AlertDialog()
        alert.set_message("Camera error")
        alert.set_detail(detail)
        alert.show(self)

    def _on_close_request(self, _window: Any) -> bool:
        """Stop the worker (and close the session) before closing."""
        self._worker.stop()
        return False
