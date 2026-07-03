"""Bottom filmstrip of RAF thumbnails."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GExiv2", "0.10")

from gi.repository import Gdk, GdkPixbuf, GExiv2, GLib, Gtk, Pango

from grawji.raf import embedded_jpeg, embedded_jpeg_prefix
from grawji.settings import cache_dir

# How much of the embedded JPEG to read for the EXIF thumbnail (near the
# start), so the multi-megabyte preview is not touched on the fast path.
_EXIF_PREFIX_BYTES = 256 * 1024

# The camera model rides inside the cached PNG as a tEXt chunk, so a warm
# start needs no RAF reads at all.
_MODEL_OPTION = "tEXt::grawji-model"

# Default continuous-scroll speed while a nav arrow is held, in px/second.
_GLIDE_PX_PER_S_DEFAULT = 600

Dispatch = Callable[[Callable[[], None]], Any]

# EXIF orientation -> (GdkPixbuf rotation, flip horizontally) to display it
# upright. 5 and 7 (rare transpose/transverse) approximate with a rotation.
_R = GdkPixbuf.PixbufRotation
_ORIENTATIONS = {
    1: (_R.NONE, False),
    2: (_R.NONE, True),
    3: (_R.UPSIDEDOWN, False),
    4: (_R.UPSIDEDOWN, True),
    5: (_R.CLOCKWISE, True),
    6: (_R.CLOCKWISE, False),
    7: (_R.COUNTERCLOCKWISE, True),
    8: (_R.COUNTERCLOCKWISE, False),
}


class FilmStrip(Gtk.ScrolledWindow):
    """A horizontally-scrolling strip of clickable RAF thumbnails."""

    def __init__(
        self,
        *,
        on_select: Callable[[str], None],
        on_loading: Callable[[bool], None] | None = None,
        dispatch: Dispatch = GLib.idle_add,
        thumb_height: int = 110,
    ) -> None:
        """Create the filmstrip.

        Args:
            on_select: Called with the RAF path when a thumbnail is
                clicked.
            on_loading: Called with True when thumbnail decoding starts and
                False when it finishes, for an activity indicator elsewhere.
            dispatch: Schedules a callback on the GTK main loop.
            thumb_height: Thumbnail height in pixels.
        """
        super().__init__()
        self._on_select = on_select
        self._on_loading = on_loading
        self._dispatch = dispatch
        self._thumb_height = thumb_height
        self._scan_id = 0
        self._paths: list[str] = []
        self._buttons: list[Gtk.Button] = []
        self._current = -1
        self._cache_dir = cache_dir() / "thumbs"
        self._workers = max(1, (os.cpu_count() or 2) - 1)
        self._glide_tick: int | None = None
        self._glide_last: int | None = None
        self._glide_dir = 0
        self._glide_speed = float(_GLIDE_PX_PER_S_DEFAULT)
        GExiv2.initialize()

        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._box.set_margin_start(4)
        self._box.set_margin_end(4)
        self._box.set_margin_top(4)
        self._box.set_margin_bottom(4)
        self.set_child(self._box)
        self.set_min_content_height(thumb_height + 52)

    def scan(self, folder: str) -> None:
        """Populate the strip with the RAF files in folder."""
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
        cards = []
        for path in paths:
            picture, camera_label, button = self._build_card(path)
            button.connect("clicked", partial(self._on_clicked, str(path)))
            self._box.append(button)
            self._buttons.append(button)
            cards.append((str(path), picture, camera_label))

        if cards:
            if self._on_loading is not None:
                self._on_loading(True)
            threading.Thread(
                target=self._load_thumbnails,
                args=(cards, scan_id),
                name="grawji-thumbs",
                daemon=True,
            ).start()

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
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        card.set_margin_top(2)
        card.set_margin_bottom(2)
        card.append(camera_label)
        card.append(picture)
        card.append(name_label)

        button = Gtk.Button(child=card)
        button.add_css_class("card")
        button.add_css_class("thumb")
        button.set_tooltip_text(path.name)
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

    def _set_current(self, index: int) -> None:
        """Mark index as selected and update the highlight."""
        for pos, button in enumerate(self._buttons):
            if pos == index:
                button.add_css_class("thumb-selected")
            else:
                button.remove_css_class("thumb-selected")
        self._current = index
        if 0 <= index < len(self._buttons):
            self._scroll_into_view(self._buttons[index])

    def _scroll_into_view(self, button: Gtk.Button) -> None:
        """Scroll the strip horizontally so button is visible."""
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

    def _card_width(self) -> float:
        """Width of one thumbnail card including the strip spacing."""
        width = self._buttons[0].get_width() if self._buttons else 0
        return (width or self._thumb_height * 1.5) + 6  # + box spacing

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

    def _load_thumbnails(
        self, cards: list[tuple[str, Any, Any]], scan_id: int
    ) -> None:
        """Decode this scan's thumbnails in parallel and dispatch each."""
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for path, picture, camera_label in cards:
                pool.submit(
                    self._decode_one, path, picture, camera_label, scan_id
                )
        self._dispatch(partial(self._loading_done, scan_id))

    def _decode_one(
        self, path: str, picture: Any, camera_label: Any, scan_id: int
    ) -> None:
        """Produce one thumbnail (cache or decode) and dispatch it."""
        if scan_id != self._scan_id:
            return  # a newer scan superseded this one
        try:
            pixbuf, model = self._thumbnail(path)
        except (ValueError, OSError, GLib.Error):
            return  # skip unreadable / non-RAF files
        apply = partial(
            self._apply_thumb, picture, camera_label, pixbuf, model, scan_id
        )
        self._dispatch(apply)

    def _thumbnail(self, path: str) -> tuple[Any, str]:
        """Return path's (thumbnail, camera model), cached when possible."""
        cache = self._cache_file(path)
        if cache is not None and cache.exists():
            try:
                cached = GdkPixbuf.Pixbuf.new_from_file(str(cache))
            except GLib.Error:
                cached = None  # corrupt cache entry: re-decode below
            if cached is not None:
                return cached, cached.get_option(_MODEL_OPTION) or ""
        pixbuf, model = self._decode_thumb(path)
        if cache is not None:
            self._store_cache(cache, pixbuf, model)
        return pixbuf, model

    def _cache_file(self, path: str) -> Path | None:
        """Return the cache path for path, keyed by its size and mtime."""
        target = Path(path)
        try:
            stat = target.stat()
        except OSError:
            return None
        # v4: the cached PNG additionally carries the camera model.
        key = (
            f"v4|{target.resolve()}|{stat.st_mtime_ns}"
            f"|{stat.st_size}|{self._thumb_height}"
        )
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()  # noqa: S324
        return self._cache_dir / f"{digest}.png"

    def _store_cache(self, cache: Path, pixbuf: Any, model: str) -> None:
        """Write a decoded thumbnail to the cache, ignoring failures.

        The camera model travels inside the PNG as a tEXt chunk, so the
        warm path re-reads nothing from the RAF.
        """
        keys, values = ([_MODEL_OPTION], [model]) if model else ([], [])
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            pixbuf.savev(str(cache), "png", keys, values)
        except (GLib.Error, OSError):
            pass

    def _decode_thumb(self, path: str) -> tuple[Any, str]:
        """Decode a RAF into a thumb_height-tall pixbuf plus camera model.

        Prefers the tiny EXIF thumbnail baked into the embedded JPEG, read
        from a bounded prefix so the multi-megabyte preview is not touched;
        the camera model comes out of that same read. Falls back to
        decoding the full embedded JPEG (downscaled at load) when the RAF
        carries no embedded thumbnail.
        """
        exif_thumb = self._exif_thumbnail_of(path)
        if exif_thumb is not None:
            data, orientation, model = exif_thumb
            pixbuf = self._orient(self._decode_bytes(data), orientation)
        else:
            jpeg = embedded_jpeg(path)
            pixbuf = self._decode_bytes(jpeg, downscale=True)
            pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
            model = self._model_of(jpeg)
        return self._to_thumb_height(pixbuf), model

    @staticmethod
    def _exif_thumbnail_of(path: str) -> tuple[bytes, int, str] | None:
        """Read only enough of the RAF to extract its EXIF thumbnail."""
        try:
            prefix = embedded_jpeg_prefix(path, _EXIF_PREFIX_BYTES)
        except (ValueError, OSError):
            return None
        return FilmStrip._exif_thumbnail(prefix)

    @staticmethod
    def _exif_thumbnail(jpeg: bytes) -> tuple[bytes, int, str] | None:
        """Return (thumbnail bytes, EXIF orientation, camera model), or None.

        Fuji bakes the photo into a 4:3 thumbnail with black letterbox bars.
        Those are kept as-is (all thumbnails share the 4:3 frame, so the
        strip stays a uniform height).
        """
        try:
            meta = GExiv2.Metadata()
            meta.open_buf(jpeg)
            thumb = meta.get_exif_thumbnail()
        except GLib.Error:
            return None
        if isinstance(thumb, tuple):  # some bindings return (ok, data)
            thumb = thumb[-1]
        if not thumb:
            return None
        try:
            orientation = int(meta.get_orientation())
        except (GLib.Error, ValueError):
            orientation = 1
        try:
            model = meta.try_get_tag_string("Exif.Image.Model") or ""
        except GLib.Error:
            model = ""
        return bytes(thumb), orientation, model

    @staticmethod
    def _model_of(jpeg: bytes) -> str:
        """Read the camera model from JPEG bytes, or an empty string."""
        try:
            meta = GExiv2.Metadata()
            meta.open_buf(jpeg)
            return meta.try_get_tag_string("Exif.Image.Model") or ""
        except GLib.Error:
            return ""

    def _decode_bytes(self, data: bytes, *, downscale: bool = False) -> Any:
        """Decode JPEG bytes, optionally downscaling to the row height."""
        loader = GdkPixbuf.PixbufLoader()
        if downscale:
            loader.connect("size-prepared", self._scale_to_thumb)
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()

    @staticmethod
    def _orient(pixbuf: Any, orientation: int) -> Any:
        """Rotate/flip a pixbuf per its EXIF orientation."""
        rotation, flip = _ORIENTATIONS.get(orientation, (_R.NONE, False))
        pixbuf = pixbuf.rotate_simple(rotation) or pixbuf
        if flip:
            pixbuf = pixbuf.flip(True) or pixbuf
        return pixbuf

    def _to_thumb_height(self, pixbuf: Any) -> Any:
        """Scale a pixbuf to exactly the row height, keeping its aspect."""
        if pixbuf.get_height() == self._thumb_height:
            return pixbuf
        width = max(
            1,
            round(
                pixbuf.get_width() * self._thumb_height / pixbuf.get_height()
            ),
        )
        return pixbuf.scale_simple(
            width, self._thumb_height, GdkPixbuf.InterpType.BILINEAR
        )

    def _scale_to_thumb(self, loader: Any, width: int, height: int) -> None:
        """Scale the image to the thumbnail height, keeping aspect."""
        if height <= 0:
            return
        scale = self._thumb_height / height
        loader.set_size(max(1, int(width * scale)), self._thumb_height)

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
            picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
            camera_label.set_text(model)

    def _loading_done(self, scan_id: int) -> None:
        """Signal that this scan's thumbnails have finished decoding."""
        if scan_id == self._scan_id and self._on_loading is not None:
            self._on_loading(False)
