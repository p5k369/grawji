"""Main application window."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from grawji import mainloop, shortcuts, sidecar
from grawji.camera import camera_info
from grawji.camera.capabilities import (
    BASELINE,
    Capabilities,
    capabilities_for,
    capabilities_for_model,
    read_iopcode,
)
from grawji.camera.core import (
    CameraSession,
    recipe_from_profile,
    shot_dynamic_range,
)
from grawji.camera.preview import CameraWorker
from grawji.controllers.backup import BackupController
from grawji.controllers.camera_ops import CameraOpsController
from grawji.controllers.edit_match import (
    EditMatchController,
)
from grawji.controllers.exports import (
    BatchController,
    SingleExportController,
)
from grawji.controllers.fileops import FileOpsController
from grawji.controllers.image_load import (
    Decoded,
    ImageLoadController,
    LoadHost,
)
from grawji.controllers.marks import MarksController
from grawji.controllers.preview_render import (
    PreviewHost,
    PreviewRenderController,
)
from grawji.image_exposure import ImageExposure
from grawji.imaging import imagemeta
from grawji.imaging.thumbnails import ThumbnailLoader
from grawji.marks import Label
from grawji.open_plan import plan_open
from grawji.pairs import Source
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary, recipes_path
from grawji.settings import (
    FROM_IMAGE,
    cache_dir,
    load_settings,
    save_settings,
    settings_path,
)
from grawji.transplant import opener
from grawji.views import dialogs
from grawji.views.browse_page import BrowseHost, BrowsePage, BrowseWidgets
from grawji.views.camera_notices import CameraNotices, NoticeHost
from grawji.views.edit_match_flow import EditMatchFlow, EditMatchHost
from grawji.views.filmstrip import FilmStrip
from grawji.views.filmstrip_nav import FilmStripNav
from grawji.views.folder_grid import FolderGrid
from grawji.views.folder_model import FolderModel
from grawji.views.foldertree import FolderTree
from grawji.views.info_panel import InfoPanel
from grawji.views.navigator import Navigator
from grawji.views.preferences import PreferencesDialog
from grawji.views.preview_view import PreviewView
from grawji.views.recipe_actions import RecipeActions, RecipeHost
from grawji.views.recipe_library import RecipeLibraryController
from grawji.views.recipe_panel import RecipePanel
from grawji.views.source_switch import SourceHost, SourceSwitch

_CAMERA_POLL_SECONDS = 3
# The status while browsing without a camera on USB.
_NO_CAMERA = "No camera connected, showing the in-camera JPEG."
# Default side-panel width, used to restore it after a collapse.
_DEFAULT_SIDEBAR_WIDTH = 240
# Pane positions at or below this count as collapsed.
_SIDEBAR_COLLAPSED_MAX = 10

_UI = (
    resources.files("grawji")
    .joinpath("ui", "grawji.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class MainWindow(Adw.ApplicationWindow):
    """grawji main window: browse RAFs, tune recipe, preview, export."""

    __gtype_name__ = "MainWindow"

    sidebar_button = Gtk.Template.Child()
    filter_button = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
    preview_view: PreviewView = Gtk.Template.Child()
    recipe_panel: RecipePanel = Gtk.Template.Child()
    original_picture = Gtk.Template.Child()
    left_stack = Gtk.Template.Child()
    nav_overlay = Gtk.Template.Child()
    exif_group = Gtk.Template.Child()
    filmstrip_slot = Gtk.Template.Child()
    view_stack = Gtk.Template.Child()
    grid_slot = Gtk.Template.Child()
    grid_size = Gtk.Template.Child()
    grid_count = Gtk.Template.Child()
    foldertree_slot = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()

    _marks: MarksController
    _mark_actions: list[Gio.SimpleAction]

    def __init__(self, **kwargs: object) -> None:
        """Wire up the worker, the composite widgets and the controllers."""
        super().__init__(**kwargs)
        self._session = CameraSession(
            open_foreign=opener.from_settings(lambda: self._settings)
        )
        self._worker = CameraWorker(self._session, dispatch=mainloop.call)
        self._worker.start()
        self.connect("close-request", self._on_close_request)

        self._raf_path: Path | None = None
        self._exposure = ImageExposure()
        self._current_folder: str | None = None
        self._close_confirmed = self._camera_seen = False
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
        self._install_assets()

        self._notices = CameraNotices(
            self,
            self._session,
            self._settings,
            NoticeHost(
                current_raf=lambda: self._raf_path,
                set_busy=lambda busy, status: self._set_busy(
                    busy=busy, status=status
                ),
                abort_batch=lambda: self._batch.abort(),  # noqa: PLW0108
                add_toast=lambda toast: self.toast_overlay.add_toast(  # noqa: PLW0108
                    toast
                ),
                manage_carriers=lambda: self._library.manage(),  # noqa: PLW0108
            ),
        )
        self._init_preview_wiring()
        self._recipe_library = RecipeLibrary(recipes_path())
        self._report_damaged_library()
        self._init_render()
        self._connect_recipe_panel()
        self.export_button.connect("clicked", self._on_export_clicked)

        self._init_info()

        self._fileops = FileOpsController(
            parent=self,
            settings=self._settings,
            save_settings=self._save_settings,
            filmstrip=lambda: self._filmstrip,
            current_raf=lambda: self._raf_path,
            begin_export=lambda paths: self._batch.begin(  # noqa: PLW0108
                paths
            ),
            set_status=self.preview_view.set_status,
            add_toast=self.toast_overlay.add_toast,
        )
        self._init_filmstrip()

        self._foldertree = FolderTree(
            on_select=self._scan_folder,
            bookmarks=self._settings.bookmarks,
            on_bookmarks_changed=self._on_bookmarks_changed,
            on_expansion_changed=self._on_expanded_changed,
            on_drop_paths=self._fileops.on_tree_drop,
            on_activate=self._browse_folder,
        )
        self._foldertree.set_vexpand(True)
        self.foldertree_slot.append(self._foldertree)

        self._init_library()
        self._init_recipes()
        self._init_exports()
        self._install_actions()
        self._refresh_camera_status()
        GLib.timeout_add_seconds(
            _CAMERA_POLL_SECONDS, self._refresh_camera_status
        )

        self._restore_last()

    def _init_render(self) -> None:
        """Build the controllers that load and render the open image."""
        self._render = PreviewRenderController(
            self._session,
            self._worker,
            PreviewHost(
                generation=lambda: self._generation,
                recipe=self.recipe_panel.get_recipe,
                baseline=self._recipe_library.baseline_recipe,
                comparing=self._compare_wanted,
                on_settled=self._persist_exposure,
                show_preview=self.preview_view.show_pixbuf,
                show_baseline=self._show_baseline,
                set_busy=lambda busy, status: self._set_busy(
                    busy=busy, status=status
                ),
                on_error=self._notices.report,
            ),
        )
        self._load = ImageLoadController(
            self._session,
            self._worker,
            LoadHost(
                generation=lambda: self._generation,
                camera_file=self._camera_file_of,
                camera_present=self._camera_present,
                on_begin=self._begin_load,
                on_decoded=self._show_decoded,
                on_opened=self._on_opened,
                on_no_camera=lambda: self._set_busy(
                    busy=False, status=_NO_CAMERA
                ),
                on_error=self._notices.report,
                on_quiet_error=self._notices.report_quietly,
            ),
        )

    def _init_library(self) -> None:
        """Build the camera operations and the recipe library."""
        self._camera_ops = CameraOpsController(self._session)
        self._backup = BackupController(
            parent=self,
            camera_ops=self._camera_ops,
            settings=self._settings,
            save_settings=self._save_settings,
            set_busy=lambda busy, status: self._set_busy(
                busy=busy, status=status
            ),
            set_status=self.preview_view.set_status,
            add_toast=self.toast_overlay.add_toast,
        )
        self._library = RecipeLibraryController(
            parent=self,
            library=self._recipe_library,
            panel=self.recipe_panel,
            on_render=self._render.render_if_open,
            on_status=self.preview_view.set_status,
            get_iopcode=self._read_iopcode,
            on_baseline_changed=self._on_baseline_changed,
            get_capabilities=self._current_capabilities,
            get_model=camera_info.detect_camera,
            load_bank_names=self._camera_ops.load_bank_names,
            carriers_for=self._carriers_for,
            add_carrier=self._add_carrier,
            forget_carrier=self._forget_carrier,
            current_raf=lambda: (
                str(self._raf_path) if self._raf_path is not None else None
            ),
            run_transfer=self._camera_ops.run_bank_transfer,
            render_thumb=self._render_recipe_thumb,
            on_closed=self._on_recipe_manager_closed,
            on_apply_saved=lambda name: self._recipes.apply(name),  # noqa: PLW0108
        )

    def _init_recipes(self) -> None:
        """Build what applies recipes to the controls."""
        self._recipes = RecipeActions(
            self,
            self.recipe_panel,
            self._library,
            self._settings,
            RecipeHost(
                library=self._recipe_library,
                exposure=self._exposure,
                profile=lambda: self._session.profile,
                uses_camera_file=lambda: (
                    self._camera_file_of(self._raf_path) is not None
                ),
                render_if_open=self._render.render_if_open,
                persist_exposure=self._persist_exposure,
                set_status=self.preview_view.set_status,
                save_settings=self._save_settings,
            ),
        )

    def _init_exports(self) -> None:
        """Build the single and the batch export."""
        self._single_export = SingleExportController(
            parent=self,
            worker=self._worker,
            session=self._session,
            settings=self._settings,
            save_settings=self._save_settings,
            get_recipe=self.recipe_panel.get_recipe,
            get_provenance=self._provenance,
            get_current_raf=(
                lambda: str(self._raf_path) if self._raf_path else None
            ),
            base_identity=self.preview_view.geometry_is_identity,
            get_crop=lambda: self.preview_view.crop_rotate,
            set_busy=self._set_busy,
            on_error=self._notices.report,
            on_status_link=self.preview_view.set_status_link,
            get_camera_file=lambda: self._camera_file_of(self._raf_path),
        )
        self._batch = BatchController(
            parent=self,
            worker=self._worker,
            session=self._session,
            settings=self._settings,
            get_recipe=self.recipe_panel.get_recipe,
            get_provenance=self._provenance,
            get_current_raf=(
                lambda: str(self._raf_path) if self._raf_path else None
            ),
            set_busy=self._set_busy,
            on_status=self.preview_view.set_status,
            on_error=self._notices.report,
            on_status_link=self.preview_view.set_status_link,
            camera_file=self._camera_file_of,
        )

    def _restore_last(self) -> None:
        """Reopen the folder and the image of the last session."""
        self._foldertree.restore_expanded(self._settings.expanded_folders)
        last = self._settings.last_folder
        if last and Path(last).is_dir():
            self._scan_folder(last)
            self._foldertree.reveal_path(last)
            image = self._settings.last_image
            if image and Path(image).is_file():
                self._filmstrip.select_path(image)

    def _install_actions(self) -> None:
        """Install window actions used by the menu and keyboard shortcuts."""
        view = self.preview_view
        specs: tuple[tuple[str, Callable[[], None]], ...] = (
            ("export", lambda: self._on_export_clicked(None)),
            ("save-recipe", self._library.save_current),
            ("paste-recipe", self._library.paste_text),
            ("copy-recipe", self._library.copy_text),
            ("reset", self._recipes.reset),
            ("undo", self._recipes.undo),
            ("redo", self._recipes.redo),
            ("preferences", self._on_preferences),
            ("export-marked", self._export_marked),
            ("export-shown", self._export_shown),
            ("trash-rejected", self._trash_rejected),
            ("select-all", self._filmstrip.select_all),
            ("cancel-selection", self._filmstrip.clear_selection),
            ("manage-recipes", self._library.manage),
            ("backup-camera", self._backup.backup),
            ("restore-camera", self._backup.restore),
            ("zoom-in", view.zoom_in),
            ("zoom-out", view.zoom_out),
            ("zoom-fit", view.zoom_fit),
            ("cycle-background", self._cycle_background),
            ("toggle-peek", self._toggle_peek),
            ("toggle-crop", self._toggle_crop),
            ("toggle-export", self._marks.toggle_export),
            ("toggle-reject", self._marks.toggle_reject),
            ("shortcuts", lambda: dialogs.present_shortcuts(self)),
            ("about", lambda: dialogs.present_about(self)),
        )
        for name, callback in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", partial(self._activate, callback))
            self.add_action(action)
        app = self.get_application()
        if app is not None:
            for name, accels in shortcuts.ACCELS.items():
                app.set_accels_for_action(f"win.{name}", list(accels))
        self._on_history_changed(self.recipe_panel)

        self._add_toggle("toggle-grid", self._on_toggle_grid, on=False)
        self._add_toggle(
            "toggle-histogram",
            self._on_toggle_histogram,
            on=self._settings.show_histogram,
        )
        self._add_toggle(
            "toggle-marks-bar",
            self._on_toggle_marks_bar,
            on=self._settings.show_marks_bar,
        )
        self._install_mark_actions()

        # Compare is available only once a recipe is marked as baseline.
        self._compare_action = self._add_toggle(
            "toggle-compare", self._on_toggle_compare, on=False
        )
        self._compare_action.set_enabled(
            self._recipe_library.baseline is not None
        )

    def _add_toggle(
        self, name: str, on_change: Callable[[Any, Any], None], *, on: bool
    ) -> Gio.SimpleAction:
        """Add a stateful on/off action to the window."""
        action = Gio.SimpleAction.new_stateful(
            name, None, GLib.Variant.new_boolean(on)
        )
        action.connect("change-state", on_change)
        self.add_action(action)
        return action

    def _install_mark_actions(self) -> None:
        """The parameterized mark actions."""
        rate = Gio.SimpleAction.new("rate", GLib.VariantType.new("i"))
        rate.connect("activate", lambda _a, v: self._marks.rate(v.get_int32()))
        self.add_action(rate)
        label = Gio.SimpleAction.new("toggle-label", GLib.VariantType.new("s"))
        label.connect(
            "activate",
            lambda _a, v: self._marks.toggle_label(Label(v.get_string())),
        )
        self.add_action(label)
        self._mark_actions.extend([rate, label])
        for name in ("toggle-export", "toggle-reject"):
            action = self.lookup_action(name)
            if isinstance(action, Gio.SimpleAction):
                self._mark_actions.append(action)

    def _camera_file_of(self, raf_path: str | Path | None) -> str | None:
        """The camera file a shot is developed from."""
        return self._source.camera_file_of(raf_path)

    def _gate_compare(self, uses_camera_file: bool) -> None:
        """Keep the split view to shots developed from their RAW."""
        if uses_camera_file and self._compare_wanted():
            self._compare_action.change_state(GLib.Variant.new_boolean(False))
        self._compare_action.set_enabled(
            self._recipe_library.baseline is not None and not uses_camera_file
        )

    def _on_card_mark(
        self, kind: str, value: object, paths: list[str]
    ) -> None:
        """Apply a mark picked from a card's context menu."""
        self._marks.from_menu(kind, value, paths)

    def _on_marks_changed(self, paths: list[str]) -> None:
        """Redraw the bar when the open image's marks changed."""
        if self._filmstrip.current_path in paths:
            self._show_current_marks()

    def _show_current_marks(self) -> None:
        """Show the open image's marks, and gate the mark actions."""
        self.preview_view.marks_bar.show_marks(
            self._marks.marks_of(self._filmstrip.current_path)
        )
        enabled = bool(self._filmstrip.mark_targets())
        for action in self._mark_actions:
            action.set_enabled(enabled)

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

    def _init_preview_wiring(self) -> None:
        """Apply preview settings and hook up its crop editor."""
        self.preview_view.set_background(self._settings.canvas_background)
        self.preview_view.set_show_histogram(self._settings.show_histogram)
        self.preview_view.set_show_marks_bar(self._settings.show_marks_bar)
        self.preview_view.connect(
            "geometry-changed", self._on_geometry_changed
        )
        self.preview_view.set_crop_guides(self._settings.crop_guides)
        self.preview_view.crop_guides.connect(
            "notify::selected", self._on_guides_changed
        )
        self._apply_export_border()
        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_window_key)
        self.add_controller(keys)

    def _apply_export_border(self) -> None:
        """Feed the preview the effective export framing settings."""
        s = self._settings
        if s.export_border_enabled:
            self.preview_view.set_export_border(
                s.export_border_percent,
                s.export_border_color,
                s.export_border_aspect,
            )
        else:
            self.preview_view.set_export_border(0.0, s.export_border_color)

    def _install_assets(self) -> None:
        """Install display-wide assets: the app CSS and bundled icons."""
        self._install_css()
        self._install_icons()

    def _install_css(self) -> None:
        """Load the app's stylesheet."""
        provider = Gtk.CssProvider()
        css = resources.files("grawji").joinpath("ui", "style.css")
        provider.load_from_string(css.read_text(encoding="utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _install_icons(self) -> None:
        """Register the bundled icons ahead of any installed copy."""
        icons = resources.files("grawji").joinpath("ui", "icons")
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.set_search_path([str(icons), *theme.get_search_path()])

    def _init_filmstrip(self) -> None:
        """Build the filmstrip flanked by previous/next navigation."""
        self._folder_model = FolderModel()
        self._folder_model.default_source = self._pair_source()
        self._folder_model.add_listener(self._on_folder_event)
        self._filmstrip = FilmStrip(
            model=self._folder_model,
            on_select=self._on_raf_selected,
            on_loading=self._on_thumbs_loading,
            on_selection_changed=self._on_selection_changed,
            on_file_action=self._fileops.on_file_action,
            on_mark_action=self._on_card_mark,
            drag_action=lambda: self._settings.drag_action,
        )
        self._source = SourceSwitch(
            self,
            self._folder_model,
            (
                self.preview_view.source_switch,
                self.preview_view.camera_source_button,
            ),
            SourceHost(
                open_path=lambda: self._raf_path,
                selected_path=lambda: self._filmstrip.current_path,
                reload=self._on_raf_selected,
                on_shown=self._gate_compare,
            ),
        )
        self._mark_actions = []
        self._marks = MarksController(
            folder=self._folder_model,
            targets=self._filmstrip.mark_targets,
            on_changed=self._on_marks_changed,
        )
        self._filmstrip.set_glide_speed(self._settings.nav_glide_speed)
        self._filmstrip.set_hexpand(True)
        self._nav = FilmStripNav(self._filmstrip)
        self._nav.attach_keys(self)
        self.filmstrip_slot.append(self._nav.prev_button)
        self.filmstrip_slot.append(self._filmstrip)
        self.filmstrip_slot.append(self._nav.next_button)
        self._filmstrip.adopt_filter_button(self.filter_button)
        self._init_grid()

    def _init_grid(self) -> None:
        """Build the folder grid that shares the preview's place."""
        self._grid = FolderGrid(
            model=self._folder_model,
            on_file_action=self._fileops.on_file_action,
            on_mark_action=self._on_card_mark,
            loader=ThumbnailLoader(
                height=self._settings.grid_tile_px,
                cache_dir=cache_dir() / "thumbs",
                workers=max(1, (os.cpu_count() or 2) - 1),
                # The camera's own thumbnail is 160 by 120, so a tile
                # this size comes from the RAF's full preview instead.
                sharp=True,
                dispatch=mainloop.call,
            ),
            tile=self._settings.grid_tile_px,
            on_activate=self._on_grid_activated,
            on_select=self._on_grid_selected,
            stand_in=self._filmstrip.texture_for,
        )
        self._browse = BrowsePage(
            self._grid,
            BrowseWidgets(
                slot=self.grid_slot,
                size=self.grid_size,
                count=self.grid_count,
                stack=self.view_stack,
            ),
            self._settings,
            BrowseHost(
                current_path=lambda: self._filmstrip.current_path,
                has_folder=lambda: self._current_folder is not None,
                on_page=self._on_page_changed,
                save_settings=self._save_settings,
            ),
        )

    def _on_toggle_grid(self, action: Any, value: Any) -> None:
        """Flip between developing one frame and browsing the folder."""
        action.set_state(value)
        self._browse.set_open(open_it=value.get_boolean())

    def _on_page_changed(self, browsing: bool) -> None:
        """Follow the page the tabs, the key or the code chose."""
        action = self.lookup_action("toggle-grid")
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(browsing))
        self._nav.cancel_hold()
        if not browsing:
            self._develop_current()

    def _browse_folder(self, path: str) -> None:
        """Open a folder in Browse, which is what a double click asks."""
        self._scan_folder(path)
        self._browse.set_open(open_it=True)

    def _on_folder_event(self, reason: str, _path: str | None) -> None:
        """Keep the frame count and the marks bar fresh."""
        if reason in ("folder", "filter", "trimmed") and self._browse.is_open:
            self._browse.update_count()
        if reason in ("folder", "trimmed"):
            self._show_current_marks()

    def _on_grid_selected(self, path: str) -> None:
        """Mark the grid's pick in the strip, without developing it."""
        if self._filmstrip.current_path != path:
            self._filmstrip.select_path(path, notify=False)
        self._show_current_marks()

    def _on_grid_activated(self, path: str) -> None:
        """Open a frame picked in the grid and go back to the preview."""
        self._filmstrip.select_path(path, notify=False)
        self._browse.set_open(open_it=False)

    def _develop_current(self) -> None:
        """Load whatever Browse left selected, unless it is loaded."""
        path = self._filmstrip.current_path
        if path is None or str(self._raf_path) == path:
            return
        self._on_raf_selected(path)

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
        if self._browse.is_open:
            self._browse.fill()
        self._nav.update()
        self._settings.last_folder = path
        self._save_settings()
        self.preview_view.set_status("Select an image from the filmstrip.")

    def _on_raf_selected(self, raf_path: str, *, quiet: bool = False) -> None:
        """React to the click instantly; load the RAF on the next idle tick.

        The file read, JPEG decode and EXIF parse are deferred so the click
        (filmstrip highlight) paints first instead of waiting on them.

        Args:
            raf_path: The RAF to load.
            quiet: A failed camera open logs instead of raising a
                dialog.
        """
        self._nav.update()
        self._render.flush()
        self._generation += 1
        self._raf_path = Path(raf_path)
        self._show_current_marks()
        self._source.show_current()
        self._settings.last_image = raf_path
        self.set_title(f"grawji — {Path(raf_path).name}")
        self._set_busy(busy=True, status="Loading RAF…")
        self._load.select(raf_path, quiet=quiet)

    def _begin_load(self, raf_path: str) -> None:
        """Take the image's stored crop and exposure as its load starts."""
        self.preview_view.set_crop(sidecar.load_crop(raf_path))
        self._exposure.begin(sidecar.load_exposure(raf_path))

    def _camera_present(self) -> bool:
        """Whether a camera is there to open a RAF on."""
        return self._session.is_open or bool(camera_info.detect_camera())

    def _show_decoded(self, decoded: Decoded) -> None:
        """Show the image's own picture and its EXIF."""
        self.preview_view.set_embedded_jpeg(decoded.jpeg)
        self.preview_view.set_native_size(decoded.native)
        if decoded.pixbuf is not None:
            self.preview_view.show_pixbuf(decoded.pixbuf)
        else:
            self.preview_view.clear_source()
        self._info.show_original(decoded.pixbuf)
        self._info.show_exif(decoded.rows)
        if self._camera_file_of(self._raf_path) is not None:
            self._set_busy(busy=False, status=self._source.status())

    def _report_damaged_library(self) -> None:
        """Say so when the recipe library could not be read."""
        kept = self._recipe_library.kept_aside
        if kept is None:
            return
        toast = Adw.Toast.new(
            f"The recipe library could not be read. It is kept as {kept.name}."
        )
        toast.set_timeout(0)
        self.toast_overlay.add_toast(toast)

    def _init_info(self) -> None:
        """Wire the Info tab's original thumbnail and EXIF rows."""
        self._info = InfoPanel(
            stack=self.left_stack,
            picture=self.original_picture,
            group=self.exif_group,
            navigator=Navigator(
                area=self.nav_overlay,
                scroll=self.preview_view.scroll,
                picture=self.original_picture,
                get_rotation=lambda: self.preview_view.rotation,
            ),
        )

    def _on_opened(self) -> None:
        """Show the first preview, applying the sticky recipe selection."""
        panel = self.recipe_panel
        carry = panel.get_recipe() if panel.needs_save else None
        profile = self._session.profile
        if profile is not None:
            model = self._rendering_model()
            foreign = self._session.foreign is not None
            self._info.set_developed_by(
                self._session.model if foreign else None
            )
            panel.apply_capabilities(capabilities_for(profile, model=model))
            panel.set_dr_ceiling(shot_dynamic_range(profile))
            self._notices.notify_unverified(model)
        else:
            panel.set_dr_ceiling(None)
        shot = recipe_from_profile(profile) if profile is not None else None
        selection = self._settings.open_recipe
        plan = plan_open(
            shot=shot,
            stored_ev=self._exposure.stored,
            carry=carry,
            selection=selection,
            saved=(
                None
                if selection == FROM_IMAGE
                else self._recipe_library.get(selection)
            ),
            has_embedded=self.preview_view.has_embedded_jpeg,
        )
        if plan.recipe is not None:
            if plan.label is None:
                panel.set_recipe(plan.recipe)
            else:
                panel.set_active(plan.recipe, plan.label)
        panel.set_exposure(plan.exposure)
        self._exposure.opened((shot or Recipe()).exposure, plan.exposure)
        panel.reset_history()
        if plan.render:
            self._render.render()
        else:
            self._set_busy(busy=False, status="Ready.")
        # Comparing carries across images: refresh the baseline for this one.
        if self.preview_view.comparing:
            self._render.render_baseline()

    def open_raf(self, path: str) -> None:
        """Open a RAF or a folder from outside."""
        target = Path(path)
        if target.is_dir():
            self._scan_folder(str(target))
            self._foldertree.reveal_path(str(target))
            return
        folder = str(target.parent)
        self._scan_folder(folder)
        self._foldertree.reveal_path(folder)
        if not self._filmstrip.select_path(path):
            self.preview_view.set_status(
                f"{target.name} is not in the filmstrip."
            )

    def _read_iopcode(self) -> int | None:
        """The open profile's IOPCode."""
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
            self._render.render_baseline()  # baseline recipe changed

    def _on_toggle_compare(self, action: Any, value: Any) -> None:
        """Start or stop the baseline split-compare view."""
        if value.get_boolean():
            if (
                self._recipe_library.baseline_recipe() is None
                or not self._session.is_open
            ):
                return  # nothing to compare, leave the toggle off
            action.set_state(value)
            self._render.render_baseline()
        else:
            action.set_state(value)
            self.preview_view.set_compare(on=False)

    def _compare_wanted(self) -> bool:
        """Whether the split-compare toggle is switched on."""
        return bool(self._compare_action.get_state().get_boolean())

    def _show_baseline(self, jpeg: bytes) -> None:
        """Open the split view on a rendered baseline."""
        self.preview_view.set_compare_baseline(jpeg)
        self.preview_view.set_compare(on=True)

    def _on_recipe_changed(self, _panel: Any) -> None:
        """Re-render after a recipe edit."""
        if not self._session.is_open:
            return
        self._render.schedule()

    def _persist_exposure(self) -> None:
        """Store the image's EV in its sidecar when it changed."""
        if self._raf_path is None:
            return
        changed, value = self._exposure.settle(
            self.recipe_panel.get_recipe().exposure
        )
        if not changed:
            return
        sidecar.save_exposure(self._raf_path, value)
        self._filmstrip.refresh_badges(str(self._raf_path))

    def _connect_recipe_panel(self) -> None:
        """Wire the recipe panel's settings and signals."""
        self.recipe_panel.set_wb_grid_tint(self._settings.wb_grid_tint)
        self.recipe_panel.connect("changed", self._on_recipe_changed)
        self.recipe_panel.connect("history-changed", self._on_history_changed)
        self.recipe_panel.connect(
            "apply-recipe", lambda _panel, name: self._recipes.apply(name)
        )
        self.recipe_panel.connect(
            "paste-recipe", lambda *_a: self._library.paste_text()
        )
        self.recipe_panel.connect(
            "copy-recipe", lambda *_a: self._library.copy_text()
        )
        self.recipe_panel.connect(
            "import-photo", lambda *_a: self._library.import_photo()
        )
        self.recipe_panel.connect(
            "import-fp", lambda *_a: self._library.import_recipe()
        )
        self._edit_flow = EditMatchFlow(
            self,
            EditMatchController(self._session, self._worker),
            self._settings,
            EditMatchHost(
                open_raf=self._open_raf,
                recipe=self.recipe_panel.get_recipe,
                capabilities=lambda: (
                    self.recipe_panel.capabilities or BASELINE
                ),
                set_status=self.preview_view.set_status,
                confirm_discard=lambda proceed: self._recipes.confirm_discard(  # noqa: PLW0108
                    proceed
                ),
                apply=lambda recipe, title: self._recipes.apply_found(  # noqa: PLW0108
                    recipe, title
                ),
            ),
        )
        self.recipe_panel.connect(
            "import-edit", lambda *_a: self._edit_flow.begin()
        )

    def _open_raf(self) -> str | None:
        """The image open on the camera, None when there is none."""
        if self._raf_path is None or not self._session.is_open:
            return None
        return str(self._raf_path)

    def _on_history_changed(self, panel: Any) -> None:
        """Enable undo and redo only while there is a step to take."""
        for name, enabled in (
            ("undo", panel.can_undo),
            ("redo", panel.can_redo),
        ):
            action = self.lookup_action(name)
            if action is not None:
                action.set_enabled(enabled)

    def _render_recipe_thumb(
        self, recipe: Recipe, on_done: Callable[[bytes | None], None]
    ) -> None:
        """Render a recipe against the open RAF into a small thumbnail."""
        if self._raf_path is None:
            on_done(None)
            return
        self._render.render_thumb(str(self._raf_path), recipe, on_done)

    def _on_recipe_manager_closed(self) -> None:
        """Re-open the current RAF if the manager closed its session."""
        if self._raf_path is not None and not self._session.is_open:
            self._on_raf_selected(str(self._raf_path))

    def _on_export_clicked(self, _button: Any) -> None:
        """Start a single full-resolution export."""
        self._single_export.begin()

    def _on_selection_changed(self, _count: int) -> None:
        """Retarget the marks bar and its actions at the new selection."""
        self._show_current_marks()

    def _export_marked(self) -> None:
        """Export every image of the folder marked for export."""
        self._export_batch(
            self._marks.marked_for_export(self._filmstrip.paths),
            "No images in this folder are marked for export.",
            unmark=partial(self._marks.set_export, on=False),
        )

    def _export_shown(self) -> None:
        """Export every image the filter leaves."""
        self._export_batch(
            self._filmstrip.visible_paths, "No images to export."
        )

    def _export_batch(
        self,
        paths: list[str],
        empty: str,
        *,
        unmark: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Export paths in one batch."""
        kept, rejected = self._marks.without_rejects(paths)
        if not kept:
            self.preview_view.set_status(
                "All of them are rejected." if rejected else empty
            )
            return
        note = f"Skipped {rejected} rejected." if rejected else ""
        complaint = self._batch.begin(kept, note=note, unmark=unmark)
        if complaint is not None:
            self.preview_view.set_status(complaint)

    def _trash_rejected(self) -> None:
        """Move every rejected image of the folder to the trash."""
        rejected = self._marks.rejected(self._filmstrip.paths)
        if not rejected:
            self.preview_view.set_status("No rejected images in this folder.")
            return
        self._fileops.trash_paths(rejected, confirm=True)

    def _cycle_background(self) -> None:
        """Cycle the preview background and remember the choice."""
        self._settings.canvas_background = self.preview_view.cycle_background()
        self._browse.set_canvas(self._settings.canvas_background)
        self._save_settings()

    def _toggle_peek(self) -> None:
        """Toggle showing the in-camera original."""
        self.preview_view.set_peek(peeking=not self.preview_view.peeking)

    def _toggle_crop(self) -> None:
        """Open the crop editor, or commit it if it is already open."""
        button = self.preview_view.crop_button
        if button.get_sensitive():
            button.set_active(not button.get_active())

    def _on_window_key(
        self, _controller: Any, keyval: int, _code: int, state: Any
    ) -> bool:
        """Crop-edit keys, then Delete and the recipe number keys."""
        if self.preview_view.crop_editing:
            return self._on_crop_key(keyval)
        if self._focus_is_editable() or self._focus_in_dialog():
            return False
        if keyval == Gdk.KEY_Delete:
            return self._fileops.handle_delete()
        return self._recipes.on_key(keyval, state)

    def _on_crop_key(self, keyval: int) -> bool:
        """Escape cancels and Return commits the crop being edited."""
        if keyval == Gdk.KEY_Escape:
            self.preview_view.cancel_crop()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.preview_view.apply_crop()
            return True
        return False

    def _focus_is_editable(self) -> bool:
        """Whether a text widget owns the focus."""
        focus = self.get_focus()
        return isinstance(focus, (Gtk.Editable, Gtk.Text))

    def _focus_in_dialog(self) -> bool:
        """Whether the focus sits inside a dialog or popover."""
        focus = self.get_focus()
        if focus is None:
            return False
        return (
            focus.get_ancestor(Adw.Dialog) is not None
            or focus.get_ancestor(Gtk.Popover) is not None
        )

    def _on_guides_changed(self, *_args: object) -> None:
        """Remember the chosen composition guides."""
        self._settings.crop_guides = self.preview_view.guides_name
        self._save_settings()

    def _on_geometry_changed(self, _view: Any) -> None:
        """Persist a committed crop/rotation to the RAF's sidecar."""
        if self._raf_path is None:
            return
        sidecar.save_crop(self._raf_path, self.preview_view.crop_rotate)
        self._filmstrip.refresh_badges(str(self._raf_path))

    def _set_busy(self, *, busy: bool, status: str) -> None:
        """Toggle the spinner and recipe controls, and set the status."""
        self.preview_view.set_spinner(active=busy)
        # Controls stay live while the camera works - the worker coalesces
        # rapid changes - so the UI never locks up mid-render.
        enabled = (
            self._session.is_open
            and self._camera_file_of(self._raf_path) is None
        )
        self.recipe_panel.set_controls_sensitive(enabled)
        self._update_export_button()
        self.preview_view.set_status(status)

    def _update_export_button(self) -> None:
        """Enable the header Export only when it applies."""
        jpeg = self._camera_file_of(self._raf_path) is not None
        self.export_button.set_sensitive(self._session.is_open or jpeg)

    def _current_capabilities(self) -> Capabilities:
        """Capabilities of the connected body for recipe compatibility."""
        profile = self._session.profile
        if profile is not None:
            return capabilities_for(profile, model=self._rendering_model())
        model = self._session.model or camera_info.detect_camera()
        return capabilities_for_model(model)

    def _carriers_for(self, model: str) -> dict[int, str]:
        """A body's carrier files by dynamic range, as the pane shows them."""
        return opener.listed_carriers(self._settings, model)

    def _add_carrier(self, model: str, path: str) -> str | None:
        """Register a RAF as a body's carrier under its shot dynamic range."""
        problem = opener.add_carrier(self._settings, model, path)
        if problem is None:
            self._save_settings()
        return problem

    def _forget_carrier(self, model: str, dynamic_range: int) -> None:
        """Forget a body's carrier of a dynamic range."""
        opener.forget_carrier(self._settings, model, dynamic_range)
        self._save_settings()

    def _rendering_model(self) -> str | None:
        """The body whose engine develops the open RAF."""
        if self._session.foreign is not None:
            return self._session.model
        raf_model = (
            imagemeta.camera_model(str(self._raf_path))
            if self._raf_path is not None
            else None
        )
        return raf_model or self._session.model

    def _refresh_camera_status(self) -> bool:
        """Update the header subtitle with the connected camera, if any."""
        model = camera_info.detect_camera()
        appeared = bool(model) and not self._camera_seen
        self._camera_seen = bool(model)
        self.preview_view.set_camera(model)
        if (
            appeared
            and self._settings.camera_auto_reconnect
            and self._raf_path is not None
            and not self._session.is_open
        ):
            self._on_raf_selected(str(self._raf_path), quiet=True)
        return GLib.SOURCE_CONTINUE

    def _on_preferences(self) -> None:
        """Open the preferences dialog."""
        dialog = PreferencesDialog(
            settings=self._settings,
            on_change=self._on_settings_changed,
            capabilities=self._current_capabilities(),
        )
        dialogs.fit_dialog(dialog, self, height_fraction=0.8)
        dialog.present(self)

    def _on_bookmarks_changed(self, bookmarks: list[str]) -> None:
        """Persist the folder-tree bookmarks."""
        self._settings.bookmarks = bookmarks
        self._save_settings()

    def _on_expanded_changed(self, paths: list[str]) -> None:
        """Persist the folder-tree expansion state."""
        self._settings.expanded_folders = paths
        self._save_settings()

    def _pair_source(self) -> Source:
        """What a RAW+JPEG shot opens with."""
        try:
            return Source(self._settings.pair_source)
        except ValueError:
            return Source.RAW

    def _apply_pair_source(self) -> None:
        """Reopen the folder when the default source of a pair changed."""
        source = self._pair_source()
        if source is self._folder_model.default_source:
            return
        self._folder_model.default_source = source
        if self._current_folder is not None:
            self._filmstrip.scan(self._current_folder)
        if self._raf_path is not None:
            self._on_raf_selected(str(self._raf_path))

    def _on_settings_changed(self) -> None:
        """Persist settings and apply any that affect the live UI."""
        self._apply_pair_source()
        self.recipe_panel.set_wb_grid_tint(self._settings.wb_grid_tint)
        self._filmstrip.set_glide_speed(self._settings.nav_glide_speed)
        self._apply_export_border()
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

    def _on_toggle_marks_bar(self, action: Any, value: Any) -> None:
        """Show or hide the marks bar and remember the choice."""
        action.set_state(value)
        show = value.get_boolean()
        self._settings.show_marks_bar = show
        self.preview_view.set_show_marks_bar(show)
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist the current settings to disk."""
        save_settings(self._settings, settings_path())

    def _provenance(self) -> str:
        """The recipe provenance for exports, "" when disabled."""
        if not self._settings.export_provenance:
            return ""
        return self.recipe_panel.provenance

    def _on_close_request(self, _window: Any) -> bool:
        """Persist window size, stop the worker, then allow closing."""
        if self.recipe_panel.needs_save and not self._close_confirmed:
            self._recipes.confirm_discard(self._close_discarded)
            return True
        self._render.flush()
        self._settings.window_width = self.get_width()
        self._settings.window_height = self.get_height()
        self._settings.sidebar_width = self.main_paned.get_position()
        self._save_settings()
        self._worker.stop()
        return False

    def _close_discarded(self) -> None:
        """Close for real after the user discarded the recipe edits."""
        self._close_confirmed = True
        self.close()
