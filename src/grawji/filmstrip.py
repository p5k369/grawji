"""Bottom filmstrip of RAF thumbnails."""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from grawji.raf import embedded_jpeg  # noqa: E402

Dispatch = Callable[[Callable[[], None]], Any]


class FilmStrip(Gtk.ScrolledWindow):
    """A horizontally-scrolling strip of clickable RAF thumbnails."""

    def __init__(
        self,
        *,
        on_select: Callable[[str], None],
        dispatch: Dispatch = GLib.idle_add,
        thumb_height: int = 110,
    ) -> None:
        """Create the filmstrip.

        Args:
            on_select: Called with the RAF path when a thumbnail is
                clicked.
            dispatch: Schedules a callback on the GTK main loop.
            thumb_height: Thumbnail height in pixels.
        """
        super().__init__()
        self._on_select = on_select
        self._dispatch = dispatch
        self._thumb_height = thumb_height
        self._scan_id = 0
        self._paths: list[str] = []
        self._buttons: list[Gtk.Button] = []
        self._current = -1

        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._box.set_margin_start(4)
        self._box.set_margin_end(4)
        self._box.set_margin_top(4)
        self._box.set_margin_bottom(4)
        self.set_child(self._box)
        self.set_min_content_height(thumb_height + 16)

    def scan(self, folder: str) -> None:
        """Populate the strip with the RAF files in ``folder``."""
        self._scan_id += 1
        scan_id = self._scan_id
        self._clear()

        base = Path(folder)
        paths = sorted(
            {p for pat in ("*.RAF", "*.raf") for p in base.glob(pat)}
        )
        self._paths = [str(p) for p in paths]
        self._buttons = []
        self._current = -1
        pictures = []
        for path in paths:
            picture = Gtk.Picture()
            picture.set_size_request(
                int(self._thumb_height * 1.5), self._thumb_height
            )
            button = Gtk.Button(child=picture)
            button.add_css_class("flat")
            button.add_css_class("thumb")
            button.set_tooltip_text(path.name)
            button.connect("clicked", partial(self._on_clicked, str(path)))
            self._box.append(button)
            self._buttons.append(button)
            pictures.append((str(path), picture))

        if pictures:
            threading.Thread(
                target=self._load_thumbnails,
                args=(pictures, scan_id),
                name="grawji-thumbs",
                daemon=True,
            ).start()

    @property
    def paths(self) -> list[str]:
        """The RAF paths currently shown, in display order."""
        return list(self._paths)

    def _clear(self) -> None:
        """Remove all thumbnails currently in the strip."""
        child = self._box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._box.remove(child)
            child = nxt
        self._buttons = []

    def _set_current(self, index: int) -> None:
        """Mark ``index`` as selected and update the highlight."""
        for pos, button in enumerate(self._buttons):
            if pos == index:
                button.add_css_class("thumb-selected")
            else:
                button.remove_css_class("thumb-selected")
        self._current = index
        if 0 <= index < len(self._buttons):
            self._scroll_into_view(self._buttons[index])

    def _scroll_into_view(self, button: Gtk.Button) -> None:
        """Scroll the strip horizontally so ``button`` is visible."""
        adj = self.get_hadjustment()
        ok, rect = button.compute_bounds(self._box)
        if not ok:
            return
        left, right = rect.origin.x, rect.origin.x + rect.size.width
        page = adj.get_page_size()
        value = adj.get_value()
        if left < value:
            adj.set_value(left)
        elif right > value + page:
            adj.set_value(right - page)

    def _on_clicked(self, path: str, _button: Gtk.Button) -> None:
        """Notify the listener that a thumbnail was clicked."""
        if path in self._paths:
            self._set_current(self._paths.index(path))
        self._on_select(path)

    def select_relative(self, delta: int) -> None:
        """Select the image ``delta`` positions away (for keyboard nav)."""
        if not self._paths:
            return
        if self._current < 0:
            index = 0
        else:
            index = max(0, min(self._current + delta, len(self._paths) - 1))
        if index != self._current:
            self._set_current(index)
            self._on_select(self._paths[index])

    def _load_thumbnails(
        self, pictures: list[tuple[str, Any]], scan_id: int
    ) -> None:
        """Decode each RAF's thumbnail off-thread and dispatch it."""
        for path, picture in pictures:
            if scan_id != self._scan_id:
                return  # a newer scan superseded this one
            try:
                pixbuf = self._decode_thumb(embedded_jpeg(path))
            except (ValueError, OSError, GLib.Error):
                continue  # skip unreadable / non-RAF files
            self._dispatch(
                partial(self._apply_thumb, picture, pixbuf, scan_id)
            )

    def _decode_thumb(self, jpeg: bytes) -> Any:
        """Decode JPEG bytes into a thumbnail-sized, oriented pixbuf."""
        loader = GdkPixbuf.PixbufLoader()
        loader.connect("size-prepared", self._scale_to_thumb)
        loader.write(jpeg)
        loader.close()
        pixbuf = loader.get_pixbuf()
        return pixbuf.apply_embedded_orientation() or pixbuf

    def _scale_to_thumb(self, loader: Any, width: int, height: int) -> None:
        """Scale the image to the thumbnail height, keeping aspect."""
        if height <= 0:
            return
        scale = self._thumb_height / height
        loader.set_size(max(1, int(width * scale)), self._thumb_height)

    def _apply_thumb(self, picture: Any, pixbuf: Any, scan_id: int) -> None:
        """Set the thumbnail on its picture if the scan is still current."""
        if scan_id == self._scan_id:
            picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
