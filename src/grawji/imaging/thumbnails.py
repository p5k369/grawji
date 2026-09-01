"""Filmstrip thumbnail pipeline: EXIF thumbs, disk cache, decoding.

Decodes RAF thumbnails in parallel on worker threads and dispatches
each finished pixbuf back to the strip on the main loop.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GExiv2", "0.10")

from gi.repository import GdkPixbuf, GExiv2, GLib

from grawji.exif import format_focal
from grawji.raf import embedded_jpeg, embedded_jpeg_prefix

# How much of the embedded JPEG to read for the EXIF thumbnail.
_EXIF_PREFIX_BYTES = 256 * 1024

# The camera model rides inside the cached PNG as a tEXt chunk, so a warm
# start needs no RAF reads at all.
_MODEL_OPTION = "tEXt::grawji-model"
_LENS_OPTION = "tEXt::grawji-lens"
_FOCAL_OPTION = "tEXt::grawji-focal"


class ThumbMeta(NamedTuple):
    """Filter-relevant EXIF of one thumbnail."""

    model: str
    lens: str
    focal: str


Dispatch = Callable[[Callable[[], None]], Any]
OnThumb = Callable[[str, Any, Any, Any, ThumbMeta, int], None]
OnFinished = Callable[[int], None]

# EXIF orientation.
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


class ThumbnailLoader:
    """Loads a scan's thumbnails in parallel and reports each result."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        height: int,
        cache_dir: Path,
        workers: int,
        dispatch: Dispatch,
        is_stale: Callable[[int], bool],
        on_thumb: OnThumb,
        on_finished: OnFinished,
    ) -> None:
        """Create the loader.

        Args:
            height: Thumbnail height in pixels.
            cache_dir: Directory for the PNG thumbnail cache.
            workers: Decoder thread-pool size.
            dispatch: Schedules a callback on the main loop.
            is_stale: Whether a scan id has been superseded (results
                for it are dropped).
            on_thumb: Called on the main loop per finished thumbnail.
            on_finished: Called on the main loop with the scan id once
                every thumbnail of that scan is done.
        """
        self._height = height
        self._cache_dir = cache_dir
        self._workers = workers
        self._dispatch = dispatch
        self._is_stale = is_stale
        self._on_thumb = on_thumb
        self._on_finished = on_finished
        GExiv2.initialize()

    def load(self, cards: list[tuple[str, Any, Any]], scan_id: int) -> None:
        """Decode cards on worker threads."""
        threading.Thread(
            target=self._load_all,
            args=(cards, scan_id),
            name="grawji-thumbs",
            daemon=True,
        ).start()

    def _load_all(
        self, cards: list[tuple[str, Any, Any]], scan_id: int
    ) -> None:
        """Decode this scan's thumbnails in parallel and dispatch each."""
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for path, picture, camera_label in cards:
                pool.submit(
                    self._decode_one, path, picture, camera_label, scan_id
                )
        self._dispatch(partial(self._on_finished, scan_id))

    def _decode_one(
        self, path: str, picture: Any, camera_label: Any, scan_id: int
    ) -> None:
        """Produce one thumbnail and dispatch it."""
        if self._is_stale(scan_id):
            return
        try:
            pixbuf, meta = self._thumbnail(path)
        except (ValueError, OSError, GLib.Error):
            return
        self._dispatch(
            partial(
                self._on_thumb,
                path,
                picture,
                camera_label,
                pixbuf,
                meta,
                scan_id,
            )
        )

    def _thumbnail(self, path: str) -> tuple[Any, ThumbMeta]:
        """Return path's pixbuf and meta, cached when possible."""
        cache = self._cache_file(path)
        if cache is not None and cache.exists():
            try:
                cached = GdkPixbuf.Pixbuf.new_from_file(str(cache))
            except GLib.Error:
                cached = None
            if cached is not None:
                return cached, ThumbMeta(
                    cached.get_option(_MODEL_OPTION) or "",
                    cached.get_option(_LENS_OPTION) or "",
                    cached.get_option(_FOCAL_OPTION) or "",
                )
        pixbuf, meta = self._decode_thumb(path)
        if cache is not None:
            self._store_cache(cache, pixbuf, meta)
        return pixbuf, meta

    def _cache_file(self, path: str) -> Path | None:
        """Return the cache path for path, keyed by its size and mtime."""
        target = Path(path)
        try:
            stat = target.stat()
        except OSError:
            return None
        # v6: the cached PNG carries camera model, lens and focal length.
        key = (
            f"v6|{target.resolve()}|{stat.st_mtime_ns}"
            f"|{stat.st_size}|{self._height}"
        )
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()  # noqa: S324
        return self._cache_dir / f"{digest}.png"

    def _store_cache(self, cache: Path, pixbuf: Any, meta: ThumbMeta) -> None:
        """Write a decoded thumbnail to the cache, ignoring failures.

        Model, lens and focal length travel inside the PNG as tEXt
        chunks, so the warm path re-reads nothing from the RAF.
        """
        options = (
            (_MODEL_OPTION, meta.model),
            (_LENS_OPTION, meta.lens),
            (_FOCAL_OPTION, meta.focal),
        )
        keys = [k for k, v in options if v]
        values = [v for _k, v in options if v]
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            pixbuf.savev(str(cache), "png", keys, values)
        except (GLib.Error, OSError):
            pass

    def _decode_thumb(self, path: str) -> tuple[Any, ThumbMeta]:
        """Decode a RAF into a pixbuf plus its filter metadata."""
        exif_thumb = self._exif_thumbnail_of(path)
        if exif_thumb is not None:
            data, orientation, meta = exif_thumb
            pixbuf = orient_exif(self._decode_bytes(data), orientation)
        else:
            jpeg = embedded_jpeg(path)
            pixbuf = self._decode_bytes(jpeg, downscale=True)
            pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
            meta = _meta_of(jpeg)
        return self._to_height(pixbuf), meta

    @staticmethod
    def _exif_thumbnail_of(
        path: str,
    ) -> tuple[bytes, int, ThumbMeta] | None:
        """Read only enough of the RAF to extract its EXIF thumbnail."""
        try:
            prefix = embedded_jpeg_prefix(path, _EXIF_PREFIX_BYTES)
        except (ValueError, OSError):
            return None
        return _exif_thumbnail(prefix)

    def _decode_bytes(self, data: bytes, *, downscale: bool = False) -> Any:
        """Decode JPEG bytes, optionally downscaling to the row height."""
        loader = GdkPixbuf.PixbufLoader()
        if downscale:
            loader.connect("size-prepared", self._scale_to_height)
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()

    def _to_height(self, pixbuf: Any) -> Any:
        """Scale a pixbuf to exactly the row height, keeping its aspect."""
        if pixbuf.get_height() == self._height:
            return pixbuf
        width = max(
            1,
            round(pixbuf.get_width() * self._height / pixbuf.get_height()),
        )
        return pixbuf.scale_simple(
            width, self._height, GdkPixbuf.InterpType.BILINEAR
        )

    def _scale_to_height(self, loader: Any, width: int, height: int) -> None:
        """Scale the image to the thumbnail height, keeping aspect."""
        if height <= 0:
            return
        scale = self._height / height
        loader.set_size(max(1, int(width * scale)), self._height)


def _exif_thumbnail(jpeg: bytes) -> tuple[bytes, int, ThumbMeta] | None:
    """Return thumbnail bytes, EXIF orientation and filter metadata."""
    try:
        meta = GExiv2.Metadata()
        meta.open_buf(jpeg)
        thumb = meta.get_exif_thumbnail()
    except GLib.Error:
        return None
    if isinstance(thumb, tuple):
        thumb = thumb[-1]
    if not thumb:
        return None
    try:
        orientation = int(meta.get_orientation())
    except (GLib.Error, ValueError):
        orientation = 1
    return bytes(thumb), orientation, _tags_of(meta)


def _tag_of(meta: Any, tag: str) -> str:
    """One EXIF tag as a string."""
    try:
        return meta.try_get_tag_string(tag) or ""
    except GLib.Error:
        return ""


def _tags_of(meta: Any) -> ThumbMeta:
    """The filter metadata of open EXIF metadata."""
    raw = _tag_of(meta, "Exif.Photo.FocalLength")
    return ThumbMeta(
        model=_tag_of(meta, "Exif.Image.Model"),
        lens=_tag_of(meta, "Exif.Photo.LensModel"),
        focal=format_focal(raw) if raw else "",
    )


def _meta_of(jpeg: bytes) -> ThumbMeta:
    """Read the filter metadata from JPEG bytes."""
    meta = GExiv2.Metadata()
    try:
        meta.open_buf(jpeg)
    except GLib.Error:
        return ThumbMeta("", "", "")
    return _tags_of(meta)


def orient_exif(pixbuf: Any, orientation: int) -> Any:
    """Rotate/flip a pixbuf per its EXIF orientation."""
    rotation, flip = _ORIENTATIONS.get(orientation, (_R.NONE, False))
    pixbuf = pixbuf.rotate_simple(rotation) or pixbuf
    if flip:
        pixbuf = pixbuf.flip(True) or pixbuf
    return pixbuf
