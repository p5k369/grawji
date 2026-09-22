"""Thumbnails for a virtualized view, loaded for the visible tiles only."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

from grawji.imaging.thumbnails import ThumbMeta, ThumbnailLoader
from grawji.views.textures import texture_for_pixbuf

# A tile this far outside the viewport still counts as worth loading.
_MARGIN_PX = 200
# And this far ahead of a scroll, so tiles arrive before they appear.
_LEAD_PX = 700
# Frames the check keeps running after it stops finding anything new.
_SETTLE_FRAMES = 12
# A hard stop, so waiting for a layout that never comes cannot spin.
_MAX_FRAMES = 600
# Frames fetched per pass once the viewport has what it needs.
_PREFETCH_BATCH = 8
# How long the folder waits before it is worked through in the background.
_PREFETCH_DELAY_MS = 400
# How many decoded frames are kept, so a huge folder cannot fill memory.
_TEXTURE_CAP = 1000


class TileThumbs:
    """Loads thumbnails for whichever tiles a scroller currently shows."""

    def __init__(
        self,
        loader: ThumbnailLoader,
        scroller: Gtk.ScrolledWindow,
        *,
        on_meta: Any = None,
        sizes_to_content: bool = False,
        keep: int = _TEXTURE_CAP,
        stand_in: Any = None,
    ) -> None:
        """Serve tiles of scroller from loader."""
        self._loader = loader
        self._scroller = scroller
        self._keep = keep
        self._stand_in = stand_in
        self._on_meta = on_meta
        self._sizes_to_content = sizes_to_content
        self._bound: dict[Gtk.Widget, tuple[Gtk.Picture, str]] = {}
        self._waiting: dict[Gtk.Widget, tuple[Gtk.Picture, str]] = {}
        self._pending: dict[Gtk.Widget, tuple[Gtk.Picture, str]] = {}
        self._textures: dict[str, Any] = {}
        self._queue: list[str] = []
        self._cursor = 0
        self._ahead: set[str] = set()
        self._prefetch_id = 0
        self._tick = 0
        self._frames_left = 0
        self._frames_total = 0
        self._lead_x = 0
        self._lead_y = 0
        self._last = {"x": 0.0, "y": 0.0}
        for axis, adjustment in (
            ("x", scroller.get_hadjustment()),
            ("y", scroller.get_vadjustment()),
        ):
            adjustment.connect("value-changed", self._on_scrolled, axis)

    def _on_scrolled(self, adjustment: Gtk.Adjustment, axis: str) -> None:
        """Note the travel direction and look at the viewport again."""
        value = adjustment.get_value()
        moved = value - self._last[axis]
        self._last[axis] = value
        if moved:
            lead = 1 if moved > 0 else -1
            if axis == "x":
                self._lead_x = lead
            else:
                self._lead_y = lead
        self.schedule()

    def want(self, tile: Gtk.Widget, picture: Gtk.Picture, path: str) -> None:
        """Show path's thumbnail on picture once the tile is in view."""
        self._bound[tile] = (picture, path)
        known = self._textures.get(path)
        if known is not None:
            self._textures[path] = self._textures.pop(path)
            self._paint(picture, known)
            self._waiting.pop(tile, None)
            return
        rough = self._stand_in(path) if self._stand_in else None
        picture.set_paintable(rough)
        self._waiting[tile] = (picture, path)
        self.schedule()

    def forget(self, tile: Gtk.Widget) -> None:
        """Drop a recycled tile, so a late thumbnail does not land."""
        self._bound.pop(tile, None)
        self._waiting.pop(tile, None)
        self._pending.pop(tile, None)

    def set_height(self, height: int) -> None:
        """Ask for thumbnails of a new size from here on."""
        self._loader.set_height(height)

    def clear(self) -> None:
        """Drop the textures and ask again for every bound tile."""
        self._pending.clear()
        self._textures.clear()
        self._ahead.clear()
        self._queue = []
        self._cursor = 0
        self._waiting = dict(self._bound)
        self.schedule()

    def _paint(self, picture: Gtk.Picture, texture: Any) -> None:
        """Show a texture and let it set the tile's size."""
        if self._sizes_to_content:
            picture.set_size_request(
                texture.get_intrinsic_width(), texture.get_intrinsic_height()
            )
        picture.set_paintable(texture)

    def _evict(self) -> None:
        """Drop the least recently shown frames over the cap."""
        on_screen = {shown for _p, shown in self._bound.values()}
        for path in list(self._textures):
            if len(self._textures) <= self._keep:
                return
            if path not in on_screen:
                del self._textures[path]

    def texture_for(self, path: str) -> Any | None:
        """The decoded frame for a path, if this view has one."""
        return self._textures.get(path)

    @property
    def bound(self) -> dict[Gtk.Widget, tuple[Gtk.Picture, str]]:
        """Every tile currently showing a frame."""
        return self._bound

    @property
    def waiting(self) -> int:
        """How many bound tiles still have no thumbnail."""
        return len(self._waiting)

    @property
    def busy(self) -> bool:
        """Whether a thumbnail request is still out."""
        return bool(self._pending)

    def prefetch(self, paths: list[str], around: int = 0) -> None:
        """Fetch the rest of the folder once the visible part is served."""
        start = max(0, min(len(paths) - 1, around))
        order = {path: index for index, path in enumerate(paths)}
        self._queue = sorted(
            (path for path in paths if path not in self._textures),
            key=lambda path: abs(order[path] - start),
        )
        self._cursor = 0
        if self._queue and not self._prefetch_id:
            self._prefetch_id = GLib.timeout_add(
                _PREFETCH_DELAY_MS, self._prefetch_batch
            )

    def _prefetch_batch(self) -> bool:
        """Ask for a few more frames, unless the viewport is waiting."""
        done = self._cursor >= len(self._queue)
        if done or len(self._textures) >= self._keep:
            self._prefetch_id = 0
            return GLib.SOURCE_REMOVE
        if self.busy or self._ahead:
            return GLib.SOURCE_CONTINUE
        sent = 0
        while self._cursor < len(self._queue) and sent < _PREFETCH_BATCH:
            path = self._queue[self._cursor]
            self._cursor += 1
            if path in self._textures or path in self._ahead:
                continue
            self._ahead.add(path)
            self._loader.request(path, self._on_thumb)
            sent += 1
        return GLib.SOURCE_CONTINUE

    def schedule(self, *, fresh: bool = False) -> None:
        """Look at the viewport on the next few frames."""
        self._frames_left = _SETTLE_FRAMES
        if fresh:
            self._frames_total = 0
        if self._tick:
            return
        self._frames_total = 0
        self._tick = self._scroller.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget: Any, _clock: Any) -> bool:
        """Re-check the viewport per frame until it stops paying off."""
        self._frames_total += 1
        if self.request_visible():
            self._frames_left = _SETTLE_FRAMES
            self._frames_total = 0
        elif self._unplaced():
            self._frames_left = _SETTLE_FRAMES
        else:
            self._frames_left -= 1
        keep = (
            self._waiting
            and self._frames_left > 0
            and self._frames_total < _MAX_FRAMES
        )
        if keep:
            return GLib.SOURCE_CONTINUE
        self._tick = 0
        return GLib.SOURCE_REMOVE

    def _unplaced(self) -> bool:
        """Whether a waiting tile is still without an allocation."""
        return any(
            tile.get_width() <= 0 or tile.get_height() <= 0
            for tile in self._waiting
        )

    def request_visible(self) -> int:
        """Ask for the thumbnails of the tiles actually in view."""
        width = self._scroller.get_width()
        height = self._scroller.get_height()
        if width <= 0 or height <= 0:
            return 0
        asked = 0
        for tile, (picture, path) in list(self._waiting.items()):
            if tile.get_width() <= 0 or tile.get_height() <= 0:
                continue
            found, rect = tile.compute_bounds(self._scroller)
            if not found:
                continue
            if not self._intersects(rect, width, height):
                continue
            del self._waiting[tile]
            self._pending[tile] = (picture, path)
            self._loader.request(path, self._on_thumb)
            asked += 1
        return asked

    def _intersects(self, rect: Any, width: float, height: float) -> bool:
        """Whether a tile's rect reaches the viewport."""
        ahead_x, behind_x = self._margins(self._lead_x)
        ahead_y, behind_y = self._margins(self._lead_y)
        left, top = rect.origin.x, rect.origin.y
        right, bottom = left + rect.size.width, top + rect.size.height
        return (
            left <= width + ahead_x
            and right >= -behind_x
            and top <= height + ahead_y
            and bottom >= -behind_y
        )

    @staticmethod
    def _margins(lead: int) -> tuple[float, float]:
        """How far to look ahead and behind on one axis."""
        if lead > 0:
            return _LEAD_PX, _MARGIN_PX
        if lead < 0:
            return _MARGIN_PX, _LEAD_PX
        return _MARGIN_PX, _MARGIN_PX

    def _on_thumb(self, path: str, pixbuf: Any, meta: ThumbMeta) -> None:
        """Paint a decoded thumbnail on every tile still waiting for it."""
        self._ahead.discard(path)
        texture = texture_for_pixbuf(pixbuf)
        self._textures[path] = texture
        self._evict()
        for tile, (picture, wanted) in list(self._pending.items()):
            if wanted == path:
                self._paint(picture, texture)
                del self._pending[tile]
        for tile, (picture, wanted) in list(self._waiting.items()):
            if wanted == path:
                self._paint(picture, texture)
                del self._waiting[tile]
        if self._on_meta is not None:
            self._on_meta(path, meta, pixbuf.get_width(), pixbuf.get_height())
