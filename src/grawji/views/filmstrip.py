"""Bottom filmstrip of RAF thumbnails."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import (
    Gdk,
    Gio,
    GLib,
    Graphene,
    Gtk,
    Pango,
    PangoCairo,
)

from grawji import catalog, mainloop
from grawji.imaging.thumbnails import ThumbMeta
from grawji.mainloop import Dispatch
from grawji.sidecar import edit_flags
from grawji.views.folder_model import EntryItem, FolderModel
from grawji.views.tile_thumbs import TileThumbs

# Default continuous-scroll speed while a nav arrow is held, in px/second.
_GLIDE_AHEAD_BOOST = 1.18
_GLIDE_PX_PER_S_DEFAULT = 600


# A focal slider settles this long before the filter is applied.
_FOCAL_SETTLE_MS = 120
# Frames the strip waits for a card to be placed before centring it.
_CENTER_FRAMES = 30
# The focal sliders need at least two distinct stops to range over.
_MIN_SLIDER_STOPS = 2

# Folder-change events settle for this long before the strip re-scans.
_RELOAD_DEBOUNCE_MS = 500


def _badged_paintable(
    base: Gdk.Paintable, button: Gtk.Widget, count: int
) -> Gdk.Paintable:
    """Compose a card paintable with a count bubble in its top corner."""
    width = max(1, button.get_width())
    height = max(1, button.get_height())
    snapshot = Gtk.Snapshot()
    base.snapshot(snapshot, width, height)
    layout = button.create_pango_layout(str(count))
    text_w, text_h = layout.get_pixel_size()
    radius = max(text_w, text_h) / 2 + 5
    center_x = width - radius - 4
    center_y = radius + 4
    bounds = Graphene.Rect()
    bounds.init(0, 0, width, height)
    ctx = snapshot.append_cairo(bounds)
    ctx.arc(center_x, center_y, radius, 0, 2 * math.pi)
    ctx.set_source_rgba(0.1, 0.1, 0.1, 0.85)
    ctx.fill()
    ctx.move_to(center_x - text_w / 2, center_y - text_h / 2)
    ctx.set_source_rgba(1, 1, 1, 1)
    PangoCairo.show_layout(ctx, layout)
    return snapshot.to_paintable()


def _is_raf(gfile: Any) -> bool:
    """Whether a monitor-event Gio.File refers to a RAF file."""
    if gfile is None:
        return False
    name = gfile.get_basename() or ""
    return name.lower().endswith(".raf")


class FilmStrip(Gtk.ScrolledWindow):
    """A horizontally-scrolling strip of clickable RAF thumbnails."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        on_select: Callable[[str], None],
        on_loading: Callable[[bool], None] | None = None,
        on_filter_changed: Callable[[], None] | None = None,
        on_selection_changed: Callable[[int], None] | None = None,
        on_file_action: Callable[[str, list[str]], None] | None = None,
        drag_action: Callable[[], str] | None = None,
        dispatch: Dispatch = mainloop.call,
        thumb_height: int = 110,
        model: FolderModel | None = None,
    ) -> None:
        """Create the filmstrip.

        Args:
            on_select: Called with the RAF path when a thumbnail is
                clicked in normal mode (to preview/edit it).
            on_loading: Called with True when thumbnail decoding starts and
                False when it finishes, for an activity indicator elsewhere.
            on_filter_changed: Called whenever the active filter changes.
            on_selection_changed: Called with the number of selected
                thumbnails while in batch-select mode.
            on_file_action: Called with ("export"/"copy"/"move"/"trash",
                paths) from a card's context menu.
            drag_action: Returns the configured default drag action
                ("move" or "copy") for an unmodified drag.
            dispatch: Schedules a callback on the GTK main loop.
            thumb_height: Thumbnail height in pixels.
            model: The folder shared with other views.
        """
        super().__init__()
        self._on_select = on_select
        self._on_loading = on_loading
        self._on_filter_changed = on_filter_changed
        self._on_selection_changed = on_selection_changed
        self._on_file_action = on_file_action
        self._drag_action = drag_action
        self._menu_paths: list[str] = []
        self._menu_click_path: str | None = None
        self._init_file_actions()
        self._dispatch = dispatch
        self._thumb_height = thumb_height
        self._scan_id = 0
        self._paths: list[str] = []
        # The widgets of every bound tile, by its card.
        self._cards: dict[Gtk.Widget, dict[str, Any]] = {}
        self._card_path: dict[Gtk.Widget, str] = {}
        self._current = -1
        # Batch-select mode: while active, a click toggles a card's
        # membership in the export set (shown raised) instead of opening it.
        self._select_mode = False
        self._selected: set[str] = set()
        self._anchor: str | None = None
        self._pending_mods: tuple[str, bool, bool] | None = None
        self._glide_tick: int | None = None
        self._glide_last: int | None = None
        self._glide_dir = 0
        self._glide_speed = float(_GLIDE_PX_PER_S_DEFAULT)
        self._reveal_wanted: tuple[str, bool] | None = None

        self._center_path: str | None = None
        self._center_tick = 0
        self._center_frames = 0
        self._folder: str | None = None
        self._init_filter_state()
        self._monitor: Any = None
        self._reload_pending_id = 0
        self._model = model or FolderModel(
            dispatch=dispatch, thumb_height=thumb_height
        )
        self._model.add_listener(self._on_model_event)
        self._thumbs = self._model.loader

        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._build_view()
        self._tiles = TileThumbs(
            self._thumbs,
            self,
            on_meta=self._note_meta,
            sizes_to_content=True,
        )
        self.set_min_content_height(thumb_height + 52)

        self._watch_scroll()
        # A strip that leaves the screen must not keep gliding.
        self.connect("unmap", lambda *_a: self.stop_glide())
        self.filter_button: Gtk.MenuButton | None = None
        self._filter_actions: dict[str, Gio.SimpleAction] = {}

    def _watch_scroll(self) -> None:
        """Wire the wheel to the strip."""
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        """Look at the viewport again once the strip changed size."""
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        self._tiles.schedule()
        if self._reveal_wanted is not None and width > 0:
            mainloop.call(self._catch_up_reveal)

    def _build_view(self) -> None:
        """The list view over the folder's shared store."""
        self._store = self._model.store
        self._model_filter = Gtk.CustomFilter.new(self._passes)
        self._shown = Gtk.FilterListModel(
            model=self._store, filter=self._model_filter
        )
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)
        self._view = Gtk.ListView(
            model=Gtk.NoSelection(model=self._shown), factory=factory
        )
        self._view.set_orientation(Gtk.Orientation.HORIZONTAL)
        self._view.set_single_click_activate(True)
        self._view.connect("activate", self._on_row_activated)
        self._view.add_css_class("filmstrip")
        self._view.set_margin_top(4)
        self._view.set_margin_bottom(4)
        self._view.set_margin_start(4)
        self._view.set_margin_end(4)
        self.set_child(self._view)

    def _init_filter_state(self) -> None:
        """Start with every filter axis off."""
        self._filter_model: str | None = None
        self._filter_lens: str | None = None
        self._filter_focal: tuple[float, float] | None = None
        self._focal_wanted: tuple[float, float] | None = None
        self._focal_pending = 0

    def adopt_filter_button(self, button: Gtk.MenuButton) -> None:
        """Drive the window's funnel button with the filter menu."""
        group = Gio.SimpleActionGroup()
        for axis in ("model", "lens"):
            action = Gio.SimpleAction.new_stateful(
                axis,
                GLib.VariantType.new("s"),
                GLib.Variant.new_string(""),
            )
            action.connect("change-state", self._on_filter_action, axis)
            group.add_action(action)
            self._filter_actions[axis] = action
        clear = Gio.SimpleAction.new("clear", None)
        clear.connect("activate", self._on_filter_cleared)
        group.add_action(clear)
        button.insert_action_group("filter", group)
        button.set_create_popup_func(self._rebuild_filter_menu)
        self.filter_button = button

    def _rebuild_filter_menu(self, button: Gtk.MenuButton) -> None:
        """Build the menu from the folder's metadata on every open."""
        menu = Gio.Menu()
        axes = (
            ("Camera", "model", self.known_models()),
            ("Lens", "lens", self.known_lenses()),
        )
        for title, axis, values in axes:
            section = Gio.Menu()
            for label, value in [("All", ""), *((v, v) for v in values)]:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(
                    f"filter.{axis}", GLib.Variant.new_string(value)
                )
                section.append_item(item)
            menu.append_section(title, section)
        slider = self._build_focal_sliders()
        if slider is not None:
            section = Gio.Menu()
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value(
                "custom", GLib.Variant.new_string("focal")
            )
            section.append_item(item)
            menu.append_section("Focal length", section)
        footer = Gio.Menu()
        footer.append("Clear filter", "filter.clear")
        menu.append_section(None, footer)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        if slider is not None:
            popover.add_child(slider, "focal")
        button.set_popover(popover)

    def _build_focal_sliders(self) -> Gtk.Widget | None:
        """The from/to focal-length sliders, snapping to folder values."""
        focals = [
            f for f in self.known_focals() if catalog.focal_mm(f) is not None
        ]
        if len(focals) < _MIN_SLIDER_STOPS:
            return None
        top = len(focals) - 1
        lo_idx, hi_idx = self._focal_indices(focals)
        grid = Gtk.Grid(column_spacing=8, row_spacing=2)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        grid.set_margin_top(4)
        grid.set_margin_bottom(4)
        summary = Gtk.Label(xalign=0)
        summary.add_css_class("dim-label")
        grid.attach(summary, 0, 0, 2, 1)
        scales: list[Gtk.Scale] = []
        for row, (label, index) in enumerate(
            (("From", lo_idx), ("To", hi_idx)), start=1
        ):
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0, top, 1
            )
            scale.set_round_digits(0)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_size_request(180, -1)
            scale.set_value(index)
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            scales.append(scale)
        low, high = scales
        syncing = [False]

        def refresh(source: Gtk.Scale) -> None:
            if syncing[0]:
                return
            lo = round(low.get_value())
            hi = round(high.get_value())
            if lo > hi:
                syncing[0] = True
                if source is low:
                    high.set_value(lo)
                    hi = lo
                else:
                    low.set_value(hi)
                    lo = hi
                syncing[0] = False
            summary.set_text(f"{focals[lo]}  to  {focals[hi]}")
            wanted = None
            if (lo, hi) != (0, top):
                mm_lo = catalog.focal_mm(focals[lo])
                mm_hi = catalog.focal_mm(focals[hi])
                if mm_lo is not None and mm_hi is not None:
                    wanted = (mm_lo, mm_hi)
            if wanted != self._filter_focal:
                self._apply_focal_later(wanted)

        low.connect("value-changed", refresh)
        high.connect("value-changed", refresh)
        summary.set_text(f"{focals[lo_idx]}  to  {focals[hi_idx]}")
        return grid

    def _apply_focal_later(self, focal: tuple[float, float] | None) -> None:
        """Apply a focal range once the slider stops moving."""
        self._focal_wanted = focal
        if self._focal_pending:
            GLib.source_remove(self._focal_pending)
        self._focal_pending = GLib.timeout_add(
            _FOCAL_SETTLE_MS, self._apply_focal_now
        )

    def _apply_focal_now(self) -> bool:
        """Hand the settled focal range to the filter."""
        self._focal_pending = 0
        self.set_filter(
            model=self._filter_model,
            lens=self._filter_lens,
            focal=self._focal_wanted,
        )
        return GLib.SOURCE_REMOVE

    def _focal_indices(self, focals: list[str]) -> tuple[int, int]:
        """The slider positions matching the active focal filter."""
        top = len(focals) - 1
        if self._filter_focal is None:
            return 0, top
        lo_mm, hi_mm = self._filter_focal
        values = [catalog.focal_mm(f) or 0.0 for f in focals]
        lo = next((i for i, v in enumerate(values) if v >= lo_mm), 0)
        hi = next((i for i in range(top, -1, -1) if values[i] <= hi_mm), top)
        return lo, max(lo, hi)

    def _on_filter_action(
        self, action: Gio.SimpleAction, value: GLib.Variant, _axis: str
    ) -> None:
        """Apply a radio pick from the filter menu."""
        action.set_state(value)
        self.set_filter(
            model=self._filter_actions["model"].get_state().get_string(),
            lens=self._filter_actions["lens"].get_state().get_string(),
            focal=self._filter_focal,
        )

    def _on_filter_cleared(self, *_args: object) -> None:
        """Reset every filter axis."""
        self.set_filter(model=None, lens=None, focal=None)

    def _init_file_actions(self) -> None:
        """Install the context-menu action group for file operations."""
        group = Gio.SimpleActionGroup()
        for kind in ("open-with", "export", "copy", "move", "trash"):
            action = Gio.SimpleAction.new(kind, None)
            action.connect("activate", partial(self._on_menu_action, kind))
            group.add_action(action)
        self.insert_action_group("fileops", group)

    def _on_menu_action(self, kind: str, *_args: object) -> None:
        """Forward a context-menu choice with its captured paths."""
        if self._on_file_action is None:
            return
        if kind == "open-with":
            if self._menu_click_path is not None:
                self._on_file_action(kind, [self._menu_click_path])
            return
        if self._menu_paths:
            self._on_file_action(kind, self._menu_paths)

    def _card_paths(self, path: str) -> list[str]:
        """The paths a card action applies to: the selection or itself."""
        if path in self._selected:
            return [p for p in self._paths if p in self._selected]
        return [path]

    def _on_tile_menu(
        self,
        gesture: Gtk.GestureClick,
        _n: int,
        x: float,
        y: float,
        button: Gtk.Button,
    ) -> None:
        """Open the file-operations menu for the right-clicked card."""
        path = self._path_of(button)
        if path is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._menu_paths = self._card_paths(path)
        self._menu_click_path = path
        count = len(self._menu_paths)
        suffix = f" ({count})" if count > 1 else ""
        menu = Gio.Menu()
        menu.append("Open With…", "fileops.open-with")
        menu.append(f"Export{suffix}…", "fileops.export")
        menu.append(f"Copy to…{suffix}", "fileops.copy")
        menu.append(f"Move to…{suffix}", "fileops.move")
        menu.append(f"Move to Trash{suffix}", "fileops.trash")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(button)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect(
            "closed", lambda p: mainloop.call(self._drop_popover, p)
        )
        popover.popup()

    @staticmethod
    def _drop_popover(popover: Gtk.PopoverMenu) -> bool:
        """Unparent a dismissed context menu so it can be collected."""
        popover.unparent()
        return GLib.SOURCE_REMOVE

    def _on_tile_drag_prepare(
        self, source: Gtk.DragSource, _x: float, _y: float, button: Gtk.Button
    ) -> Any:
        """Provide the dragged card's paths."""
        path = self._path_of(button)
        if path is None:
            return None
        state = source.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            action = Gdk.DragAction.COPY
        elif state & Gdk.ModifierType.SHIFT_MASK:
            action = Gdk.DragAction.MOVE
        elif self._drag_action is not None and self._drag_action() == "copy":
            action = Gdk.DragAction.COPY
        else:
            action = Gdk.DragAction.MOVE
        source.set_actions(action)
        return Gdk.ContentProvider.new_for_value(
            "\n".join(self._card_paths(path))
        )

    def _on_tile_drag_begin(
        self,
        source: Gtk.DragSource,
        _drag: Any,
        button: Gtk.Button,
    ) -> None:
        """Use the dragged card as the drag icon, badged with the count.

        A drag from a marked card carries every marked image, so a
        multi-image drag shows how many are coming along.
        """
        path = self._path_of(button)
        if path is None:
            return
        paintable: Gdk.Paintable = Gtk.WidgetPaintable.new(button)
        count = len(self._card_paths(path))
        if count > 1:
            paintable = _badged_paintable(paintable, button, count)
        source.set_icon(paintable, 0, 0)

    def scan(self, folder: str) -> None:
        """Populate the strip with the RAF files in folder, and watch it.

        The strip re-scans itself automatically (debounced) when the
        folder's RAF files change. Re-scanning the same folder keeps
        the current image selected and in view.
        """
        self._scan_id += 1
        scan_id = self._scan_id
        keep = None
        if folder == self._folder and 0 <= self._current < len(self._paths):
            keep = self._paths[self._current]
        self._tiles.clear()
        if folder != self._folder:
            self._select_mode = False
            self.clear_selection()
            self.set_filter(model=None, lens=None, focal=None)
            self._folder = folder
            self._watch(folder)

        entries = self._model.scan(folder)
        self._paths = [entry.path for entry in entries]
        self._current = -1
        self._tiles.prefetch(self._paths, max(0, self._current))

        if self._selected:
            self._selected &= set(self._paths)
            self._apply_selection_style()
            self._notify_selection()

        if keep is not None and keep in self._paths:
            # Restore the selection once the new cards have a layout
            mainloop.call(partial(self._restore_current, scan_id, keep))

        if entries and self._on_loading is not None:
            self._on_loading(True)

    def _on_setup(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Build one reusable card: camera on top, name at the bottom."""
        picture = Gtk.Picture()
        picture.set_size_request(
            int(self._thumb_height * 1.5), self._thumb_height
        )

        def caption(text: str) -> Gtk.Label:
            label = Gtk.Label(label=text, halign=Gtk.Align.FILL)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            # Keep the label's natural width small so the card's width is
            # driven by the thumbnail, not by a long filename.
            label.set_max_width_chars(8)
            label.add_css_class("caption")
            label.add_css_class("dim-label")
            return label

        camera_label = caption("")
        name_label = caption("")
        badges = self._build_badges()
        thumb = Gtk.Overlay(child=picture)
        thumb.add_overlay(badges["box"])
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        card.set_margin_top(2)
        card.set_margin_bottom(2)
        card.set_margin_start(3)
        card.set_margin_end(3)
        card.append(camera_label)
        card.append(thumb)
        card.append(name_label)

        button = Gtk.Box()
        button.append(card)
        button.add_css_class("card")
        button.add_css_class("thumb")
        modifier_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        modifier_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        modifier_click.connect("pressed", self._on_tile_pressed, button)
        button.add_controller(modifier_click)
        if self._on_file_action is not None:
            menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu_click.connect("pressed", self._on_tile_menu, button)
            button.add_controller(menu_click)
            drag = Gtk.DragSource(
                actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY
            )
            drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("prepare", self._on_tile_drag_prepare, button)
            drag.connect("drag-begin", self._on_tile_drag_begin, button)
            button.add_controller(drag)
        self._cards[button] = {
            "picture": picture,
            "camera": camera_label,
            "name": name_label,
            **badges,
        }
        item.set_child(button)

    def _on_bind(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Fill a card with one entry."""
        button = item.get_child()
        entry_item = item.get_item()
        if button is None or entry_item is None:
            return
        entry = entry_item.entry
        parts = self._cards[button]
        self._card_path[button] = entry.path
        # A known shape sizes the card up front, so it does not jump
        # from the placeholder when the thumbnail lands.
        width = (
            round(self._thumb_height * entry.aspect)
            if entry.aspect
            else int(self._thumb_height * 1.5)
        )
        parts["picture"].set_size_request(width, self._thumb_height)
        parts["name"].set_text(Path(entry.path).stem)
        parts["camera"].set_text(entry.model)
        parts["crop"].set_visible(entry.has_crop)
        parts["ev"].set_visible(entry.has_ev)
        button.set_tooltip_text(Path(entry.path).name)
        self._style_card(button, entry.path)
        self._tiles.want(button, parts["picture"], entry.path)

    def _on_unbind(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Let go of a recycled card."""
        button = item.get_child()
        if button is None:
            return
        self._tiles.forget(button)
        self._card_path.pop(button, None)

    def _style_card(self, button: Gtk.Widget, path: str) -> None:
        """Mark a card as the current one or as batch-selected."""
        current = self.current_path
        for style, wanted in (
            ("thumb-selected", path == current),
            ("thumb-marked", path in self._selected),
        ):
            if wanted:
                button.add_css_class(style)
            else:
                button.remove_css_class(style)

    def _note_meta(
        self, path: str, meta: ThumbMeta, width: int, height: int
    ) -> None:
        """Fold a decoded frame's metadata into the shared folder."""
        aspect = width / height if height > 0 else None
        self._model.fold_meta(path, meta, aspect)
        if not self._tiles.busy and self._on_loading is not None:
            self._on_loading(False)

    def _on_model_event(self, reason: str, path: str | None) -> None:
        """Follow the shared folder."""
        if reason == "filter":
            self._apply_filter()
            if self._on_filter_changed is not None:
                self._on_filter_changed()
            return
        if reason == "entry" and path is not None:
            entry = self._model.entry_for(path)
            for button, shown in self._card_path.items():
                if shown == path and entry is not None:
                    self._cards[button]["camera"].set_text(entry.model)

    def _item_for(self, path: str) -> EntryItem | None:
        """The list item holding one frame, if the folder still has it."""
        return self._model.item_for(path)

    def _path_of(self, button: Gtk.Widget) -> str | None:
        """Which frame a card currently shows."""
        return self._card_path.get(button)

    @property
    def paths(self) -> list[str]:
        """The RAF paths currently shown, in display order."""
        return list(self._paths)

    @property
    def entries(self) -> list[catalog.Entry]:
        """The folder's entries in scan order."""
        return [
            self._entries[path]
            for path in self._paths
            if path in self._entries
        ]

    @property
    def _entries(self) -> dict[str, catalog.Entry]:
        """The shared folder's entries, by path."""
        return self._model.entries

    @_entries.setter
    def _entries(self, entries: dict[str, catalog.Entry]) -> None:
        """Replace the shared folder's entries wholesale."""
        self._model.entries = entries

    @property
    def active_filter(self) -> catalog.Filter:
        """What the folder is narrowed down to right now."""
        return self._filter()

    @property
    def current_path(self) -> str | None:
        """The selected RAF, or None while nothing is selected."""
        if 0 <= self._current < len(self._paths):
            return self._paths[self._current]
        return None

    def _clear(self) -> None:
        """Drop every thumbnail currently in the strip."""
        self._tiles.clear()
        self._cards.clear()
        self._card_path.clear()

    def _build_badges(self) -> dict[str, Any]:
        """The per-card edit badges, bottom right."""

        def badge(icon: str, tooltip: str) -> Gtk.Image:
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(10)
            image.set_opacity(0.75)
            image.set_tooltip_text(tooltip)
            return image

        badges = {
            "crop": badge("grawji-crop-symbolic", "Crop/rotate applied"),
            "ev": badge("grawji-ev-symbolic", "Exposure adjusted"),
        }
        box = Gtk.Box(spacing=3)
        box.set_halign(Gtk.Align.END)
        box.set_valign(Gtk.Align.END)
        box.set_margin_end(4)
        box.set_margin_bottom(4)
        box.append(badges["ev"])
        box.append(badges["crop"])
        badges["box"] = box
        return badges

    def refresh_badges(self, path: str) -> None:
        """Re-read path's sidecar and update its edit badges."""
        entry = self._entries.get(path)
        if entry is None:
            return
        has_crop, has_ev = edit_flags(path)
        self._entries[path] = catalog.with_edits(entry, has_crop, has_ev)
        for position in range(self._store.get_n_items()):
            item = self._store.get_item(position)
            if item is not None and item.entry.path == path:
                item.entry = self._entries[path]
                break
        for button, shown in self._card_path.items():
            if shown == path:
                self._cards[button]["crop"].set_visible(has_crop)
                self._cards[button]["ev"].set_visible(has_ev)

    def _restore_current(self, scan_id: int, path: str) -> bool:
        """Re-select path after a same-folder re-scan (on idle)."""
        if scan_id == self._scan_id and path in self._paths:
            self._set_current(self._paths.index(path), center=True)
        return GLib.SOURCE_REMOVE

    def _set_current(self, index: int, *, center: bool = False) -> None:
        """Mark index as the current frame and bring it into view."""
        self._current = index
        path = self.current_path
        for button in self._card_path:
            self._style_card(button, self._card_path[button])
        if path is None:
            return
        if self.get_width() <= 0:
            self._reveal_wanted = (path, center)
            return
        self._reveal(path, center=center)

    def _reveal(self, path: str, *, center: bool) -> None:
        """Bring one frame into view, centring it when asked."""
        position = self._shown_position(path)
        if position is None:
            return
        if not self._is_near(position):
            self._jump_near(position)
        self._view.scroll_to(position, Gtk.ListScrollFlags.NONE, None)
        if center:
            self._center_on(path)

    def _is_near(self, position: int) -> bool:
        """Whether a position is within a page of what the strip shows."""
        count = self._shown.get_n_items()
        adj = self.get_hadjustment()
        per_card = adj.get_upper() / count if count else 0
        if per_card <= 0:
            return False
        first = adj.get_value() / per_card
        page = adj.get_page_size() / per_card
        return first - page <= position <= first + 2 * page

    def _jump_near(self, position: int) -> None:
        """Scroll roughly to a position, from the list's own estimate."""
        count = self._shown.get_n_items()
        if count <= 0:
            return
        adj = self.get_hadjustment()
        middle = adj.get_upper() * (position + 0.5) / count
        adj.set_value(self._clamped(middle - adj.get_page_size() / 2))

    def _catch_up_reveal(self) -> bool:
        """Catch up on a selection made before the strip had a size."""
        wanted = self._reveal_wanted
        self._reveal_wanted = None
        if wanted is not None:
            self._reveal(wanted[0], center=wanted[1])
        return GLib.SOURCE_REMOVE

    def _cancel_center(self) -> None:
        """Drop a pending centring, so scrolling by hand wins."""
        self._center_path = None
        self._center_frames = 0

    def _center_on(self, path: str) -> None:
        """Put path's card in the middle of the strip.

        A running glide is stopped, or it would fight the centring.
        """
        self.stop_glide()
        self._center_path = path
        self._center_frames = _CENTER_FRAMES
        if self._center_tick:
            return
        self._center_tick = self.add_tick_callback(self._on_center_tick)

    def _on_center_tick(self, _widget: Any, _clock: Any) -> bool:
        """Centre the pending card once it has a place on screen."""
        self._center_frames -= 1
        adj = self.get_hadjustment()
        page = adj.get_page_size()
        for button, shown in self._card_path.items():
            if shown != self._center_path or button.get_width() <= 0:
                continue
            found, rect = button.compute_bounds(self)
            if not found:
                continue
            middle = adj.get_value() + rect.origin.x + rect.size.width / 2
            adj.set_value(self._clamped(middle - page / 2))
            self._center_tick = 0
            return GLib.SOURCE_REMOVE
        if self._center_frames > 0:
            return GLib.SOURCE_CONTINUE
        self._center_tick = 0
        return GLib.SOURCE_REMOVE

    def _shown_position(self, path: str) -> int | None:
        """Where path sits in the filtered model, if the filter keeps it."""
        for position in range(self._shown.get_n_items()):
            item = self._shown.get_item(position)
            if item is not None and item.entry.path == path:
                return position
        return None

    def _on_row_activated(self, _view: Gtk.ListView, position: int) -> None:
        """Handle a click on one card."""
        item = self._shown.get_item(position)
        if item is None:
            return
        self._activate(item.entry.path)

    def _activate(self, path: str) -> None:
        """Open a frame, or mark it when batch-selecting."""
        pending = self._pending_mods
        self._pending_mods = None
        if pending is not None and pending[0] == path:
            _, ctrl, shift = pending
            if shift:
                self._select_range_to(path)
            elif ctrl:
                self._toggle_selected(path)
            return
        if self._select_mode:
            self._toggle_selected(path)
            return
        self.clear_selection()
        if path in self._paths:
            self._set_current(self._paths.index(path))
        self._on_select(path)

    def clear_selection(self) -> None:
        """Unmark every card (the open-image highlight is kept)."""
        if not self._selected and self._anchor is None:
            return
        self._selected.clear()
        self._anchor = None
        self._apply_selection_style()
        self._notify_selection()

    def _on_tile_pressed(
        self,
        gesture: Gtk.GestureClick,
        _n: int,
        _x: float,
        _y: float,
        button: Gtk.Button,
    ) -> None:
        """Record the modifiers held as a card press begins."""
        path = self._path_of(button)
        if path is None:
            return
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self._pending_mods = (path, ctrl, shift) if ctrl or shift else None

    def _select_range_to(self, path: str) -> None:
        """Select every card from the anchor through path."""
        if path not in self._paths:
            return
        end = self._paths.index(path)
        if self._anchor is not None and self._anchor in self._paths:
            start = self._paths.index(self._anchor)
        elif 0 <= self._current < len(self._paths):
            start = self._current
        else:
            start = end
        low, high = min(start, end), max(start, end)
        self._selected.update(self._paths[low : high + 1])
        self._anchor = path
        self._apply_selection_style()
        self._notify_selection()

    def enter_select_mode(self) -> None:
        """Begin batch-select: clicks toggle export selection.

        The open-image highlight is hidden for the duration so the only
        raised cards are the selected ones.
        """
        self._select_mode = True
        self._selected.clear()
        self._anchor = None
        self._apply_selection_style()
        self._notify_selection()

    def exit_select_mode(self) -> None:
        """Leave batch-select mode and clear the selection.

        Restores the open image's highlight, hidden while selecting.
        """
        self._select_mode = False
        self._selected.clear()
        self._anchor = None
        self._apply_selection_style()
        self._notify_selection()

    @property
    def in_select_mode(self) -> bool:
        """Whether batch-select mode is active."""
        return self._select_mode

    @property
    def selected_paths(self) -> list[str]:
        """The selected RAF paths, in display order."""
        return [p for p in self._paths if p in self._selected]

    def select_all(self) -> None:
        """Select every visible thumbnail (batch-select mode only)."""
        if not self._select_mode:
            return
        self._selected = {self._paths[i] for i in self._visible_indices()}
        self._apply_selection_style()
        self._notify_selection()

    def _toggle_selected(self, path: str) -> None:
        """Add or remove one thumbnail from the export selection."""
        if path not in self._paths:
            return
        if path in self._selected:
            self._selected.discard(path)
        else:
            self._selected.add(path)
        self._anchor = path
        self._apply_selection_style()
        self._notify_selection()

    def _apply_selection_style(self) -> None:
        """Repaint every bound card from the current selection."""
        for button, path in self._card_path.items():
            self._style_card(button, path)

    def _notify_selection(self) -> None:
        """Report the current selection size to the listener."""
        if self._on_selection_changed is not None:
            self._on_selection_changed(len(self._selected))

    def set_filter(
        self,
        *,
        model: str | None,
        lens: str | None,
        focal: tuple[float, float] | None,
    ) -> None:
        """Show only cards matching the active filter axes."""
        self._filter_model = model or None
        self._filter_lens = lens or None
        self._filter_focal = focal
        self._model.set_filter(self._filter())
        states = (("model", self._filter_model), ("lens", self._filter_lens))
        for axis, value in states:
            action = self._filter_actions.get(axis)
            if action is not None:
                action.set_state(GLib.Variant.new_string(value or ""))
        if self.filter_button is not None:
            active = self._filter().is_active
            if active:
                self.filter_button.add_css_class("accent")
            else:
                self.filter_button.remove_css_class("accent")

    def known_models(self) -> list[str]:
        """Camera models present in the folder, sorted."""
        return self._model.known_models()

    def known_lenses(self) -> list[str]:
        """Lens models present in the folder, sorted."""
        return self._model.known_lenses()

    def known_focals(self) -> list[str]:
        """Focal lengths present in the folder, sorted numerically."""
        return self._model.known_focals()

    def _filter(self) -> catalog.Filter:
        """The active filter, as the model states it."""
        return catalog.Filter(
            camera=self._filter_model,
            lens=self._filter_lens,
            focal=self._filter_focal,
        )

    def texture_for(self, path: str) -> Any | None:
        """The small frame the strip has decoded for a path."""
        return self._tiles.texture_for(path)

    def entry_for(self, path: str) -> catalog.Entry | None:
        """The folder entry behind a path."""
        return self._model.entry_for(path)

    def _matches_filter(self, path: str) -> bool:
        """Whether a card passes the active filter."""
        entry = self._model.entry_for(path)
        return entry is None or self._model.passes(entry)

    def _passes(self, item: EntryItem) -> bool:
        """Whether one entry survives the active filter."""
        return self._model.passes(item.entry)

    def _apply_filter(self) -> None:
        """Re-run the filter over the model, dropping hidden marks."""
        self._model_filter.changed(Gtk.FilterChange.DIFFERENT)
        visible = set(self.visible_paths)
        hidden_marks = self._selected - visible
        if hidden_marks:
            self._selected &= visible
            self._apply_selection_style()
            self._notify_selection()

    @property
    def visible_paths(self) -> list[str]:
        """The frames the filter currently leaves, in display order."""
        paths = []
        for position in range(self._shown.get_n_items()):
            item = self._shown.get_item(position)
            if item is not None:
                paths.append(item.entry.path)
        return paths

    def _visible_indices(self) -> list[int]:
        """Indices of the cards the filter currently shows."""
        return [
            i
            for i, path in enumerate(self._paths)
            if self._matches_filter(path)
        ]

    def scroll_step(self, direction: int) -> None:
        """Scroll the strip by one thumbnail card, keeping the selection.

        direction is -1 for left, +1 for right.
        """
        self.stop_glide()
        self._cancel_center()
        self._scroll_by(self._card_width() * direction)

    def set_glide_speed(self, px_per_second: float) -> None:
        """Set the hold-to-scroll speed (user preference)."""
        self._glide_speed = max(1.0, px_per_second)

    def start_glide(self, direction: int) -> None:
        """Scroll continuously (frame-synced) until stop_glide is called."""
        self._glide_dir = direction
        self._cancel_center()
        if self._glide_tick is None:
            self._glide_last = None
            self._glide_tick = self.add_tick_callback(self._on_glide_tick)

    def stop_glide(self) -> None:
        """Stop a continuous scroll started by start_glide (idempotent)."""
        if self._glide_tick is not None:
            self.remove_tick_callback(self._glide_tick)
            self._glide_tick = None

    def _on_glide_tick(self, _widget: Any, clock: Any) -> bool:
        """Advance the glide by the elapsed frame time."""
        now = clock.get_frame_time()  # microseconds
        if self._glide_last is not None:
            elapsed = (now - self._glide_last) / 1e6
            step = self._glide_speed * elapsed * self._glide_dir
            if self._glide_dir > 0:
                step *= _GLIDE_AHEAD_BOOST
            self._scroll_by(step)
        self._glide_last = now
        return GLib.SOURCE_CONTINUE

    def _clamped(self, value: float) -> float:
        """A scroll position inside the strip's range."""
        adj = self.get_hadjustment()
        top = adj.get_upper() - adj.get_page_size()
        return max(adj.get_lower(), min(top, value))

    def _scroll_by(self, delta: float) -> None:
        """Move the horizontal scroll position by delta, clamped."""
        adj = self.get_hadjustment()
        top = adj.get_upper() - adj.get_page_size()
        adj.set_value(max(adj.get_lower(), min(top, adj.get_value() + delta)))

    def _on_scroll(self, _controller: Any, dx: float, dy: float) -> bool:
        """Pan the strip sideways from a plain wheel or trackpad swipe."""
        delta = dx or dy
        if delta:
            self._cancel_center()
            self._scroll_by(delta * self._card_width())
        return True

    def _card_width(self) -> float:
        """How far one step moves: one landscape card."""
        return self._thumb_height * 1.5 + 6  # + list spacing

    def select_path(self, path: str, *, notify: bool = True) -> bool:
        """Select the thumbnail for path."""
        if path not in self._paths:
            return False
        self._set_current(self._paths.index(path), center=True)
        if notify:
            self._on_select(path)
        return True

    def _watch(self, folder: str) -> None:
        """Re-scan automatically when the folder's contents change."""
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        try:
            monitor = Gio.File.new_for_path(folder).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error:
            return
        monitor.connect("changed", self._on_folder_changed)
        self._monitor = monitor

    def _on_folder_changed(
        self, _monitor: Any, file: Any, other: Any, _event: Any
    ) -> None:
        """Debounce a re-scan when RAF files appear, vanish or move."""
        if not (_is_raf(file) or _is_raf(other)):
            return
        if self._reload_pending_id:
            GLib.source_remove(self._reload_pending_id)
        self._reload_pending_id = GLib.timeout_add(
            _RELOAD_DEBOUNCE_MS, self._reload_now
        )

    def _reload_now(self) -> bool:
        """Re-scan the current folder (picks up added/removed files)."""
        self._reload_pending_id = 0
        if self._folder is not None:
            self.scan(self._folder)
        return GLib.SOURCE_REMOVE
