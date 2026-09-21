"""The folder as a grid."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk

from grawji import catalog
from grawji.imaging.thumbnails import ThumbnailLoader
from grawji.views.entry_item import EntryItem
from grawji.views.tile_thumbs import TileThumbs

# Room for the caption under each thumbnail.
_CAPTION_PX = 22
# Breathing room between tiles.
_GAP_PX = 10
# How long the size slider settles before thumbnails are re-decoded.
_RESIZE_SETTLE_MS = 350
# Frames the grid waits for a tile to be placed before centring it.
_CENTER_FRAMES = 30


class FolderGrid(Gtk.ScrolledWindow):
    """A scrolling grid of the folder's frames."""

    def __init__(
        self,
        *,
        loader: ThumbnailLoader,
        tile: int = 240,
        on_activate: Callable[[str], None] | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        """Build the grid."""
        super().__init__()
        self._tile = tile
        self._on_activate = on_activate
        self._on_select = on_select
        # True while the grid is being told where to stand, so
        # syncing the selection cannot bounce back.
        self._syncing = False
        self._thumbs = TileThumbs(loader, self)
        self._store = Gio.ListStore.new(EntryItem)
        self._filter = catalog.Filter()
        self._model_filter = Gtk.CustomFilter.new(self._passes)
        self._shown = Gtk.FilterListModel(
            model=self._store, filter=self._model_filter
        )
        self._selection = Gtk.SingleSelection(model=self._shown)
        self._selection.connect("notify::selected", self._on_selected)
        self._selection.set_autoselect(False)
        self._selection.set_can_unselect(True)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)
        self._view = Gtk.GridView(model=self._selection, factory=factory)
        self._view.set_min_columns(1)
        self._view.set_max_columns(1)
        self._fit_pending = 0
        self._resize_pending = 0
        self._reveal_wanted: str | None = None
        self._center_path: str | None = None
        self._center_tick = 0
        self._center_frames = 0
        self._cells: dict[Gtk.Widget, Gtk.AspectFrame] = {}
        self._view.connect("activate", self._on_activated)
        self.set_child(self._view)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        """Re-fit the columns and re-check the viewport on a resize."""
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        self._thumbs.schedule()
        if self._reveal_wanted is not None and width > 0:
            GLib.idle_add(self._catch_up_reveal)
        if not self._fit_pending:
            self._fit_pending = GLib.idle_add(self._fit_now)

    def _fit_now(self) -> bool:
        """Apply the column count once the allocation has settled."""
        self._fit_pending = 0
        self._fit_columns()
        return GLib.SOURCE_REMOVE

    def set_tile(self, height: int) -> None:
        """Change the tile size, live."""
        if height == self._tile:
            return
        self._tile = height
        for cell in self._cells.values():
            cell.set_size_request(int(height * 1.5), height)
        self._fit_columns()
        if self._resize_pending:
            GLib.source_remove(self._resize_pending)
        self._resize_pending = GLib.timeout_add(
            _RESIZE_SETTLE_MS, self._redecode
        )

    def _redecode(self) -> bool:
        """Fetch the visible thumbnails again at the current size."""
        self._resize_pending = 0
        self._thumbs.set_height(self._tile)
        self._thumbs.clear()
        return GLib.SOURCE_REMOVE

    def _fit_columns(self) -> None:
        """Use as many columns as fit at the tile's own width."""
        width = self.get_width() or self.get_hadjustment().get_page_size()
        if width <= 0:
            return
        columns = max(1, int(width // (self._tile * 1.5 + _GAP_PX)))
        if columns != self._view.get_max_columns():
            self._view.set_max_columns(columns)

    def show_entries(self, entries: Sequence[catalog.Entry]) -> None:
        """Replace the folder the grid shows.

        One splice rather than a remove and hundreds of appends, which
        the model would otherwise report one item at a time.
        """
        self._fit_columns()
        self._thumbs.clear()
        self._store.splice(
            0,
            self._store.get_n_items(),
            [EntryItem(entry) for entry in entries],
        )

    def set_filter(self, entry_filter: catalog.Filter) -> None:
        """Narrow the grid to what the filter leaves."""
        if entry_filter == self._filter:
            return
        self._filter = entry_filter
        self._model_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _passes(self, item: EntryItem) -> bool:
        """Whether one entry survives the active filter."""
        return self._filter.matches(item.entry)

    def select_path(self, path: str) -> bool:
        """Select the tile for path and put it in view."""
        for position in range(self._shown.get_n_items()):
            item = self._shown.get_item(position)
            if item is not None and item.entry.path == path:
                self._syncing = True
                self._selection.set_selected(position)
                self._syncing = False
                if self.get_width() <= 0:
                    self._reveal_wanted = path
                    return True
                if not self._is_near(position):
                    self._jump_near(position)
                self._view.scroll_to(position, Gtk.ListScrollFlags.NONE, None)
                self._center_on(path)
                return True
        return False

    def _is_near(self, position: int) -> bool:
        """Whether a position is within a page of what the grid shows."""
        rows = self._rows()
        adjustment = self.get_vadjustment()
        per_row = adjustment.get_upper() / rows if rows else 0
        if per_row <= 0:
            return False
        first = adjustment.get_value() / per_row
        page = adjustment.get_page_size() / per_row
        row = position // max(1, self._view.get_max_columns())
        return first - page <= row <= first + 2 * page

    def _jump_near(self, position: int) -> None:
        """Scroll roughly to a row, from the view's own estimate."""
        rows = self._rows()
        if rows <= 0:
            return
        adjustment = self.get_vadjustment()
        row = position // max(1, self._view.get_max_columns())
        middle = adjustment.get_upper() * (row + 0.5) / rows
        top = adjustment.get_upper() - adjustment.get_page_size()
        value = middle - adjustment.get_page_size() / 2
        adjustment.set_value(max(0.0, min(top, value)))

    def _rows(self) -> int:
        """How many rows the grid currently holds."""
        columns = max(1, self._view.get_max_columns())
        return -(-self._shown.get_n_items() // columns)

    def _catch_up_reveal(self) -> bool:
        """Show a frame that was selected while the page was hidden."""
        wanted = self._reveal_wanted
        self._reveal_wanted = None
        if wanted is not None:
            self.select_path(wanted)
        return GLib.SOURCE_REMOVE

    def _on_selected(self, *_args: object) -> None:
        """Tell the window which frame the grid is standing on."""
        if self._syncing or self._on_select is None:
            return
        item = self._selection.get_selected_item()
        if item is not None:
            self._on_select(item.entry.path)

    def _center_on(self, path: str) -> None:
        """Bring the selected tile to the middle, once it is placed."""
        self._center_path = path
        self._center_frames = _CENTER_FRAMES
        if self._center_tick:
            return
        self._center_tick = self.add_tick_callback(self._on_center_tick)

    def _on_center_tick(self, _widget: Any, _clock: Any) -> bool:
        """Center the selected tile as soon as it has a place."""
        self._center_frames -= 1
        adjustment = self.get_vadjustment()
        page = adjustment.get_page_size()
        for tile, (_picture, shown) in self._thumbs.bound.items():
            if shown != self._center_path or tile.get_width() <= 0:
                continue
            found, rect = tile.compute_bounds(self)
            if not found:
                continue
            middle = adjustment.get_value() + rect.origin.y
            middle += rect.size.height / 2
            top = adjustment.get_upper() - page
            adjustment.set_value(max(0.0, min(top, middle - page / 2)))
            self._center_tick = 0
            return GLib.SOURCE_REMOVE
        if self._center_frames > 0:
            return GLib.SOURCE_CONTINUE
        self._center_tick = 0
        return GLib.SOURCE_REMOVE

    @property
    def shown(self) -> int:
        """How many frames the filter currently leaves."""
        return int(self._shown.get_n_items())

    @property
    def held(self) -> int:
        """How many frames the folder holds, filter aside."""
        return int(self._store.get_n_items())

    def _on_setup(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Build one reusable tile."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(_GAP_PX // 2)
        box.set_margin_end(_GAP_PX // 2)
        box.set_margin_top(_GAP_PX // 2)
        box.set_margin_bottom(_GAP_PX // 2)
        picture = Gtk.Picture()
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        cell = Gtk.AspectFrame(ratio=1.5, obey_child=False)
        cell.set_size_request(int(self._tile * 1.5), self._tile)
        cell.set_child(picture)
        caption = Gtk.Label(ellipsize=3, max_width_chars=1)
        caption.add_css_class("caption")
        caption.set_size_request(-1, _CAPTION_PX)
        box.append(cell)
        box.append(caption)
        self._cells[box] = cell
        item.set_child(box)

    def _on_bind(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Fill a tile with an entry and ask for its thumbnail."""
        box = item.get_child()
        entry_item = item.get_item()
        if box is None or entry_item is None:
            return
        cell, caption = box.get_first_child(), box.get_last_child()
        picture = cell.get_child()
        entry = entry_item.entry
        caption.set_text(entry.name)
        self._thumbs.want(box, picture, entry.path)

    def _on_unbind(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Forget what a recycled tile was waiting for."""
        box = item.get_child()
        if box is not None:
            self._thumbs.forget(box)

    def _on_activated(self, _view: Gtk.GridView, position: int) -> None:
        """Open the frame a tile was activated on."""
        item = self._shown.get_item(position)
        if item is not None and self._on_activate is not None:
            self._on_activate(item.entry.path)
