"""The folder grid with its size slider and count."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from grawji.settings import Settings
from grawji.views.folder_grid import FolderGrid
from grawji.views.preview_view import BACKGROUNDS

_TILE_SAVE_DELAY_MS = 500
_GRID_PAGE = "grid"
_PREVIEW_PAGE = "preview"


@dataclass(frozen=True)
class BrowseWidgets:
    """The window's widgets the Browse page works with.

    Attributes:
        slot: The box the grid is placed in.
        size: The tile size slider.
        count: The label saying how many frames are shown.
        stack: The stack that switches between Browse and the preview.
    """

    slot: Gtk.Box
    size: Gtk.Scale
    count: Gtk.Label
    stack: Any


@dataclass(frozen=True)
class BrowseHost:
    """What the Browse page needs from the window.

    Attributes:
        current_path: The image selected in the filmstrip.
        has_folder: Whether a folder is open.
        on_page: Called with whether Browse is the page now showing.
        save_settings: Writes the settings to disk.
    """

    current_path: Callable[[], str | None]
    has_folder: Callable[[], bool]
    on_page: Callable[[bool], None]
    save_settings: Callable[[], None]


class BrowsePage:
    """Keeps the folder grid, its slider and its count in step."""

    def __init__(
        self,
        grid: FolderGrid,
        widgets: BrowseWidgets,
        settings: Settings,
        host: BrowseHost,
    ) -> None:
        """Place the grid and wire the page's controls."""
        self.grid = grid
        self._widgets = widgets
        self._settings = settings
        self._host = host
        self._save_id = 0
        widgets.slot.append(grid)
        widgets.size.set_value(settings.grid_tile_px)
        widgets.size.connect("value-changed", self._on_tile_size)
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", self._on_dark_changed)
        self._on_dark_changed(style)
        widgets.stack.connect(
            "notify::visible-child-name", self._on_page_changed
        )
        widgets.stack.set_visible_child_name(_PREVIEW_PAGE)
        self.set_canvas(settings.canvas_background)

    @property
    def is_open(self) -> bool:
        """Whether Browse is the visible page right now."""
        return bool(self._widgets.stack.get_visible_child_name() == _GRID_PAGE)

    def set_open(self, *, open_it: bool) -> None:
        """Show Browse or the preview."""
        self._widgets.stack.set_visible_child_name(
            _GRID_PAGE if open_it else _PREVIEW_PAGE
        )

    def set_canvas(self, css_class: str) -> None:
        """Paint the grid's canvas like the preview's."""
        for name in BACKGROUNDS:
            if name:
                self.grid.remove_css_class(name)
        if css_class:
            self.grid.add_css_class(css_class)

    def fill(self) -> bool:
        """Line the grid up with the filmstrip."""
        if not self._host.has_folder():
            return False
        current = self._host.current_path()
        if current is not None:
            self.grid.select_path(current)
        self.update_count()
        return True

    def update_count(self) -> None:
        """Say how many frames the grid shows."""
        shown, held = self.grid.shown, self.grid.held
        if not held:
            text = ""
        elif shown == held:
            text = f"{held} frames"
        else:
            text = f"{shown} of {held} frames"
        self._widgets.count.set_label(text)

    def _on_page_changed(self, *_args: object) -> None:
        """React to the page the tabs, the key or the code chose."""
        browsing = self.is_open
        self._host.on_page(browsing)
        if browsing:
            self.fill()

    def _on_dark_changed(
        self, style: Adw.StyleManager, *_args: object
    ) -> None:
        """Soften the grid cards when the theme goes dark."""
        if style.get_dark():
            self.grid.add_css_class("dim-cards")
        else:
            self.grid.remove_css_class("dim-cards")

    def _on_tile_size(self, scale: Gtk.Scale) -> None:
        """Resize the grid's tiles and remember the choice."""
        size = int(scale.get_value())
        self.grid.set_tile(size)
        self.update_count()
        self._settings.grid_tile_px = size
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(
            _TILE_SAVE_DELAY_MS, self._save_tile_size
        )

    def _save_tile_size(self) -> bool:
        """Write the tile size once the slider stops moving."""
        self._save_id = 0
        self._host.save_settings()
        return bool(GLib.SOURCE_REMOVE)
