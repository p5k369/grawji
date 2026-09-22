"""Tests for the thumbnail cache prune."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gi")

from grawji.imaging.thumbnails import prune_cache

_DAY = 24 * 3600


def _aged(path: Path, age_s: float, now: float) -> None:
    """Create a cache file whose mtime lies age_s in the past."""
    path.write_bytes(b"jpeg")
    os.utime(path, (now - age_s, now - age_s))


def test_prune_deletes_only_stale_files(tmp_path: Path) -> None:
    """Files past the age limit go, recently used ones stay."""
    now = 1_000_000_000.0
    _aged(tmp_path / "old.jpg", 40 * _DAY, now)
    _aged(tmp_path / "fresh.jpg", 5 * _DAY, now)
    removed = prune_cache(tmp_path, max_age_s=30 * _DAY, now=now)
    assert removed == 1
    assert not (tmp_path / "old.jpg").exists()
    assert (tmp_path / "fresh.jpg").exists()


def test_prune_ignores_foreign_files(tmp_path: Path) -> None:
    """Only .jpg cache entries are considered."""
    now = 1_000_000_000.0
    _aged(tmp_path / "notes.txt", 400 * _DAY, now)
    assert prune_cache(tmp_path, max_age_s=30 * _DAY, now=now) == 0
    assert (tmp_path / "notes.txt").exists()


def test_prune_tolerates_a_missing_directory(tmp_path: Path) -> None:
    """A cache directory that does not exist yet is a no-op."""
    assert prune_cache(tmp_path / "absent", now=0.0) == 0


def test_prune_also_ages_out_the_old_png_cache(tmp_path: Path) -> None:
    """The cache used to be PNG, and those entries must go too."""
    now = 1_000_000_000.0
    _aged(tmp_path / "old.png", 40 * _DAY, now)
    _aged(tmp_path / "old.jpg", 40 * _DAY, now)
    assert prune_cache(tmp_path, max_age_s=30 * _DAY, now=now) == 2
    assert not (tmp_path / "old.png").exists()


def test_the_cache_is_pruned_once_a_session(tmp_path: Path) -> None:
    """Pruning used to ride along with a bulk load that is gone."""
    from grawji.imaging.thumbnails import ThumbnailLoader

    pruned: list[Path] = []
    calls: list[str] = []

    class Pool:
        """Stand-in for the decoder pool."""

        def submit(self, call: Any, *args: Any) -> None:
            """Record what would have run on a worker."""
            if getattr(call, "__name__", "") == "prune_cache":
                pruned.append(args[0])
            else:
                calls.append(args[0])

    loader = ThumbnailLoader(
        height=110,
        cache_dir=tmp_path,
        workers=1,
        dispatch=lambda call: call(),
    )
    loader._pool = Pool()
    loader.request("/frames/a.RAF", lambda *_a: None)
    loader.request("/frames/b.RAF", lambda *_a: None)
    assert pruned == [tmp_path]
    assert calls == ["/frames/a.RAF", "/frames/b.RAF"]


def test_the_cache_remembers_what_a_frame_is(tmp_path: Path) -> None:
    """A folder seen before is filterable without reading it again."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    from grawji.imaging.thumbnails import (
        ThumbMeta,
        ThumbnailLoader,
        remembered_facts,
    )

    loader = ThumbnailLoader(
        height=110,
        cache_dir=tmp_path,
        workers=1,
        dispatch=lambda call: call(),
    )
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 30, 20)
    meta = ThumbMeta("X-E5", "XF23mmF2 R WR", "23.0 mm")
    loader._remember(tmp_path / "abc.jpg", pixbuf, meta)
    facts = remembered_facts(tmp_path)
    assert facts["abc"].aspect == 1.5
    assert facts["abc"].meta == meta
    loader._remember(tmp_path / "abc.jpg", pixbuf, meta)
    assert len(remembered_facts(tmp_path)) == 1


def test_the_metadata_sweep_reads_only_the_head(tmp_path: Path) -> None:
    """A folder's filters must not wait for full-file reads."""
    from grawji.imaging import thumbnails
    from grawji.imaging.thumbnails import ThumbnailLoader

    got: list[tuple[str, Any, Any]] = []
    loader = ThumbnailLoader(
        height=110,
        cache_dir=tmp_path,
        workers=1,
        dispatch=lambda call: call(),
    )
    meta = thumbnails.ThumbMeta("X-E5", "XF23mmF2", "23 mm")
    loader._sweep_one = lambda path, ready: ready(path, meta, 1.5)
    loader.sweep_meta(["/frames/a.RAF"], lambda *a: got.append(a))
    assert loader._meta_pool is not None
    assert loader._pool is None
    loader._meta_pool.shutdown(wait=True)
    assert got == [("/frames/a.RAF", meta, 1.5)]


def test_swept_frames_are_remembered_without_a_thumbnail(
    tmp_path: Path,
) -> None:
    """The next visit needs no sweep, and pruning keeps the memory."""
    from grawji.imaging import thumbnails
    from grawji.imaging.thumbnails import ThumbnailLoader, remembered_facts

    raf = tmp_path / "a.RAF"
    raf.write_bytes(b"not a real raf")
    loader = ThumbnailLoader(
        height=110,
        cache_dir=tmp_path,
        workers=1,
        dispatch=lambda call: call(),
    )
    meta = thumbnails.ThumbMeta("X-E5", "XF23mmF2", "23 mm")
    real = thumbnails.read_frame_meta
    thumbnails.read_frame_meta = lambda _path: (meta, 1.5)
    got: list[str] = []
    try:
        loader._sweep_one(str(raf), lambda p, _m, _a: got.append(p))
    finally:
        thumbnails.read_frame_meta = real
    assert got == [str(raf)]
    facts = remembered_facts(tmp_path)
    key = loader.cache_key(str(raf))
    assert key is not None
    assert facts[key].meta == meta
    prune_cache(tmp_path, max_age_s=0, now=1e12)
    assert remembered_facts(tmp_path)[key].meta == meta
