"""Main application window (layout in ui/grawji.ui via Gtk.Template)."""

from __future__ import annotations

import tempfile
import threading
from functools import partial
from importlib import metadata, resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GExiv2", "0.10")

import rawji
from gi.repository import (
    Adw,
    Gdk,
    GdkPixbuf,
    GExiv2,
    Gio,
    GLib,
    Gtk,
)
from rawji.fuji_enums import (
    FP_TONE_MAX,
    FP_TONE_MIN,
    FUJIFILM_USB_VENDOR_ID,
    WB_KELVIN_PRESETS,
    ColorSpace,
    GrainEffect,
)

from grawji import exif, raf
from grawji.capabilities import (
    Capabilities,
    capabilities_for,
    read_iopcode,
)
from grawji.core import (
    CameraSession,
    ForeignRafError,
    recipe_from_profile,
)
from grawji.filmstrip import FilmStrip
from grawji.foldertree import FolderTree
from grawji.fp_xml import parse_fp, serialize_fp
from grawji.preferences import PreferencesDialog
from grawji.preview import CameraWorker
from grawji.recipe import Recipe
from grawji.recipes import (
    load_recipes,
    recipes_path,
    save_recipes,
)
from grawji.settings import (
    load_settings,
    save_settings,
    settings_path,
)
from grawji.widgets import SliderRow, WBShiftGrid

_FILM_SIMULATIONS = [e.name for e in rawji.FilmSimulation]
_WHITE_BALANCES = [e.name for e in rawji.WhiteBalance]
_DYNAMIC_RANGES = [e.name for e in rawji.DynamicRange]
_GRAINS = [e.name for e in GrainEffect]
_COLOR_SPACES = [member.name for member in ColorSpace]
_WB_KELVIN_PRESETS = sorted(WB_KELVIN_PRESETS)


def _nearest_kelvin_index(kelvin: int) -> int:
    """Return the Kelvin preset index closest to the given value."""
    return min(
        range(len(_WB_KELVIN_PRESETS)),
        key=lambda i: abs(_WB_KELVIN_PRESETS[i] - kelvin),
    )


# Manual rotation (degrees clockwise) -> GdkPixbuf rotation.
_ROTATIONS = {
    90: GdkPixbuf.PixbufRotation.CLOCKWISE,
    180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
    270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
}

# Friendly names for known Fuji product ids. Detection accepts any device on
# the Fuji vendor id. This map only supplies a nice label.
# todo: This map is cosmetic only, could be very well integrated in rawji.
_PID_NAMES = {
    0x02D1: "X100F",
    0x02DD: "X-T3",
    0x02E3: "X-T30",
    0x02E5: "X-T3",
    0x02E7: "X-T4",
}
_CAMERA_POLL_SECONDS = 3
# Debounce a selection's load so fast scrubbing does not spawn a decode per
# image passed over, the decode then runs off-thread and never blocks the UI.
_LOAD_DELAY_MS = 50
# Default side-panel width, used to restore it after a collapse.
_DEFAULT_SIDEBAR_WIDTH = 240
# Pane positions at or below this count as collapsed.
_SIDEBAR_COLLAPSED_MAX = 10
# Kernel USB device tree; read directly so plug/unplug is seen immediately.
_USB_SYSFS = Path("/sys/bus/usb/devices")
# Below this, an exposure value counts as zero EV (avoids "-0.0 EV").
_EV_EPSILON = 1e-9

# Preview canvas backgrounds, cycled by the toolbar button (darktable-style).
_CANVAS_CSS = """
.canvas-white, .canvas-white > viewport { background-color: #ffffff; }
.canvas-gray, .canvas-gray > viewport { background-color: #777777; }
.canvas-black, .canvas-black > viewport { background-color: #000000; }
button.thumb {
    padding: 0;
    margin-top: 16px;
    margin-bottom: 0;
    transition: margin 120ms ease;
}
button.thumb.thumb-selected {
    margin-top: 0;
    margin-bottom: 16px;
}
/* Soften the folder tree: dimmer text and a gentler selection. */
.folder-tree { color: alpha(currentColor, 0.7); }
.folder-tree image { opacity: 0.7; }
.folder-tree row:selected { background-color: alpha(currentColor, 0.12); }
"""
_BACKGROUNDS = ["", "canvas-white", "canvas-gray", "canvas-black"]

_UI = (
    resources.files("grawji")
    .joinpath("ui", "grawji.ui")
    .read_text(encoding="utf-8")
)


def _export_basename(raf_path: Path | str) -> str:
    """Build an export filename from the RAF stem."""
    return f"{Path(raf_path).stem}.jpg"


def _app_version() -> str:
    """Return grawji's installed version, or a fallback if not packaged."""
    try:
        return metadata.version("grawji")
    except metadata.PackageNotFoundError:
        return "0.0.1"


@Gtk.Template(string=_UI)
class MainWindow(Adw.ApplicationWindow):
    """grawji main window: browse RAFs, tune recipe, preview, export."""

    __gtype_name__ = "MainWindow"

    window_title = Gtk.Template.Child()
    rotate_left = Gtk.Template.Child()
    rotate_right = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    preview_scroll = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    original_picture = Gtk.Template.Child()
    status = Gtk.Template.Child()
    recipe_row = Gtk.Template.Child()
    recipe_group = Gtk.Template.Child()
    exif_group = Gtk.Template.Child()
    filmstrip_slot = Gtk.Template.Child()
    foldertree_slot = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire up the worker, recipe controls, filmstrip and signals."""
        super().__init__(**kwargs)
        self._session = CameraSession()
        self._worker = CameraWorker(self._session, dispatch=GLib.idle_add)
        self._worker.start()
        self.connect("close-request", self._on_close_request)

        self._rotation = 0
        self._zoom = 1.0
        self._pixbuf: Any | None = None
        self._last_jpeg: bytes | None = None
        self._embedded_jpeg: bytes | None = None
        self._raf_path: Path | None = None
        self._current_folder: str | None = None
        self._exif_rows: list[Any] = []
        self._suppress_recipe_signals = False
        self._suppress_combo_signal = False
        self._recipe_names: list[str] = []
        self._applied_recipe = Recipe()
        self._active_label = "Default"
        self._pan_h = 0.0
        self._pan_v = 0.0
        self._render_pending_id = 0
        self._load_pending_id = 0
        self._reload_pending_id = 0
        self._file_monitor: Any = None
        # Bumped on every image selection. Async open/preview callbacks carry
        # the value they were issued under and ignore themselves if a newer
        # selection has superseded them (fast filmstrip scrubbing).
        self._generation = 0

        self._settings = load_settings(settings_path())
        self._recipes = load_recipes(recipes_path())
        if self._settings.window_width and self._settings.window_height:
            self.set_default_size(
                self._settings.window_width, self._settings.window_height
            )
        self._init_sidebar()

        self._install_css()
        self._apply_background(self._settings.canvas_background)

        self._build_recipe_controls()
        self._connect_signals()

        self._filmstrip = FilmStrip(
            on_select=self._on_raf_selected,
            on_loading=self._on_thumbs_loading,
        )
        self.filmstrip_slot.append(self._filmstrip)

        self._foldertree = FolderTree(on_select=self._scan_folder)
        self._foldertree.set_vexpand(True)
        self.foldertree_slot.append(self._foldertree)

        self._install_actions()
        self._rebuild_menu()
        self._rebuild_recipes()
        self._update_recipe_status()
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
        specs = (
            (
                "export",
                None,
                lambda *_a: self._on_export_clicked(None),
                ["<Ctrl>e"],
            ),
            (
                "save-recipe",
                None,
                lambda *_a: self._on_save_recipe(),
                ["<Ctrl>s"],
            ),
            ("reset", None, lambda *_a: self._reset_recipe(), ["<Ctrl>r"]),
            (
                "preferences",
                None,
                lambda *_a: self._on_preferences(),
                ["<Ctrl>comma"],
            ),
            ("batch-export", None, lambda *_a: self._on_batch_export(), ()),
            (
                "import-recipe",
                None,
                lambda *_a: self._on_import_fp(),
                (),
            ),
            (
                "export-recipe",
                None,
                lambda *_a: self._on_export_fp(),
                (),
            ),
            (
                "prev-image",
                None,
                lambda *_a: self._filmstrip.select_relative(-1),
                ["Left"],
            ),
            (
                "next-image",
                None,
                lambda *_a: self._filmstrip.select_relative(1),
                ["Right"],
            ),
            (
                "zoom-in",
                None,
                lambda *_a: self._set_zoom(self._zoom * 1.25),
                ["<Ctrl>plus", "<Ctrl>equal"],
            ),
            (
                "zoom-out",
                None,
                lambda *_a: self._set_zoom(self._zoom / 1.25),
                ["<Ctrl>minus"],
            ),
            ("zoom-fit", None, lambda *_a: self._set_zoom(1.0), ["<Ctrl>0"]),
            (
                "cycle-background",
                None,
                lambda *_a: self._cycle_background(),
                ["b"],
            ),
            (
                "open-folder",
                None,
                lambda *_a: self._on_open_folder_clicked(None),
                ["<Ctrl>o"],
            ),
            (
                "shortcuts",
                None,
                lambda *_a: self._on_shortcuts(),
                ["<Ctrl>question"],
            ),
            ("about", None, lambda *_a: self._on_about(), ()),
            (
                "delete-recipe",
                "s",
                lambda _a, p: self._delete_recipe(p.get_string()),
                (),
            ),
        )
        app = self.get_application()
        for name, ptype, callback, accels in specs:
            vtype = GLib.VariantType.new(ptype) if ptype else None
            action = Gio.SimpleAction.new(name, vtype)
            action.connect("activate", callback)
            self.add_action(action)
            if app is not None and accels:
                app.set_accels_for_action(f"win.{name}", list(accels))

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

    def _on_thumbs_loading(self, loading: bool) -> None:
        """Show the activity spinner while the filmstrip decodes thumbnails."""
        self._set_spinner(active=loading)

    def _set_spinner(self, *, active: bool) -> None:
        """Show and run the status-line spinner, or hide and stop it."""
        self.spinner.set_visible(active)
        if active:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _install_css(self) -> None:
        """Load the app's CSS for the preview canvas and thumbnails."""
        provider = Gtk.CssProvider()
        provider.load_from_string(_CANVAS_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_recipe_controls(self) -> None:
        """Build every recipe control in the Fuji IQ-menu order."""

        def ifmt(value: float) -> str:
            n = round(value)
            return f"{n:+d}" if n else "0"

        def evfmt(value: float) -> str:
            return f"{value:+.1f} EV" if abs(value) > _EV_EPSILON else "0 EV"

        def combo(title: str, names: list[str]) -> Adw.ComboRow:
            row = Adw.ComboRow(title=title)
            row.set_model(Gtk.StringList.new(names))
            return row

        self.film_row = combo("Film simulation", _FILM_SIMULATIONS)
        self.grain_row = combo("Grain", _GRAINS)
        self.wb_row = combo("White balance", _WHITE_BALANCES)
        self.dr_row = combo("Dynamic range", _DYNAMIC_RANGES)
        self.color_space_row = combo("Color space", _COLOR_SPACES)
        self._combo_rows = (
            self.film_row,
            self.grain_row,
            self.wb_row,
            self.dr_row,
            self.color_space_row,
        )

        self._temp_row = SliderRow(
            "Color temp",
            lower=0,
            upper=len(_WB_KELVIN_PRESETS) - 1,
            step=1,
            fmt=lambda i: f"{_WB_KELVIN_PRESETS[round(i)]}K",
        )
        self._wb_grid = WBShiftGrid()
        self._wb_grid.set_colored(self._settings.wb_grid_tint)
        self._wb_shift_label = Gtk.Label(halign=Gtk.Align.CENTER)
        self._wb_shift_label.add_css_class("dim-label")
        wb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wb_box.set_valign(Gtk.Align.CENTER)
        wb_box.set_margin_top(8)
        wb_box.set_margin_bottom(8)
        wb_box.append(self._wb_grid)
        wb_box.append(self._wb_shift_label)
        grid_row = Adw.ActionRow(title="WB shift")
        grid_row.add_suffix(wb_box)
        self._update_wb_shift_label()

        self._exposure_row = SliderRow(
            "Exposure", lower=-2.0, upper=3.0, step=1 / 3, fmt=evfmt
        )
        self._highlights_row = SliderRow(
            "Highlights", lower=-2, upper=4, fmt=ifmt
        )
        self._shadows_row = SliderRow("Shadows", lower=-2, upper=4, fmt=ifmt)
        self._color_row = SliderRow(
            "Color", lower=FP_TONE_MIN, upper=FP_TONE_MAX, fmt=ifmt
        )
        self._sharpness_row = SliderRow(
            "Sharpness", lower=FP_TONE_MIN, upper=FP_TONE_MAX, fmt=ifmt
        )
        self._nr_row = SliderRow(
            "Noise reduction", lower=-4, upper=4, fmt=ifmt
        )
        self._slider_rows = (
            self._exposure_row,
            self._highlights_row,
            self._shadows_row,
            self._color_row,
            self._sharpness_row,
            self._nr_row,
            self._temp_row,
        )
        value_chars = max(row.value_chars for row in self._slider_rows)
        for row in self._slider_rows:
            row.set_value_chars(value_chars)

        # Fuji IQ-menu order: film, grain, WB (+ temp + shift), dynamic
        # range, then exposure leading the tonal block.
        for row in (
            self.film_row,
            self.grain_row,
            self.wb_row,
            self._temp_row,
            grid_row,
            self.dr_row,
            self._exposure_row,
            self._highlights_row,
            self._shadows_row,
            self._color_row,
            self._sharpness_row,
            self._nr_row,
            self.color_space_row,
        ):
            self.recipe_group.add(row)
        self._update_temp_visibility()

    def _connect_signals(self) -> None:
        """Connect widget signals to handlers (done in code, not the .ui)."""
        self.rotate_left.connect("clicked", self._on_rotate_left)
        self.rotate_right.connect("clicked", self._on_rotate_right)
        self.export_button.connect("clicked", self._on_export_clicked)
        export_menu = Gio.Menu()
        export_menu.append("Batch Export…", "win.batch-export")
        self.export_button.set_menu_model(export_menu)
        self.recipe_row.connect("notify::selected", self._on_recipe_selected)
        for row in self._combo_rows:
            row.connect("notify::selected", self._on_recipe_changed)
        for slider in self._slider_rows:
            slider.connect_changed(self._on_recipe_changed)
        self._wb_grid.connect_changed(self._on_wb_shift_changed)
        self.wb_row.connect("notify::selected", self._on_wb_mode_changed)
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_scroll_zoom)
        self.preview_scroll.add_controller(scroll)
        pan = Gtk.GestureDrag()
        pan.connect("drag-begin", self._on_pan_begin)
        pan.connect("drag-update", self._on_pan_update)
        self.preview_scroll.add_controller(pan)

    def _current_recipe(self) -> Recipe:
        """Read the current selector values into a Recipe."""
        red, blue = self._wb_grid.get_values()
        return Recipe(
            film_simulation=_FILM_SIMULATIONS[self.film_row.get_selected()],
            white_balance=_WHITE_BALANCES[self.wb_row.get_selected()],
            dynamic_range=_DYNAMIC_RANGES[self.dr_row.get_selected()],
            grain=_GRAINS[self.grain_row.get_selected()],
            exposure=self._exposure_row.get_value(),
            highlights=int(self._highlights_row.get_value()),
            shadows=int(self._shadows_row.get_value()),
            color=int(self._color_row.get_value()),
            sharpness=int(self._sharpness_row.get_value()),
            noise_reduction=int(self._nr_row.get_value()),
            wb_shift_r=red,
            wb_shift_b=blue,
            color_temp=_WB_KELVIN_PRESETS[int(self._temp_row.get_value())],
            color_space=_COLOR_SPACES[self.color_space_row.get_selected()],
        )

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
            self._scan_folder(path)

    def _scan_folder(self, path: str) -> None:
        """Scan a folder into the filmstrip, remember it, and watch it."""
        # Already showing this folder (e.g. re-selected in the tree).
        if path == self._current_folder:
            return
        self._current_folder = path
        self._filmstrip.scan(path)
        self._settings.last_folder = path
        save_settings(self._settings, settings_path())
        self.status.set_label("Select an image from the filmstrip.")
        self._watch_folder(path)

    def _watch_folder(self, path: str) -> None:
        """Re-scan the filmstrip automatically when the folder changes."""
        if self._file_monitor is not None:
            self._file_monitor.cancel()
            self._file_monitor = None
        try:
            monitor = Gio.File.new_for_path(path).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error:
            return
        monitor.connect("changed", self._on_folder_changed)
        self._file_monitor = monitor

    def _on_folder_changed(self, *_args: Any) -> None:
        """Debounce a re-scan after the folder's contents settle."""
        if self._reload_pending_id:
            GLib.source_remove(self._reload_pending_id)
        self._reload_pending_id = GLib.timeout_add(500, self._reload_now)

    def _reload_now(self) -> bool:
        """Re-scan the current folder (picks up added/removed files)."""
        self._reload_pending_id = 0
        if self._current_folder is not None:
            self._filmstrip.scan(self._current_folder)
        return GLib.SOURCE_REMOVE

    def _on_raf_selected(self, raf_path: str) -> None:
        """React to the click instantly; load the RAF on the next idle tick.

        The file read, JPEG decode and EXIF parse are deferred so the click
        (filmstrip highlight) paints first instead of waiting on them.
        """
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
        self._rotation = 0
        # The camera open runs on its own worker; the embedded-preview read
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
            pixbuf = self._oriented_pixbuf(jpeg)
            exif = self._read_exif(jpeg)
        except (ValueError, OSError, GLib.Error):
            GLib.idle_add(self._apply_selection, generation, None, None, [])
            return
        GLib.idle_add(self._apply_selection, generation, jpeg, pixbuf, exif)

    def _apply_selection(
        self,
        generation: int,
        jpeg: bytes | None,
        pixbuf: Any,
        exif: list[tuple[str, str]],
    ) -> bool:
        """Show the decoded embedded preview + EXIF (on the main thread)."""
        if generation != self._generation:
            return GLib.SOURCE_REMOVE
        self._embedded_jpeg = jpeg
        if pixbuf is not None:
            self._last_jpeg = jpeg
            self._pixbuf = pixbuf
            self._apply_zoom()
            self.rotate_left.set_sensitive(True)
            self.rotate_right.set_sensitive(True)
            self.original_picture.set_paintable(
                Gdk.Texture.new_for_pixbuf(pixbuf)
            )
        else:
            self._last_jpeg = None
        self._populate_exif_rows(exif)
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
            self._apply_capabilities(capabilities_for(profile))
        if profile is not None and self._settings.load_recipe_from_image:
            self._set_active_recipe(recipe_from_profile(profile), "From image")
            # The loaded recipe is the image's own, so the embedded JPEG
            # already shown is exactly what a render would produce - skip the
            # slow conversion round-trip until the user actually edits.
            if self._embedded_jpeg is not None:
                self._set_busy(busy=False, status="Ready.")
                return
        self._render_preview()

    def _apply_capabilities(self, caps: Capabilities) -> None:
        """Adjust controls to what the connected body's processor supports."""
        self._highlights_row.set_range(caps.tone_min, caps.tone_max)
        self._shadows_row.set_range(caps.tone_min, caps.tone_max)

    def _load_recipe(self, recipe: Recipe) -> None:
        """Set the selectors from a recipe without triggering renders."""
        self._suppress_recipe_signals = True
        try:
            self.film_row.set_selected(
                _FILM_SIMULATIONS.index(recipe.film_simulation)
            )
            self.wb_row.set_selected(
                _WHITE_BALANCES.index(recipe.white_balance)
            )
            self.dr_row.set_selected(
                _DYNAMIC_RANGES.index(recipe.dynamic_range)
            )
            self.grain_row.set_selected(_GRAINS.index(recipe.grain))
            self._exposure_row.set_value(recipe.exposure)
            self._highlights_row.set_value(recipe.highlights)
            self._shadows_row.set_value(recipe.shadows)
            self._color_row.set_value(recipe.color)
            self._sharpness_row.set_value(recipe.sharpness)
            self._nr_row.set_value(recipe.noise_reduction)
            self._temp_row.set_value(_nearest_kelvin_index(recipe.color_temp))
            self._wb_grid.set_values(recipe.wb_shift_r, recipe.wb_shift_b)
            self._update_wb_shift_label()
            self.color_space_row.set_selected(
                _COLOR_SPACES.index(recipe.color_space)
            )
        finally:
            self._suppress_recipe_signals = False
        self._update_temp_visibility()

    def _set_active_recipe(self, recipe: Recipe, label: str) -> None:
        """Load a recipe and mark it active (for the recipe indicator)."""
        self._applied_recipe = recipe
        self._active_label = label
        self._load_recipe(recipe)
        self._update_recipe_status()
        self._sync_recipe_combo(label)

    def _rebuild_recipes(self) -> None:
        """Refresh the recipe apply-combo from the saved recipes."""
        self._recipe_names = sorted(self._recipes)
        self._suppress_combo_signal = True
        try:
            self.recipe_row.set_model(
                Gtk.StringList.new(["—", *self._recipe_names])
            )
            self.recipe_row.set_selected(0)
        finally:
            self._suppress_combo_signal = False

    def _sync_recipe_combo(self, label: str) -> None:
        """Point the apply-combo at label (or "—" if not a recipe)."""
        index = (
            self._recipe_names.index(label) + 1
            if label in self._recipe_names
            else 0
        )
        self._suppress_combo_signal = True
        try:
            self.recipe_row.set_selected(index)
        finally:
            self._suppress_combo_signal = False

    def _on_recipe_selected(self, *_args: object) -> None:
        """Apply the recipe chosen in the apply-combo."""
        if self._suppress_combo_signal:
            return
        index = self.recipe_row.get_selected()
        if index > 0:
            self._apply_recipe(self._recipe_names[index - 1])

    def _on_pan_begin(self, _gesture: Any, _x: float, _y: float) -> None:
        """Remember the scroll position at the start of a pan drag."""
        self._pan_h = self.preview_scroll.get_hadjustment().get_value()
        self._pan_v = self.preview_scroll.get_vadjustment().get_value()

    def _on_pan_update(self, _gesture: Any, dx: float, dy: float) -> None:
        """Pan the zoomed preview by dragging."""
        self.preview_scroll.get_hadjustment().set_value(self._pan_h - dx)
        self.preview_scroll.get_vadjustment().set_value(self._pan_v - dy)

    def _update_recipe_status(self) -> None:
        """Show the active recipe/source and whether it has been modified."""
        if self._current_recipe() == self._applied_recipe:
            self.recipe_group.set_description(self._active_label)
        else:
            self.recipe_group.set_description(
                f"{self._active_label} (modified)"
            )

    def _reset_recipe(self) -> None:
        """Reset all controls to the default recipe."""
        self._set_active_recipe(Recipe(), "Default")
        if self._session.is_open:
            self._render_preview()

    def _on_wb_shift_changed(self, _red: int, _blue: int) -> None:
        """Handle a white-balance shift grid edit."""
        self._update_wb_shift_label()
        self._on_recipe_changed()

    def _update_wb_shift_label(self) -> None:
        """Show the grid marker's red/blue position next to the grid."""
        red, blue = self._wb_grid.get_values()
        self._wb_shift_label.set_text(f"R {red:+d}  B {blue:+d}")

    def _on_wb_mode_changed(self, *_args: object) -> None:
        """Enable the colour-temp slider only in Temperature mode."""
        self._update_temp_visibility()

    def _update_temp_visibility(self) -> None:
        """Show the colour-temp slider only when WB is Temperature."""
        wb = _WHITE_BALANCES[self.wb_row.get_selected()]
        self._temp_row.set_visible(wb == "Temperature")

    def _on_recipe_changed(self, *_args: object) -> None:
        """Re-render and update the recipe indicator on a change."""
        if self._suppress_recipe_signals:
            return
        self._update_recipe_status()
        # A manual edit no longer matches a saved recipe.
        self._sync_recipe_combo("")
        if self._session.is_open:
            self._schedule_render()

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
        if self._session.is_open:
            self._render_preview()
        return GLib.SOURCE_REMOVE

    def _render_preview(self) -> None:
        """Queue a fast (non-full-resolution) preview render."""
        self._set_busy(busy=True, status="Rendering preview…")
        self._worker.render(
            self._current_recipe(),
            full_resolution=False,
            on_done=partial(self._on_preview, self._generation),
            on_error=self._on_error,
        )

    def _on_preview(self, generation: int, jpeg: bytes) -> None:
        """Display a finished preview render."""
        if generation != self._generation:
            return  # a newer selection has superseded this render
        self._show_jpeg(jpeg)
        self._set_busy(busy=False, status="Ready.")

    def _on_rotate_left(self, _button: Any) -> None:
        """Rotate the displayed image 90 degrees counter-clockwise."""
        self._rotate(-90)

    def _on_rotate_right(self, _button: Any) -> None:
        """Rotate the displayed image 90 degrees clockwise."""
        self._rotate(90)

    def _rotate(self, degrees: int) -> None:
        """Update the rotation and redisplay the current image."""
        self._rotation = (self._rotation + degrees) % 360
        if self._last_jpeg is not None:
            self._show_jpeg(self._last_jpeg)

    def _show_original(self, jpeg: bytes) -> None:
        """Show the in-camera original (EXIF-oriented) in the left panel."""
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(jpeg)
            loader.close()
            pixbuf = loader.get_pixbuf().apply_embedded_orientation()
        except GLib.Error:
            return
        self.original_picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))

    def _on_export_clicked(self, _button: Any) -> None:
        """Show a save dialog for a full-resolution export."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export JPEG")
        dialog.set_initial_name(self._export_filename())
        dialog.save(self, None, self._on_export_response)

    def _export_filename(self) -> str:
        """Build an export name from the current RAF stem."""
        return _export_basename(self._raf_path or "grawji-export")

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
        # Encode and transplant EXIF on a temp file first, then write the
        # finished bytes to the chosen path in one go. The chosen path may be
        # an XDG document-portal proxy (Flatpak), which exiv2 cannot rewrite
        # in place. Doing so leaves a 0-byte file.
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            pixbuf = self._pixbuf_from_jpeg(jpeg)
            quality = str(self._settings.jpeg_quality)
            pixbuf.savev(tmp_path, "jpeg", ["quality"], [quality])
            # GdkPixbuf re-encoding drops all metadata, so copy the camera's
            # EXIF back on (orientation is now baked into the pixels).
            self._copy_exif(jpeg, tmp_path)
            Path(path).write_bytes(Path(tmp_path).read_bytes())
        except (GLib.Error, OSError) as exc:
            self._set_busy(busy=False, status=f"Export failed: {exc}")
            return
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        self._set_busy(busy=False, status=f"Exported to {path}")

    @staticmethod
    def _copy_exif(source_jpeg: bytes, dest_path: str) -> None:
        """Transplant the camera JPEG's metadata onto the exported file."""
        try:
            metadata = GExiv2.Metadata()
            metadata.open_buf(source_jpeg)
            metadata.set_orientation(GExiv2.Orientation.NORMAL)
            metadata.save_file(dest_path)
        except GLib.Error:
            pass

    @staticmethod
    def _oriented_pixbuf(jpeg: bytes) -> Any:
        """Decode JPEG bytes into an EXIF-oriented pixbuf.

        Static and GTK-free so it can run on a worker thread; the result is
        only attached to widgets back on the main thread.
        """
        loader = GdkPixbuf.PixbufLoader()
        loader.write(jpeg)
        loader.close()
        return loader.get_pixbuf().apply_embedded_orientation()

    def _pixbuf_from_jpeg(self, jpeg: bytes) -> Any:
        """Decode JPEG bytes, applying EXIF orientation and rotation."""
        pixbuf = self._oriented_pixbuf(jpeg)
        rotation = _ROTATIONS.get(self._rotation)
        return pixbuf.rotate_simple(rotation) if rotation else pixbuf

    def _show_jpeg(self, jpeg: bytes) -> None:
        """Display JPEG bytes in the main preview and remember them."""
        self._last_jpeg = jpeg
        try:
            pixbuf = self._pixbuf_from_jpeg(jpeg)
        except GLib.Error as exc:
            self._set_busy(busy=False, status=f"Cannot display image: {exc}")
            return
        self._pixbuf = pixbuf
        self._apply_zoom()
        self.rotate_left.set_sensitive(True)
        self.rotate_right.set_sensitive(True)

    def _set_zoom(self, value: float) -> None:
        """Set the preview zoom factor (1.0 = fit; <1 shrinks below fit)."""
        self._zoom = max(0.1, min(value, 8.0))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        """Show the preview at the zoom factor by scaling the pixbuf.

        The displayed paintable's *intrinsic* size is the zoom size, so
        the wrapping GtkBox centres it (below fit, with the canvas
        background around it) or lets the scroller scroll it (above fit).
        At 1.0 the full pixbuf fills the area (responsive to resize).
        """
        if self._pixbuf is None:
            return
        pw, ph = self._pixbuf.get_width(), self._pixbuf.get_height()
        if pw <= 0 or ph <= 0:
            return
        if self._zoom == 1.0:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.picture.set_paintable(
                Gdk.Texture.new_for_pixbuf(self._pixbuf)
            )
            return
        vw = self.preview_scroll.get_width() or pw
        vh = self.preview_scroll.get_height() or ph
        fit = min(vw / pw, vh / ph)
        scaled = self._pixbuf.scale_simple(
            max(1, int(pw * fit * self._zoom)),
            max(1, int(ph * fit * self._zoom)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        self.picture.set_can_shrink(False)
        self.picture.set_halign(Gtk.Align.CENTER)
        self.picture.set_valign(Gtk.Align.CENTER)
        self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(scaled))

    def _on_scroll_zoom(self, controller: Any, _dx: float, dy: float) -> bool:
        """Zoom the preview on Ctrl+scroll."""
        event = controller.get_current_event()
        state = event.get_modifier_state() if event else 0
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        self._set_zoom(self._zoom * (1.25 if dy < 0 else 1 / 1.25))
        return True

    def _apply_background(self, css_class: str) -> None:
        """Set the preview canvas background to the given CSS class."""
        for cls in _BACKGROUNDS:
            if cls:
                self.preview_scroll.remove_css_class(cls)
        if css_class:
            self.preview_scroll.add_css_class(css_class)
        self._settings.canvas_background = css_class

    def _cycle_background(self) -> None:
        """Cycle the preview background (themed -> white -> gray -> black)."""
        current = self._settings.canvas_background
        index = _BACKGROUNDS.index(current) if current in _BACKGROUNDS else 0
        self._apply_background(_BACKGROUNDS[(index + 1) % len(_BACKGROUNDS)])
        save_settings(self._settings, settings_path())

    def _populate_exif_rows(self, rows: list[tuple[str, str]]) -> None:
        """Show already-parsed EXIF (label, value) pairs in the Image group."""
        for row in self._exif_rows:
            self.exif_group.remove(row)
        self._exif_rows = []
        for label, value in rows:
            row = Adw.ActionRow(title=label, subtitle=value)
            self.exif_group.add(row)
            self._exif_rows.append(row)

    def _read_exif(self, jpeg: bytes) -> list[tuple[str, str]]:
        """Read raw EXIF tags from JPEG bytes and format them."""
        GExiv2.initialize()
        meta = GExiv2.Metadata()
        try:
            meta.open_buf(jpeg)
        except GLib.Error:
            return []
        raw = {}
        for _label, tag, _fmt in exif.EXIF_FIELDS:
            try:
                value = meta.try_get_tag_string(tag)
            except GLib.Error:
                value = None
            if value:
                raw[tag] = value
        return exif.format_exif(raw)

    def _set_busy(self, *, busy: bool, status: str) -> None:
        """Toggle the spinner and recipe controls, and set the status."""
        self._set_spinner(active=busy)
        # Controls stay live while the camera works - the worker coalesces
        # rapid changes - so the UI never locks up mid-render.
        enabled = self._session.is_open
        for row in (*self._combo_rows, *self._slider_rows, self._wb_grid):
            row.set_sensitive(enabled)
        if enabled:
            self._update_temp_visibility()
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

    def _refresh_camera_status(self) -> bool:
        """Update the header subtitle with the connected camera, if any."""
        model = self._detect_camera()
        self.window_title.set_subtitle(
            f"{model} connected" if model else "No camera"
        )
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _detect_camera() -> str | None:
        """Return the connected camera's label, or None if none is found.

        Reads sysfs directly rather than enumerating via libusb: the kernel
        updates /sys on every plug and unplug, whereas libusb caches its
        device list and only refreshes it from udev hotplug events, which a
        Flatpak sandbox never receives - so a body plugged in after launch
        would otherwise go unnoticed. Any Fuji-vendor device counts (matching
        rawji's find_camera), named from _PID_NAMES when its id is known and
        the generic "Camera" otherwise (e.g. an X70).
        """
        vendor = f"{FUJIFILM_USB_VENDOR_ID:04x}"
        try:
            entries = sorted(_USB_SYSFS.iterdir())
        except OSError:
            return None
        for entry in entries:
            try:
                if (entry / "idVendor").read_text().strip() != vendor:
                    continue
                pid = int((entry / "idProduct").read_text().strip(), 16)
            except (OSError, ValueError):
                continue
            return _PID_NAMES.get(pid, "Camera")
        return None

    def _rebuild_menu(self) -> None:
        """(Re)build the header menu model, including the recipe lists."""
        menu = Gio.Menu()

        files = Gio.Menu()
        files.append("Import Recipe…", "win.import-recipe")
        files.append("Export Recipe…", "win.export-recipe")
        menu.append_section(None, files)

        if self._recipes:
            delete_menu = Gio.Menu()
            for name in sorted(self._recipes):
                delete_menu.append_item(
                    self._recipe_item(name, "win.delete-recipe")
                )
            recipes = Gio.Menu()
            recipes.append_submenu("Delete Recipe", delete_menu)
            menu.append_section(None, recipes)

        prefs = Gio.Menu()
        prefs.append("Keyboard Shortcuts", "win.shortcuts")
        prefs.append("Preferences", "win.preferences")
        menu.append_section(None, prefs)

        about = Gio.Menu()
        about.append("About grawji", "win.about")
        menu.append_section(None, about)

        self.menu_button.set_menu_model(menu)

    def _on_shortcuts(self) -> None:
        """Show a dialog listing the keyboard shortcuts."""
        groups = {
            "Files": [
                ("Open folder", "<Ctrl>O"),
                ("Export JPEG", "<Ctrl>E"),
            ],
            "Recipe": [
                ("Save recipe", "<Ctrl>S"),
                ("Reset to default", "<Ctrl>R"),
            ],
            "Navigation": [
                ("Previous image", "Left"),
                ("Next image", "Right"),
            ],
            "View": [
                ("Zoom in", "<Ctrl>plus"),
                ("Zoom out", "<Ctrl>minus"),
                ("Fit to window", "<Ctrl>0"),
                ("Cycle background", "b"),
            ],
            "Application": [
                ("Preferences", "<Ctrl>comma"),
                ("Keyboard shortcuts", "<Ctrl>question"),
            ],
        }
        page = Adw.PreferencesPage()
        for title, items in groups.items():
            group = Adw.PreferencesGroup(title=title)
            for label, accel in items:
                row = Adw.ActionRow(title=label)
                shortcut = Gtk.ShortcutLabel(accelerator=accel)
                shortcut.set_valign(Gtk.Align.CENTER)
                row.add_suffix(shortcut)
                group.add(row)
            page.add(group)
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Keyboard Shortcuts")
        dialog.add(page)
        dialog.present(self)

    def _on_about(self) -> None:
        """Show the About dialog."""
        about = Adw.AboutDialog(
            application_name="grawji",
            application_icon="camera-photo-symbolic",
            version=_app_version(),
            website="https://github.com/p5k369/grawji",
            issue_url="https://github.com/p5k369/grawji/issues",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 Patrick Zwerschke",
        )
        about.add_credit_section(
            "Credits",
            [
                "rawji by pinpox https://github.com/pinpox/rawji",
                "petabyt https://github.com/petabyt",
            ],
        )
        about.present(self)

    @staticmethod
    def _recipe_item(name: str, action: str) -> Any:
        """Build a menu item invoking action with the recipe name."""
        item = Gio.MenuItem.new(name, None)
        item.set_action_and_target_value(action, GLib.Variant("s", name))
        return item

    def _delete_recipe(self, name: str) -> None:
        """Remove a saved recipe and persist the change."""
        if name in self._recipes:
            del self._recipes[name]
            save_recipes(self._recipes, recipes_path())
            self._rebuild_menu()
            self._rebuild_recipes()
            self.status.set_label(f"Deleted recipe “{name}”.")

    def _on_preferences(self) -> None:
        """Open the preferences dialog."""
        dialog = PreferencesDialog(
            settings=self._settings, on_change=self._on_settings_changed
        )
        dialog.present(self)

    def _on_settings_changed(self) -> None:
        """Persist settings and apply any that affect the live UI."""
        self._wb_grid.set_colored(self._settings.wb_grid_tint)
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist the current settings to disk."""
        save_settings(self._settings, settings_path())

    def _on_save_recipe(self) -> None:
        """Ask for a name and save the current controls as a recipe."""
        self._prompt_save_recipe(self._current_recipe())

    def _prompt_save_recipe(
        self, recipe: Recipe, default_name: str = "", *, activate: bool = False
    ) -> None:
        """Ask for a name, then store recipe under it and make it active.

        Args:
            recipe: The recipe to store.
            default_name: The name pre-filled in the entry.
            activate: Re-render the preview after saving (used for imports,
                where the saved recipe is new to the controls).
        """
        dialog = Adw.AlertDialog(
            heading="Save recipe", body="Name this recipe:"
        )
        entry = Gtk.Entry(text=default_name)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_default_response("save")
        dialog.set_response_appearance(
            "save", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect(
            "response", self._on_save_recipe_response, entry, recipe, activate
        )
        dialog.present(self)

    def _on_save_recipe_response(
        self,
        _dialog: Any,
        response: str,
        entry: Any,
        recipe: Recipe,
        activate: bool,
    ) -> None:
        """Store the named recipe when the save dialog is confirmed."""
        if response != "save":
            return
        name = entry.get_text().strip()
        if not name:
            return
        self._recipes[name] = recipe
        save_recipes(self._recipes, recipes_path())
        self._rebuild_menu()
        self._rebuild_recipes()
        self._set_active_recipe(recipe, name)
        if activate and self._session.is_open:
            self._render_preview()
        verb = "Imported" if activate else "Saved"
        self.status.set_label(f"{verb} recipe “{name}”.")

    def _apply_recipe(self, name: str) -> None:
        """Apply a saved recipe to the controls and re-render."""
        recipe = self._recipes.get(name)
        if recipe is None:
            return
        self._load_recipe(recipe)
        if self._session.is_open:
            self._render_preview()
        self.status.set_label(f"Applied recipe “{name}”.")

    def _on_import_fp(self) -> None:
        """Pick an X RAW Studio FP file and import its recipe."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import recipe")
        fp_filter = Gtk.FileFilter()
        fp_filter.set_name("X RAW Studio recipes (FP1/FP2/FP3)")
        for pattern in ("*.FP1", "*.FP2", "*.FP3", "*.fp1", "*.fp2", "*.fp3"):
            fp_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(fp_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(fp_filter)
        dialog.open(self, None, self._on_import_fp_response)

    def _on_import_fp_response(self, dialog: Any, result: Any) -> None:
        """Parse the chosen FP file, then save it as a named recipe."""
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        try:
            recipe = parse_fp(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.status.set_label(f"Could not import recipe: {exc}")
            return
        self._prompt_save_recipe(recipe, Path(path).stem, activate=True)

    def _on_export_fp(self) -> None:
        """Pick a path and write the current recipe as an FP file."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export recipe")
        stem = self._active_label or _export_basename(
            self._raf_path or "grawji-recipe"
        )
        dialog.set_initial_name(f"{stem}.FP1")
        dialog.save(self, None, self._on_export_fp_response)

    def _on_export_fp_response(self, dialog: Any, result: Any) -> None:
        """Write the current recipe as an FP file to the chosen path."""
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        profile = self._session.profile
        iopcode = read_iopcode(profile) if profile is not None else None
        text = serialize_fp(
            self._current_recipe(),
            iopcode=iopcode,
            label=self._active_label,
        )
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self.status.set_label(f"Could not export recipe: {exc}")
            return
        self.status.set_label(f"Exported recipe to {path}.")

    def _on_batch_export(self) -> None:
        """Pick a folder and export every RAF with the current recipe."""
        if not self._filmstrip.paths:
            self.status.set_label("No images to export.")
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Batch export to folder")
        dialog.select_folder(self, None, self._on_batch_folder_response)

    def _on_batch_folder_response(self, dialog: Any, result: Any) -> None:
        """Start the batch export into the chosen folder."""
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        out_dir = gfile.get_path()
        if out_dir is not None:
            self._start_batch(out_dir)

    def _start_batch(self, out_dir: str) -> None:
        """Render every RAF in the folder with the current recipe."""
        paths = self._filmstrip.paths
        recipe = self._current_recipe()
        total = len(paths)
        current = str(self._raf_path) if self._raf_path else None
        skip_foreign = self._settings.batch_skip_foreign
        self._set_busy(busy=True, status=f"Batch export: 0/{total}…")

        def task() -> tuple[int, int]:
            exported = 0
            skipped = 0
            for index, raf_file in enumerate(paths, start=1):
                try:
                    self._session.open(raf_file)
                    jpeg = self._session.render(recipe, full_resolution=True)
                except ForeignRafError:
                    # RAF from a different body; skip it and carry on.
                    if not skip_foreign:
                        raise
                    skipped += 1
                else:
                    name = _export_basename(raf_file)
                    Path(out_dir, name).write_bytes(jpeg)
                    exported += 1
                GLib.idle_add(self._batch_progress, index, total)
            if current is not None:
                self._session.open(current)  # restore the open image
            return exported, skipped

        self._worker.submit(
            task, on_done=self._on_batch_done, on_error=self._on_error
        )

    def _batch_progress(self, index: int, total: int) -> int:
        """Update the status label with batch progress (on the main loop)."""
        self.status.set_label(f"Batch export: {index}/{total}…")
        return GLib.SOURCE_REMOVE

    def _on_batch_done(self, result: Any) -> None:
        """Report batch completion (result is (exported, skipped))."""
        exported, skipped = result
        status = f"Batch exported {exported} image(s)."
        if skipped:
            status += f" Skipped {skipped} from another camera."
        self._set_busy(busy=False, status=status)

    def _on_close_request(self, _window: Any) -> bool:
        """Persist window size, stop the worker, then allow closing."""
        self._settings.window_width = self.get_width()
        self._settings.window_height = self.get_height()
        self._settings.sidebar_width = self.main_paned.get_position()
        save_settings(self._settings, settings_path())
        self._worker.stop()
        return False
