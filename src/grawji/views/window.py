"""Main application window (layout in ui/grawji.ui via Gtk.Template)."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from functools import partial
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from grawji import camera_info, raf
from grawji.capabilities import (
    capabilities_for,
    is_known_model,
    read_iopcode,
)
from grawji.core import (
    CameraSession,
    ForeignRafError,
    recipe_from_profile,
)
from grawji.preview import CameraWorker
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary, recipes_path
from grawji.settings import (
    load_settings,
    save_settings,
    settings_path,
)
from grawji.views import dialogs, imagemeta
from grawji.views.export import (
    BatchController,
    export_basename,
    initial_folder,
    write_jpeg,
)
from grawji.views.filmstrip import FilmStrip, FilmStripNav
from grawji.views.foldertree import FolderTree
from grawji.views.navigator import Navigator
from grawji.views.preferences import PreferencesDialog
from grawji.views.preview_view import PreviewView, oriented_pixbuf
from grawji.views.recipe_manager import RecipeLibraryController
from grawji.views.recipe_panel import RecipePanel

_CAMERA_POLL_SECONDS = 3
# Debounce a selection's load so fast scrubbing does not spawn a decode per
# image passed over, the decode then runs off-thread and never blocks the UI.
_LOAD_DELAY_MS = 50
# Default side-panel width, used to restore it after a collapse.
_DEFAULT_SIDEBAR_WIDTH = 240
# Pane positions at or below this count as collapsed.
_SIDEBAR_COLLAPSED_MAX = 10

# App-wide CSS: preview canvas backgrounds, thumbnails, the folder tree.
_CANVAS_CSS = """
.canvas-white, .canvas-white > viewport { background-color: #ffffff; }
.canvas-gray, .canvas-gray > viewport { background-color: #777777; }
.canvas-black, .canvas-black > viewport { background-color: #000000; }
button.thumb {
    padding: 2px 6px;
    margin-top: 16px;
    margin-bottom: 0;
    transition: margin 120ms ease;
}
button.thumb.thumb-selected,
button.thumb.thumb-marked {
    margin-top: 0;
    margin-bottom: 16px;
}
/* A selected card (batch-select mode) also gets an accent ring. */
button.thumb.thumb-marked {
    box-shadow: inset 0 0 0 2px @accent_bg_color;
}
/* Soften the folder tree: slightly dimmed text and a gentler selection. */
.folder-tree { color: alpha(currentColor, 0.85); font-weight: normal; }
.folder-tree image { opacity: 0.7; }
.folder-tree row:selected { background-color: alpha(currentColor, 0.12); }
/* Filmstrip nav buttons: round only the edge facing the window border. */
.filmstrip-nav-start { border-radius: 0 0 0 8px; }
.filmstrip-nav-end { border-radius: 0 0 8px 0; }
"""

_UI = (
    resources.files("grawji")
    .joinpath("ui", "grawji.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class MainWindow(Adw.ApplicationWindow):
    """grawji main window: browse RAFs, tune recipe, preview, export."""

    __gtype_name__ = "MainWindow"

    window_title = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
    preview_view: PreviewView = Gtk.Template.Child()
    recipe_panel: RecipePanel = Gtk.Template.Child()
    original_picture = Gtk.Template.Child()
    nav_overlay = Gtk.Template.Child()
    exif_group = Gtk.Template.Child()
    filmstrip_slot = Gtk.Template.Child()
    foldertree_slot = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    select_bar = Gtk.Template.Child()
    select_label = Gtk.Template.Child()
    select_separator = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire up the worker, the composite widgets and the controllers."""
        super().__init__(**kwargs)
        self._session = CameraSession()
        self._worker = CameraWorker(self._session, dispatch=GLib.idle_add)
        self._worker.start()
        self.connect("close-request", self._on_close_request)

        self._raf_path: Path | None = None
        self._current_folder: str | None = None
        self._notified_models: set[str] = set()
        self._exif_rows: list[Any] = []
        self._render_pending_id = 0
        self._load_pending_id = 0
        self._error_showing = False
        # Bumped on every image selection. Async open/preview callbacks carry
        # the value they were issued under and ignore themselves if a newer
        # selection has superseded them (fast filmstrip scrubbing).
        self._generation = 0

        self._settings = load_settings(settings_path())
        self._apply_color_scheme()
        if self._settings.window_width and self._settings.window_height:
            self.set_default_size(
                self._settings.window_width, self._settings.window_height
            )
        self._init_sidebar()
        self._install_css()

        self.preview_view.set_background(self._settings.canvas_background)
        self.preview_view.set_show_histogram(self._settings.show_histogram)
        self.recipe_panel.set_wb_grid_tint(self._settings.wb_grid_tint)
        self.recipe_panel.connect("changed", self._on_recipe_changed)
        self.recipe_panel.connect("apply-recipe", self._on_apply_recipe)
        self.export_button.connect("clicked", self._on_export_clicked)

        self._navigator = Navigator(
            area=self.nav_overlay,
            scroll=self.preview_view.scroll,
            picture=self.original_picture,
            get_rotation=lambda: self.preview_view.rotation,
        )

        self._init_filmstrip()

        self._foldertree = FolderTree(
            on_select=self._scan_folder,
            bookmarks=self._settings.bookmarks,
            on_bookmarks_changed=self._on_bookmarks_changed,
        )
        self._foldertree.set_vexpand(True)
        self.foldertree_slot.append(self._foldertree)

        self._recipe_library = RecipeLibrary(recipes_path())
        self._library = RecipeLibraryController(
            parent=self,
            library=self._recipe_library,
            panel=self.recipe_panel,
            on_render=self._render_if_open,
            on_status=self.preview_view.set_status,
            get_iopcode=self._read_iopcode,
            on_baseline_changed=self._on_baseline_changed,
        )
        self._batch = BatchController(
            parent=self,
            worker=self._worker,
            session=self._session,
            settings=self._settings,
            get_paths=lambda: self._filmstrip.paths,
            get_recipe=self.recipe_panel.get_recipe,
            get_current_raf=(
                lambda: str(self._raf_path) if self._raf_path else None
            ),
            set_busy=self._set_busy,
            on_status=self.preview_view.set_status,
            on_error=self._on_error,
            on_finished=self._end_select_mode,
        )

        self._install_actions()
        self._refresh_camera_status()
        GLib.timeout_add_seconds(
            _CAMERA_POLL_SECONDS, self._refresh_camera_status
        )

        last = self._settings.last_folder
        if last and Path(last).is_dir():
            self._scan_folder(last)
            self._foldertree.reveal_path(last)

    def _install_actions(self) -> None:
        """Install window actions used by the menu and keyboard shortcuts."""
        view = self.preview_view
        specs: tuple[tuple[str, Callable[[], None], tuple[str, ...]], ...] = (
            ("export", lambda: self._on_export_clicked(None), ("<Ctrl>e",)),
            ("save-recipe", self._library.save_current, ("<Ctrl>s",)),
            ("reset", self._reset_recipe, ("<Ctrl>r",)),
            ("preferences", self._on_preferences, ("<Ctrl>comma",)),
            ("batch-export", self._on_batch_export, ()),
            ("select-all", self._select_all, ("<Ctrl>a",)),
            ("cancel-selection", self._end_select_mode, ("Escape",)),
            ("manage-recipes", self._library.manage, ()),
            ("zoom-in", view.zoom_in, ("<Ctrl>plus", "<Ctrl>equal")),
            ("zoom-out", view.zoom_out, ("<Ctrl>minus",)),
            ("zoom-fit", view.zoom_fit, ("<Ctrl>0",)),
            ("cycle-background", self._cycle_background, ("b",)),
            ("toggle-peek", self._toggle_peek, ("backslash",)),
            (
                "shortcuts",
                lambda: dialogs.present_shortcuts(self),
                ("<Ctrl>question",),
            ),
            ("about", lambda: dialogs.present_about(self), ()),
        )
        app = self.get_application()
        for name, callback, accels in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", partial(self._activate, callback))
            self.add_action(action)
            if app is not None and accels:
                app.set_accels_for_action(f"win.{name}", list(accels))

        histogram = Gio.SimpleAction.new_stateful(
            "toggle-histogram",
            None,
            GLib.Variant.new_boolean(self._settings.show_histogram),
        )
        histogram.connect("change-state", self._on_toggle_histogram)
        self.add_action(histogram)
        if app is not None:
            app.set_accels_for_action("win.toggle-histogram", ["h"])

        # Enabled only once at least one image is selected in select mode.
        self._export_selection_action = Gio.SimpleAction.new(
            "export-selection", None
        )
        self._export_selection_action.connect(
            "activate", partial(self._activate, self._export_selection)
        )
        self._export_selection_action.set_enabled(False)
        self.add_action(self._export_selection_action)

        # Compare is available only once a recipe is marked as baseline.
        self._compare_action = Gio.SimpleAction.new_stateful(
            "toggle-compare", None, GLib.Variant.new_boolean(False)
        )
        self._compare_action.connect("change-state", self._on_toggle_compare)
        self._compare_action.set_enabled(
            self._recipe_library.baseline is not None
        )
        self.add_action(self._compare_action)

    @staticmethod
    def _activate(callback: Callable[[], None], *_args: object) -> None:
        """Adapt a no-argument callback to the action activate signature."""
        callback()

    def _init_sidebar(self) -> None:
        """Restore the side panel width and wire its collapse button."""
        self.main_paned.set_position(self._settings.sidebar_width)
        self._sidebar_expanded_width = (
            self._settings.sidebar_width or _DEFAULT_SIDEBAR_WIDTH
        )
        self.sidebar_button.connect("clicked", self._toggle_sidebar)

    def _toggle_sidebar(self, _button: Any) -> None:
        """Collapse the side panel, or restore it to its expanded width."""
        position = self.main_paned.get_position()
        if position > _SIDEBAR_COLLAPSED_MAX:
            self._sidebar_expanded_width = position
            self.main_paned.set_position(0)
        else:
            self.main_paned.set_position(self._sidebar_expanded_width)

    def _install_css(self) -> None:
        """Load the app's CSS for the preview canvas and thumbnails."""
        provider = Gtk.CssProvider()
        provider.load_from_string(_CANVAS_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _init_filmstrip(self) -> None:
        """Build the filmstrip flanked by previous/next navigation."""
        self._filmstrip = FilmStrip(
            on_select=self._on_raf_selected,
            on_loading=self._on_thumbs_loading,
            on_selection_changed=self._on_selection_changed,
        )
        self._filmstrip.set_glide_speed(self._settings.nav_glide_speed)
        self._filmstrip.set_hexpand(True)
        self._nav = FilmStripNav(self._filmstrip)
        self._nav.attach_keys(self)
        self.filmstrip_slot.append(self._nav.prev_button)
        self.filmstrip_slot.append(self._filmstrip)
        self.filmstrip_slot.append(self._nav.next_button)

    def _on_thumbs_loading(self, loading: bool) -> None:
        """Show the activity spinner while the filmstrip decodes thumbs."""
        self.preview_view.set_spinner(active=loading)

    def _scan_folder(self, path: str) -> None:
        """Scan a folder into the filmstrip and remember it."""
        # Already showing this folder (e.g. re-selected in the tree).
        if path == self._current_folder:
            return
        self._current_folder = path
        self._filmstrip.scan(path)
        self._nav.update()
        self._settings.last_folder = path
        self._save_settings()
        self.preview_view.set_status("Select an image from the filmstrip.")

    def _on_raf_selected(self, raf_path: str) -> None:
        """React to the click instantly; load the RAF on the next idle tick.

        The file read, JPEG decode and EXIF parse are deferred so the click
        (filmstrip highlight) paints first instead of waiting on them.
        """
        self._nav.update()
        self._generation += 1
        self._raf_path = Path(raf_path)
        self.set_title(f"grawji — {Path(raf_path).name}")
        self._set_busy(busy=True, status="Loading RAF…")
        if self._load_pending_id:
            GLib.source_remove(self._load_pending_id)
        self._load_pending_id = GLib.timeout_add(
            _LOAD_DELAY_MS, self._load_selected, self._generation, raf_path
        )

    def _load_selected(self, generation: int, raf_path: str) -> bool:
        """Read the embedded preview + EXIF and open the RAF (off the click).

        Skips itself if a newer selection has already superseded it, so fast
        scrubbing does not pile up decodes.
        """
        self._load_pending_id = 0
        if generation != self._generation:
            return GLib.SOURCE_REMOVE
        self.preview_view.reset_rotation()
        # The camera open runs on its own worker. The embedded-preview read
        # and decode run on a short-lived thread. Neither blocks the UI, so
        # the filmstrip animation stays smooth and the image appears as soon
        # as it is decoded.
        self._worker.open(
            raf_path,
            on_done=partial(self._on_opened, generation),
            on_error=self._on_error,
        )
        threading.Thread(
            target=self._decode_selection,
            args=(generation, raf_path),
            name="grawji-decode",
            daemon=True,
        ).start()
        return GLib.SOURCE_REMOVE

    def _decode_selection(self, generation: int, raf_path: str) -> None:
        """Read and decode the embedded preview off the main thread."""
        try:
            jpeg = raf.embedded_jpeg(raf_path)
            pixbuf = oriented_pixbuf(jpeg)
            rows = imagemeta.exif_rows(jpeg)
        except (ValueError, OSError, GLib.Error):
            GLib.idle_add(self._apply_selection, generation, None, None, [])
            return
        GLib.idle_add(self._apply_selection, generation, jpeg, pixbuf, rows)

    def _apply_selection(
        self,
        generation: int,
        jpeg: bytes | None,
        pixbuf: Any,
        rows: list[tuple[str, str]],
    ) -> bool:
        """Show the decoded embedded preview + EXIF (on the main thread)."""
        if generation != self._generation:
            return GLib.SOURCE_REMOVE
        self.preview_view.set_embedded_jpeg(jpeg)
        if pixbuf is not None:
            self.preview_view.show_pixbuf(pixbuf, jpeg=jpeg)
            self.original_picture.set_paintable(
                Gdk.Texture.new_for_pixbuf(pixbuf)
            )
        else:
            self.preview_view.clear_source()
        self._populate_exif_rows(rows)
        return GLib.SOURCE_REMOVE

    def _on_opened(self, generation: int, _result: object) -> None:
        """Show the first preview, optionally from the image's recipe.

        If the "load recipe from image" setting is on, the controls are
        set to the image's own in-camera recipe first; otherwise the
        current (sticky) recipe is kept and applied to the new image.
        """
        if generation != self._generation:
            return  # a newer selection has superseded this open
        profile = self._session.profile
        if profile is not None:
            # The RAF is provably from the connected body (a foreign one
            # fails with 0x2002), so its EXIF model identifies the camera.
            model = (
                imagemeta.camera_model(str(self._raf_path))
                if self._raf_path is not None
                else None
            )
            self.recipe_panel.apply_capabilities(
                capabilities_for(profile, model=model)
            )
            self._notify_unverified(model)
        render_working = True
        if profile is not None and self._settings.load_recipe_from_image:
            self.recipe_panel.set_active(
                recipe_from_profile(profile), "From image"
            )
            # The loaded recipe is the image's own, so the embedded JPEG
            # already shown is exactly what a render would produce - skip the
            # slow conversion round-trip until the user actually edits.
            if self.preview_view.has_embedded_jpeg:
                self._set_busy(busy=False, status="Ready.")
                render_working = False
        if render_working:
            self._render_preview()
        # Comparing carries across images: refresh the baseline for this one.
        if self.preview_view.comparing:
            self._render_baseline(self._generation)

    def _notify_unverified(self, model: str | None) -> None:
        """Toast once per session when the body is not in the table.

        An unknown model gets the conservative baseline, so controls the
        body may well support stay hidden. Say so instead of looking
        broken, and invite the report that gets the body added.
        """
        if model is None or is_known_model(model):
            return
        if model in self._notified_models:
            return
        self._notified_models.add(model)
        toast = Adw.Toast.new(
            f"The {model} is not in grawji's capability table yet - "
            "showing the safe baseline. Please report your body!"
        )
        toast.set_timeout(0)  # stays until dismissed, it is actionable
        toast.set_button_label("Report…")
        toast.connect("button-clicked", self._on_report_body)
        self.toast_overlay.add_toast(toast)

    def _on_report_body(self, _toast: Any) -> None:
        """Open the new-body issue template in the browser."""
        Gtk.UriLauncher.new(
            "https://github.com/p5k369/grawji/issues/new"
            "?template=new-body-report.yml"
        ).launch(self, None, None)

    def open_raf(self, path: str) -> None:
        """Open a RAF from outside (file manager or command line).

        Scans the file's folder into the filmstrip and selects it,
        driving the normal selection pipeline.
        """
        folder = str(Path(path).parent)
        self._scan_folder(folder)
        self._foldertree.reveal_path(folder)
        if not self._filmstrip.select_path(path):
            self.preview_view.set_status(
                f"{Path(path).name} is not in the filmstrip."
            )

    def _read_iopcode(self) -> int | None:
        """The open profile's IOPCode (for FP export), or None."""
        profile = self._session.profile
        return read_iopcode(profile) if profile is not None else None

    def _on_baseline_changed(self) -> None:
        """React to the marked baseline changing in the recipe manager."""
        has_baseline = self._recipe_library.baseline is not None
        self._compare_action.set_enabled(has_baseline)
        if not has_baseline:
            self._compare_action.set_state(GLib.Variant.new_boolean(False))
            self.preview_view.set_compare(on=False)
        elif self.preview_view.comparing:
            self._render_baseline(self._generation)  # baseline recipe changed

    def _on_toggle_compare(self, action: Any, value: Any) -> None:
        """Start or stop the baseline split-compare view."""
        if value.get_boolean():
            if (
                self._recipe_library.baseline_recipe() is None
                or not self._session.is_open
            ):
                return  # nothing to compare, leave the toggle off
            action.set_state(value)
            self._render_baseline(self._generation)
        else:
            action.set_state(value)
            self.preview_view.set_compare(on=False)

    def _render_baseline(self, generation: int) -> None:
        """Render the marked baseline recipe for the current image."""
        baseline = self._recipe_library.baseline_recipe()
        if baseline is None:
            return
        # Rendering the baseline is a camera round-trip; show progress so
        # the wait before the split appears is not a dead moment.
        self._set_busy(busy=True, status="Preparing comparison…")
        self._worker.submit(
            partial(self._session.render, baseline, full_resolution=False),
            on_done=partial(self._on_baseline_rendered, generation),
            on_error=self._on_error,
        )

    def _on_baseline_rendered(self, generation: int, jpeg: bytes) -> None:
        """Feed the baseline render into the split view."""
        if generation != self._generation:
            return  # a newer selection has superseded this baseline
        if not self._compare_action.get_state().get_boolean():
            # Compare was toggled off while this baseline was rendering,
            # dropping it keeps the split and the button state in sync.
            self._set_busy(busy=False, status="Ready.")
            return
        self.preview_view.set_compare_baseline(jpeg)
        self.preview_view.set_compare(on=True)
        self._set_busy(busy=False, status="Comparing with baseline.")

    def _on_recipe_changed(self, _panel: Any) -> None:
        """Re-render (debounced) after a recipe edit."""
        if self._session.is_open:
            self._schedule_render()

    def _on_apply_recipe(self, _panel: Any, name: str) -> None:
        """Apply the recipe chosen in the panel's apply-combo."""
        self._library.apply(name)

    def _reset_recipe(self) -> None:
        """Reset all controls to the default recipe."""
        self.recipe_panel.set_active(Recipe(), "Default")
        self._render_if_open()

    def _render_if_open(self) -> None:
        """Re-render the preview if an image is open (else do nothing)."""
        if self._session.is_open:
            self._render_preview()

    def _schedule_render(self) -> None:
        """Debounce preview renders so a slider drag fires only one.

        Without this, every intermediate slider value queues a camera
        render; the preview then lags behind the control and shows a
        stale result. Coalesce to a single render once edits settle.
        """
        if self._render_pending_id:
            GLib.source_remove(self._render_pending_id)
        self._render_pending_id = GLib.timeout_add(150, self._render_now)

    def _render_now(self) -> bool:
        """Fire the debounced preview render."""
        self._render_pending_id = 0
        self._render_if_open()
        return GLib.SOURCE_REMOVE

    def _render_preview(self) -> None:
        """Queue a fast (non-full-resolution) preview render."""
        self._set_busy(busy=True, status="Rendering preview…")
        self._worker.render(
            self.recipe_panel.get_recipe(),
            full_resolution=False,
            on_done=partial(self._on_preview, self._generation),
            on_error=self._on_error,
        )

    def _on_preview(self, generation: int, jpeg: bytes) -> None:
        """Display a finished preview render."""
        if generation != self._generation:
            return  # a newer selection has superseded this render
        self.preview_view.show_jpeg(jpeg)
        self._set_busy(busy=False, status="Ready.")

    def _on_export_clicked(self, _button: Any) -> None:
        """Show a save dialog for a full-resolution export."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export JPEG")
        dialog.set_initial_name(
            export_basename(self._raf_path or "grawji-export")
        )
        start = initial_folder(self._settings.last_export_dir)
        if start is not None:
            dialog.set_initial_folder(start)
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
        self._settings.last_export_dir = str(Path(path).parent)
        self._save_settings()
        self._set_busy(busy=True, status="Rendering full-resolution export…")
        self._worker.render(
            self.recipe_panel.get_recipe(),
            full_resolution=True,
            on_done=partial(self._on_exported, path),
            on_error=self._on_error,
        )

    def _on_exported(self, path: str, jpeg: bytes) -> None:
        """Save the exported JPEG with orientation and rotation baked in."""
        try:
            write_jpeg(
                jpeg,
                path,
                quality=self._settings.jpeg_quality,
                decode=self.preview_view.pixbuf_from_jpeg,
            )
        except (GLib.Error, OSError) as exc:
            self._set_busy(busy=False, status=f"Export failed: {exc}")
            return
        self._set_busy(busy=False, status=f"Exported to {path}")

    def _on_batch_export(self) -> None:
        """Enter batch-select mode: pick images, then export them."""
        if not self._filmstrip.paths:
            self.preview_view.set_status("No images to export.")
            return
        self._filmstrip.enter_select_mode()
        self.select_bar.set_reveal_child(True)
        self.select_separator.set_visible(True)
        self._update_export_button()

    def _on_selection_changed(self, count: int) -> None:
        """Reflect the batch selection in the bar label and export action."""
        self.select_label.set_label(
            "Select images to export" if count == 0 else f"{count} selected"
        )
        self._export_selection_action.set_enabled(count > 0)

    def _select_all(self) -> None:
        """Select every image (batch-select mode only)."""
        self._filmstrip.select_all()

    def _end_select_mode(self) -> None:
        """Leave batch-select mode and hide the selection bar."""
        self._filmstrip.exit_select_mode()
        self.select_bar.set_reveal_child(False)
        self.select_separator.set_visible(False)
        self._update_export_button()

    def _export_selection(self) -> None:
        """Export the selected images; the bar stays until the run ends."""
        complaint = self._batch.begin(self._filmstrip.selected_paths)
        if complaint is not None:
            self.preview_view.set_status(complaint)

    def _cycle_background(self) -> None:
        """Cycle the preview background and remember the choice."""
        self._settings.canvas_background = self.preview_view.cycle_background()
        self._save_settings()

    def _toggle_peek(self) -> None:
        """Toggle showing the in-camera original."""
        self.preview_view.set_peek(peeking=not self.preview_view.peeking)

    def _populate_exif_rows(self, rows: list[tuple[str, str]]) -> None:
        """Show already-parsed EXIF (label, value) pairs in the Image group."""
        for row in self._exif_rows:
            self.exif_group.remove(row)
        self._exif_rows = []
        for label, value in rows:
            row = Adw.ActionRow(title=label, subtitle=value)
            self.exif_group.add(row)
            self._exif_rows.append(row)

    def _set_busy(self, *, busy: bool, status: str) -> None:
        """Toggle the spinner and recipe controls, and set the status."""
        self.preview_view.set_spinner(active=busy)
        # Controls stay live while the camera works - the worker coalesces
        # rapid changes - so the UI never locks up mid-render.
        enabled = self._session.is_open
        self.recipe_panel.set_controls_sensitive(enabled)
        self._update_export_button()
        self.preview_view.set_status(status)

    def _update_export_button(self) -> None:
        """Enable the header Export only when it applies."""
        self.export_button.set_sensitive(
            self._session.is_open and not self._filmstrip.in_select_mode
        )

    def _on_error(self, exc: Exception) -> None:
        """Surface a camera error in a dialog and reset the busy state."""
        logging.getLogger("grawji").warning("camera operation failed: %s", exc)
        self._set_busy(busy=False, status="Error.")
        # A camera failure aborts any batch mid-flight, unstick its dialog.
        self._batch.abort()
        # A wedged camera fails every queued render in turn; show one dialog.
        if self._error_showing:
            return
        if isinstance(exc, ForeignRafError):
            message = "Camera error"
            detail = (
                "This RAF was shot by a different camera body. Fuji "
                "cameras only convert their own RAFs."
            )
        elif camera_info.is_camera_stuck(exc):
            message = "Camera not responding"
            detail = (
                "The camera stopped responding during conversion and "
                "appears to be stuck. Turn it off and on again - if it "
                "will not turn off, briefly remove the battery - then "
                "reconnect and try the image again."
            )
        elif camera_info.is_camera_disconnected(exc):
            message = "Camera disconnected"
            detail = (
                "grawji must be restarted to use a camera that was "
                "unplugged and reconnected: the Flatpak sandbox does not "
                "pick up the reconnected camera on its own."
                if camera_info.IN_FLATPAK
                else "The camera was disconnected. Reconnect it, then "
                "select an image again to continue."
            )
        else:
            message = "Camera error"
            detail = str(exc)
        self._error_showing = True
        alert = Gtk.AlertDialog()
        alert.set_message(message)
        alert.set_detail(detail)
        alert.set_buttons(["Close"])
        alert.choose(self, None, self._on_error_dismissed)

    def _on_error_dismissed(self, dialog: Any, result: Any) -> None:
        """Clear the error-showing guard once the dialog is closed."""
        with contextlib.suppress(GLib.Error):
            dialog.choose_finish(result)
        self._error_showing = False

    def _refresh_camera_status(self) -> bool:
        """Update the header subtitle with the connected camera, if any."""
        model = camera_info.detect_camera()
        self.window_title.set_subtitle(
            f"{model} connected" if model else "No camera"
        )
        return GLib.SOURCE_CONTINUE

    def _on_preferences(self) -> None:
        """Open the preferences dialog."""
        dialog = PreferencesDialog(
            settings=self._settings, on_change=self._on_settings_changed
        )
        dialog.present(self)

    def _on_bookmarks_changed(self, bookmarks: list[str]) -> None:
        """Persist the folder-tree bookmarks."""
        self._settings.bookmarks = bookmarks
        self._save_settings()

    def _on_settings_changed(self) -> None:
        """Persist settings and apply any that affect the live UI."""
        self.recipe_panel.set_wb_grid_tint(self._settings.wb_grid_tint)
        self._filmstrip.set_glide_speed(self._settings.nav_glide_speed)
        self._apply_color_scheme()
        self._save_settings()

    def _apply_color_scheme(self) -> None:
        """Apply the chosen theme: follow the desktop, or force light/dark."""
        scheme = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(self._settings.color_scheme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def _on_toggle_histogram(self, action: Any, value: Any) -> None:
        """Show or hide the histogram overlay and remember the choice."""
        action.set_state(value)
        show = value.get_boolean()
        self._settings.show_histogram = show
        self.preview_view.set_show_histogram(show)
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist the current settings to disk."""
        save_settings(self._settings, settings_path())

    def _on_close_request(self, _window: Any) -> bool:
        """Persist window size, stop the worker, then allow closing."""
        self._settings.window_width = self.get_width()
        self._settings.window_height = self.get_height()
        self._settings.sidebar_width = self.main_paned.get_position()
        self._save_settings()
        self._worker.stop()
        return False
