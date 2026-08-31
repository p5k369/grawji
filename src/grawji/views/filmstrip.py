"""Bottom filmstrip of RAF thumbnails."""

from __future__ import annotations

import math
import os
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

from grawji.imaging.render import texture_for_pixbuf
from grawji.imaging.thumbnails import ThumbnailLoader
from grawji.settings import cache_dir
from grawji.sidecar import sidecar_path

# Default continuous-scroll speed while a nav arrow is held, in px/second.
_GLIDE_PX_PER_S_DEFAULT = 600

# Folder-change events settle for this long before the strip re-scans.
_RELOAD_DEBOUNCE_MS = 500

Dispatch = Callable[[Callable[[], None]], Any]


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
        on_selection_changed: Callable[[int], None] | None = None,
        on_file_action: Callable[[str, list[str]], None] | None = None,
        drag_action: Callable[[], str] | None = None,
        dispatch: Dispatch = GLib.idle_add,
        thumb_height: int = 110,
    ) -> None:
        """Create the filmstrip.

        Args:
            on_select: Called with the RAF path when a thumbnail is
                clicked in normal mode (to preview/edit it).
            on_loading: Called with True when thumbnail decoding starts and
                False when it finishes, for an activity indicator elsewhere.
            on_selection_changed: Called with the number of selected
                thumbnails while in batch-select mode.
            on_file_action: Called with ("export"/"copy"/"move"/"trash",
                paths) from a card's context menu.
            drag_action: Returns the configured default drag action
                ("move" or "copy") for an unmodified drag.
            dispatch: Schedules a callback on the GTK main loop.
            thumb_height: Thumbnail height in pixels.
        """
        super().__init__()
        self._on_select = on_select
        self._on_loading = on_loading
        self._on_selection_changed = on_selection_changed
        self._on_file_action = on_file_action
        self._drag_action = drag_action
        self._menu_paths: list[str] = []
        self._init_file_actions()
        self._dispatch = dispatch
        self._thumb_height = thumb_height
        self._scan_id = 0
        self._paths: list[str] = []
        self._buttons: list[Gtk.Button] = []
        self._badges: dict[str, Gtk.Image] = {}
        self._center_pending: Gtk.Button | None = None
        self._recenter_id = 0
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
        self._folder: str | None = None
        self._monitor: Any = None
        self._reload_pending_id = 0
        self._thumbs = ThumbnailLoader(
            height=thumb_height,
            cache_dir=cache_dir() / "thumbs",
            workers=max(1, (os.cpu_count() or 2) - 1),
            dispatch=dispatch,
            is_stale=lambda scan_id: scan_id != self._scan_id,
            on_thumb=self._apply_thumb,
            on_finished=self._loading_done,
        )

        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._box.set_margin_start(4)
        self._box.set_margin_end(4)
        self._box.set_margin_top(4)
        self._box.set_margin_bottom(4)
        self.set_child(self._box)
        self.set_min_content_height(thumb_height + 52)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        self.get_hadjustment().connect("changed", self._on_range_changed)

    def _init_file_actions(self) -> None:
        """Install the context-menu action group for file operations."""
        group = Gio.SimpleActionGroup()
        for kind in ("export", "copy", "move", "trash"):
            action = Gio.SimpleAction.new(kind, None)
            action.connect("activate", partial(self._on_menu_action, kind))
            group.add_action(action)
        self.insert_action_group("fileops", group)

    def _on_menu_action(self, kind: str, *_args: object) -> None:
        """Forward a context-menu choice with its captured paths."""
        if self._on_file_action is not None and self._menu_paths:
            self._on_file_action(kind, self._menu_paths)

    def _card_paths(self, path: str) -> list[str]:
        """The paths a card action applies to: the selection or itself."""
        if path in self._selected:
            return [p for p in self._paths if p in self._selected]
        return [path]

    def _on_card_menu(
        self,
        path: str,
        button: Gtk.Button,
        gesture: Gtk.GestureClick,
        _n: int,
        x: float,
        y: float,
    ) -> None:
        """Open the file-operations menu for the right-clicked card."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._menu_paths = self._card_paths(path)
        count = len(self._menu_paths)
        suffix = f" ({count})" if count > 1 else ""
        menu = Gio.Menu()
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
            "closed", lambda p: GLib.idle_add(self._drop_popover, p)
        )
        popover.popup()

    @staticmethod
    def _drop_popover(popover: Gtk.PopoverMenu) -> bool:
        """Unparent a dismissed context menu so it can be collected."""
        popover.unparent()
        return GLib.SOURCE_REMOVE

    def _on_drag_prepare(
        self, source: Gtk.DragSource, _x: float, _y: float, path: str
    ) -> Any:
        """Provide the dragged card's paths."""
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

    def _on_drag_begin(
        self,
        source: Gtk.DragSource,
        _drag: Any,
        path: str,
        button: Gtk.Button,
    ) -> None:
        """Use the dragged card as the drag icon, badged with the count.

        A drag from a marked card carries every marked image, so a
        multi-image drag shows how many are coming along.
        """
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
        self._clear()
        if folder != self._folder:
            # A new folder invalidates any in-progress selection.
            self._select_mode = False
            self.clear_selection()
            self._folder = folder
            self._watch(folder)

        base = Path(folder)
        paths = sorted(
            {p for pat in ("*.RAF", "*.raf") for p in base.glob(pat)}
        )
        self._paths = [str(p) for p in paths]
        self._buttons = []
        self._current = -1
        cards = []
        for path in paths:
            picture, camera_label, button = self._build_card(path)
            button.connect("clicked", partial(self._on_clicked, str(path)))
            self._box.append(button)
            self._buttons.append(button)
            cards.append((str(path), picture, camera_label))

        if self._selected:
            self._selected &= set(self._paths)
            self._apply_selection_style()
            self._notify_selection()

        if keep is not None and keep in self._paths:
            # Restore the selection once the new cards have a layout
            GLib.idle_add(partial(self._restore_current, scan_id, keep))

        if cards:
            if self._on_loading is not None:
                self._on_loading(True)
            self._thumbs.load(cards, scan_id)

    def _build_card(self, path: Path) -> tuple[Gtk.Picture, Gtk.Label, Any]:
        """Build one thumbnail card: camera on top, name at the bottom."""
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
        name_label = caption(path.stem)
        badge = Gtk.Image.new_from_icon_name("grawji-crop-symbolic")
        badge.set_pixel_size(10)
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.END)
        badge.set_margin_end(4)
        badge.set_margin_bottom(4)
        badge.set_opacity(0.75)
        badge.set_tooltip_text("Crop/rotate applied")
        badge.set_visible(sidecar_path(path).exists())
        self._badges[str(path)] = badge
        thumb = Gtk.Overlay(child=picture)
        thumb.add_overlay(badge)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        card.set_margin_top(2)
        card.set_margin_bottom(2)
        card.append(camera_label)
        card.append(thumb)
        card.append(name_label)

        button = Gtk.Button(child=card)
        button.add_css_class("card")
        button.add_css_class("thumb")
        button.set_tooltip_text(path.name)
        modifier_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        modifier_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        modifier_click.connect(
            "pressed", partial(self._on_card_pressed, str(path))
        )
        button.add_controller(modifier_click)
        if self._on_file_action is not None:
            menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu_click.connect(
                "pressed", partial(self._on_card_menu, str(path), button)
            )
            button.add_controller(menu_click)
            drag = Gtk.DragSource(
                actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY
            )
            drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("prepare", self._on_drag_prepare, str(path))
            drag.connect("drag-begin", self._on_drag_begin, str(path), button)
            button.add_controller(drag)
        return picture, camera_label, button

    @property
    def paths(self) -> list[str]:
        """The RAF paths currently shown, in display order."""
        return list(self._paths)

    @property
    def current_index(self) -> int:
        """Index of the selected thumbnail, or -1 if none is selected."""
        return self._current

    def _clear(self) -> None:
        """Remove all thumbnails currently in the strip."""
        child = self._box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._box.remove(child)
            child = nxt
        self._buttons = []
        self._badges = {}
        self._center_pending = None

    def set_altered(self, path: str, altered: bool) -> None:
        """Show or hide the geometry badge on path's card."""
        badge = self._badges.get(path)
        if badge is not None:
            badge.set_visible(altered)

    def _restore_current(self, scan_id: int, path: str) -> bool:
        """Re-select path after a same-folder re-scan (on idle)."""
        if scan_id == self._scan_id and path in self._paths:
            self._set_current(self._paths.index(path), center=True)
        return GLib.SOURCE_REMOVE

    def _set_current(self, index: int, *, center: bool = False) -> None:
        """Mark index as selected and update the highlight."""
        for pos, button in enumerate(self._buttons):
            if pos == index:
                button.add_css_class("thumb-selected")
            else:
                button.remove_css_class("thumb-selected")
        self._current = index
        self._center_pending = None
        if 0 <= index < len(self._buttons):
            button = self._buttons[index]
            if center:
                # Card widths settle while thumbnails load; re-center
                # on the final layout once this scan finishes.
                self._center_pending = button
            self._scroll_into_view(button, center=center)

    def _scroll_into_view(
        self, button: Gtk.Button, retries: int = 20, *, center: bool = False
    ) -> None:
        """Scroll the strip horizontally so button is visible."""
        adj = self.get_hadjustment()
        ok, rect = button.compute_bounds(self._box)
        if not ok or rect.size.width <= 0 or adj.get_page_size() <= 0:
            if retries > 0:
                GLib.timeout_add(
                    50, self._retry_scroll, button, retries - 1, center
                )
            return
        left, right = rect.origin.x, rect.origin.x + rect.size.width
        page = adj.get_page_size()
        value = adj.get_value()
        if center:
            adj.set_value(left - (page - rect.size.width) / 2)
        elif left < value:
            adj.set_value(left)
        elif right > value + page:
            adj.set_value(right - page)

    def _retry_scroll(
        self, button: Gtk.Button, retries: int, center: bool
    ) -> bool:
        """Re-attempt the scroll once the widget got its layout."""
        if button.get_parent() is not None:  # card still in the strip
            self._scroll_into_view(button, retries, center=center)
        return GLib.SOURCE_REMOVE

    def _on_range_changed(self, _adj: Any) -> None:
        """Keep a pending restored selection centered through layout."""
        if self._center_pending is None or self._recenter_id:
            return
        self._recenter_id = GLib.timeout_add(10, self._recenter_pending)

    def _recenter_pending(self) -> bool:
        """Re-center the pending selection after a layout pass."""
        self._recenter_id = 0
        button = self._center_pending
        if button is not None and button.get_parent() is not None:
            self._scroll_into_view(button, 0, center=True)
        return GLib.SOURCE_REMOVE

    def _on_clicked(self, path: str, _button: Gtk.Button) -> None:
        """Handle a thumbnail click."""
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

    def _on_card_pressed(
        self,
        path: str,
        gesture: Gtk.GestureClick,
        _n: int,
        _x: float,
        _y: float,
    ) -> None:
        """Record the modifiers held as a card press begins."""
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
        for button in self._buttons:
            button.remove_css_class("thumb-selected")
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
        if 0 <= self._current < len(self._buttons):
            self._buttons[self._current].add_css_class("thumb-selected")
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
        """Select every thumbnail (batch-select mode only)."""
        if not self._select_mode:
            return
        self._selected = set(self._paths)
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
        """Raise the selected cards, lower the rest."""
        for path, button in zip(self._paths, self._buttons, strict=False):
            if path in self._selected:
                button.add_css_class("thumb-marked")
            else:
                button.remove_css_class("thumb-marked")

    def _notify_selection(self) -> None:
        """Report the current selection size to the listener."""
        if self._on_selection_changed is not None:
            self._on_selection_changed(len(self._selected))

    def scroll_step(self, direction: int) -> None:
        """Scroll the strip by one thumbnail card, keeping the selection.

        direction is -1 for left, +1 for right.
        """
        self._scroll_by(self._card_width() * direction)

    def set_glide_speed(self, px_per_second: float) -> None:
        """Set the hold-to-scroll speed (user preference)."""
        self._glide_speed = max(1.0, px_per_second)

    def start_glide(self, direction: int) -> None:
        """Scroll continuously (frame-synced) until stop_glide is called."""
        self._glide_dir = direction
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
            self._scroll_by(self._glide_speed * elapsed * self._glide_dir)
        self._glide_last = now
        return GLib.SOURCE_CONTINUE

    def _scroll_by(self, delta: float) -> None:
        """Move the horizontal scroll position by delta, clamped."""
        adj = self.get_hadjustment()
        top = adj.get_upper() - adj.get_page_size()
        adj.set_value(max(adj.get_lower(), min(top, adj.get_value() + delta)))

    def _on_scroll(self, _controller: Any, dx: float, dy: float) -> bool:
        """Pan the strip sideways from a plain wheel or trackpad swipe."""
        delta = dx or dy
        if delta:
            self._scroll_by(delta * self._card_width())
        return True

    def _card_width(self) -> float:
        """Width of one thumbnail card including the strip spacing."""
        width = self._buttons[0].get_width() if self._buttons else 0
        return (width or self._thumb_height * 1.5) + 6  # + box spacing

    def select_path(self, path: str) -> bool:
        """Select the thumbnail for path. False if it is not in the strip."""
        if path not in self._paths:
            return False
        self._set_current(self._paths.index(path), center=True)
        self._on_select(path)
        return True

    def select_relative(self, delta: int) -> None:
        """Select the image delta positions away (for keyboard nav)."""
        if not self._paths:
            return
        if self._current < 0:
            index = 0
        else:
            index = max(0, min(self._current + delta, len(self._paths) - 1))
        if index != self._current:
            self._set_current(index)
            self._on_select(self._paths[index])

    def _apply_thumb(
        self,
        picture: Any,
        camera_label: Any,
        pixbuf: Any,
        model: str,
        scan_id: int,
    ) -> None:
        """Set the thumbnail and camera caption of one card."""
        if scan_id == self._scan_id:
            picture.set_size_request(pixbuf.get_width(), self._thumb_height)
            picture.set_paintable(texture_for_pixbuf(pixbuf))
            camera_label.set_text(model)

    def _loading_done(self, scan_id: int) -> None:
        """Signal that this scan's thumbnails have finished decoding."""
        if scan_id == self._scan_id and self._on_loading is not None:
            self._on_loading(False)

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
