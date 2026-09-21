"""Filmstrip thumbnail pipeline: EXIF thumbs, disk cache, decoding.

Decodes RAF thumbnails in parallel on worker threads and dispatches
each finished pixbuf back to the strip on the main loop.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
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
from grawji.imaging.pixbufs import (
    crop_to_aspect,
    orient_exif,
    trim_letterbox,
)
from grawji.raf import embedded_jpeg, embedded_jpeg_prefix

# How much of the embedded JPEG to read for the EXIF thumbnail.
_EXIF_PREFIX_BYTES = 256 * 1024

# Cached thumbnails unused for this long are deleted at startup.
_CACHE_MAX_AGE_S = 30 * 24 * 3600


def remembered_shapes(cache_dir: Path) -> dict[str, float]:
    """Frame shapes this cache has already seen, by cache key."""
    shapes: dict[str, float] = {}
    try:
        with (cache_dir / _SHAPES_FILE).open() as handle:
            for line in handle:
                key, _, ratio = line.partition(" ")
                with contextlib.suppress(ValueError):
                    shapes[key] = float(ratio)
    except OSError:
        return shapes
    return shapes


def prune_cache(
    cache_dir: Path,
    max_age_s: float = _CACHE_MAX_AGE_S,
    now: float | None = None,
) -> int:
    """Delete cached thumbnails unused for max_age_s."""
    if now is None:
        now = time.time()
    removed = 0
    try:
        entries = [
            entry
            for suffix in _CACHE_SUFFIXES
            for entry in cache_dir.glob(f"*{suffix}")
        ]
    except OSError:
        return 0
    for entry in entries:
        with contextlib.suppress(OSError):
            if now - entry.stat().st_mtime > max_age_s:
                entry.unlink()
                removed += 1
    if removed:
        _prune_shapes(cache_dir)
    return removed


def _prune_shapes(cache_dir: Path) -> None:
    """Drop remembered shapes whose thumbnail is gone."""
    shapes = remembered_shapes(cache_dir)
    if not shapes:
        return
    kept = {
        key: ratio
        for key, ratio in shapes.items()
        if (cache_dir / f"{key}.jpg").exists()
    }
    if len(kept) == len(shapes):
        return
    lines = "".join(f"{key} {ratio:.4f}\n" for key, ratio in kept.items())
    with contextlib.suppress(OSError):
        (cache_dir / _SHAPES_FILE).write_text(lines)


# Filter metadata rides along in the cached JPEG's own Exif tags.
_MODEL_TAG = "Exif.Image.Model"
_LENS_TAG = "Exif.Photo.LensModel"
_FOCAL_TAG = "Exif.Image.ImageDescription"
_SHAPES_FILE = "shapes.v1"
_CACHE_QUALITY = "88"
# What a cache entry can be called, past spellings included.
_CACHE_SUFFIXES = (".jpg", ".png")


class ThumbMeta(NamedTuple):
    """Filter-relevant EXIF of one thumbnail."""

    model: str
    lens: str
    focal: str


Dispatch = Callable[[Callable[[], None]], Any]
OnThumb = Callable[[str, Any, Any, Any, ThumbMeta, int], None]
OnFinished = Callable[[int], None]
OnOne = Callable[[str, Any, ThumbMeta], None]


class ThumbnailLoader:
    """Loads a scan's thumbnails in parallel and reports each result."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        height: int,
        cache_dir: Path,
        workers: int,
        sharp: bool = False,
        dispatch: Dispatch,
        is_stale: Callable[[int], bool],
        on_thumb: OnThumb,
        on_finished: OnFinished,
    ) -> None:
        """Create the loader.

        Args:
            height: Thumbnail height in pixels.
            cache_dir: Directory for the thumbnail cache.
            workers: Decoder thread-pool size.
            sharp: Decode the RAF's full preview instead of its small
                Exif thumbnail.
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
        self._sharp = sharp
        self._dispatch = dispatch
        self._is_stale = is_stale
        self._on_thumb = on_thumb
        self._on_finished = on_finished
        self._pruned = False
        self._shapes: dict[str, float] | None = None
        self._pool: ThreadPoolExecutor | None = None
        GExiv2.initialize()

    def set_height(self, height: int) -> None:
        """Decode at a new size from here on."""
        self._height = height

    def request(self, path: str, on_ready: OnOne) -> None:
        """Decode one thumbnail off the main loop."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._workers)
        self._pool.submit(self._request_one, path, on_ready)

    def _request_one(self, path: str, on_ready: OnOne) -> None:
        """Produce one thumbnail for a single request and dispatch it."""
        try:
            pixbuf, meta = self._thumbnail(path)
        except (ValueError, OSError, GLib.Error):
            return
        self._dispatch(partial(on_ready, path, pixbuf, meta))

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
        if not self._pruned:
            self._pruned = True
            prune_cache(self._cache_dir)
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
                with contextlib.suppress(OSError):
                    os.utime(cache)
                # A warm cache decodes nothing, so this is where the
                # shape of an already known frame gets recorded.
                self._remember_shape(cache, cached)
                return cached, self._cached_meta(cache)
        pixbuf, meta = self._decode_thumb(path)
        if cache is not None:
            self._store_cache(cache, pixbuf, meta)
        return pixbuf, meta

    @staticmethod
    def _cached_meta(cache: Path) -> ThumbMeta:
        """Read the filter metadata back out of a cached thumbnail."""
        try:
            tags = GExiv2.Metadata()
            tags.open_path(str(cache))
            return ThumbMeta(
                tags.try_get_tag_string(_MODEL_TAG) or "",
                tags.try_get_tag_string(_LENS_TAG) or "",
                tags.try_get_tag_string(_FOCAL_TAG) or "",
            )
        except GLib.Error:
            return ThumbMeta("", "", "")

    def cache_key(self, path: str) -> str | None:
        """The key this file's thumbnail is cached under."""
        target = Path(path)
        try:
            stat = target.stat()
        except OSError:
            return None
        return self._digest(target, stat)

    def _digest(self, target: Path, stat: os.stat_result) -> str:
        """Hash the identity a cached thumbnail is bound to."""
        key = (
            f"v7|{target.resolve()}|{stat.st_mtime_ns}"
            f"|{stat.st_size}|{self._height}|{int(self._sharp)}"
        )
        return hashlib.sha1(key.encode("utf-8")).hexdigest()  # noqa: S324

    def _cache_file(self, path: str) -> Path | None:
        """Return the cache path for path, keyed by its size and mtime."""
        target = Path(path)
        try:
            stat = target.stat()
        except OSError:
            return None
        # v7: the cache is a JPEG carrying its metadata in Exif. PNG
        # cost four milliseconds and 45 KB per frame to write, against
        # half a millisecond and 9 KB here.
        return self._cache_dir / f"{self._digest(target, stat)}.jpg"

    def _remember_shape(self, cache: Path, pixbuf: Any) -> None:
        """Note how wide this frame turned out, keyed like its cache."""
        height = pixbuf.get_height()
        if height <= 0:
            return
        if self._shapes is None:
            self._shapes = remembered_shapes(self._cache_dir)
        if cache.stem in self._shapes:
            return
        ratio = pixbuf.get_width() / height
        self._shapes[cache.stem] = ratio
        shapes = self._cache_dir / _SHAPES_FILE
        with contextlib.suppress(OSError), shapes.open("a") as handle:
            handle.write(f"{cache.stem} {ratio:.4f}\n")

    def _store_cache(self, cache: Path, pixbuf: Any, meta: ThumbMeta) -> None:
        """Write a decoded thumbnail to the cache, ignoring failures."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            pixbuf.savev(str(cache), "jpeg", ["quality"], [_CACHE_QUALITY])
            self._remember_shape(cache, pixbuf)
            if any((meta.model, meta.lens, meta.focal)):
                tags = GExiv2.Metadata()
                tags.open_path(str(cache))
                for tag, value in (
                    (_MODEL_TAG, meta.model),
                    (_LENS_TAG, meta.lens),
                    (_FOCAL_TAG, meta.focal),
                ):
                    if value:
                        tags.try_set_tag_string(tag, value)
                tags.save_file(str(cache))
        except (GLib.Error, OSError):
            pass

    def _decode_thumb(self, path: str) -> tuple[Any, ThumbMeta]:
        """Decode a RAF into a pixbuf plus its filter metadata."""
        exif_thumb = None if self._sharp else self._exif_thumbnail_of(path)
        if exif_thumb is not None:
            data, orientation, meta, ratio = exif_thumb
            pixbuf = self._unpadded(self._decode_bytes(data), ratio)
            pixbuf = orient_exif(pixbuf, orientation)
        else:
            jpeg = embedded_jpeg(path)
            pixbuf = self._decode_bytes(jpeg, downscale=True)
            pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
            meta = _meta_of(jpeg)
        return self._to_height(pixbuf), meta

    @staticmethod
    def _exif_thumbnail_of(
        path: str,
    ) -> tuple[bytes, int, ThumbMeta, float | None] | None:
        """Read only enough of the RAF to extract its EXIF thumbnail."""
        try:
            prefix = embedded_jpeg_prefix(path, _EXIF_PREFIX_BYTES)
        except (ValueError, OSError):
            return None
        return _exif_thumbnail(prefix)

    @staticmethod
    def _unpadded(pixbuf: Any, ratio: float | None) -> Any:
        """Cut the 4:3 padding off a camera thumbnail."""
        if ratio is None:
            return trim_letterbox(pixbuf)
        return crop_to_aspect(pixbuf, ratio)

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


def _frame_ratio(meta: Any) -> float | None:
    """The shot's width over height, as its Exif records it."""
    width = meta.try_get_tag_long("Exif.Photo.PixelXDimension")
    height = meta.try_get_tag_long("Exif.Photo.PixelYDimension")
    if not width or not height:
        return None
    return float(width) / float(height)


def _exif_thumbnail(
    jpeg: bytes,
) -> tuple[bytes, int, ThumbMeta, float | None] | None:
    """Return the thumbnail, its orientation, metadata and frame shape."""
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
        orientation = int(meta.try_get_orientation())
    except (GLib.Error, ValueError):
        orientation = 1
    return bytes(thumb), orientation, _tags_of(meta), _frame_ratio(meta)


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
