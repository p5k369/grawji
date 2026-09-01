"""Tests for the thumbnail cache prune."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("gi")

from grawji.imaging.thumbnails import prune_cache

_DAY = 24 * 3600


def _aged(path: Path, age_s: float, now: float) -> None:
    """Create a cache file whose mtime lies age_s in the past."""
    path.write_bytes(b"png")
    os.utime(path, (now - age_s, now - age_s))


def test_prune_deletes_only_stale_files(tmp_path: Path) -> None:
    """Files past the age limit go, recently used ones stay."""
    now = 1_000_000_000.0
    _aged(tmp_path / "old.png", 40 * _DAY, now)
    _aged(tmp_path / "fresh.png", 5 * _DAY, now)
    removed = prune_cache(tmp_path, max_age_s=30 * _DAY, now=now)
    assert removed == 1
    assert not (tmp_path / "old.png").exists()
    assert (tmp_path / "fresh.png").exists()


def test_prune_ignores_foreign_files(tmp_path: Path) -> None:
    """Only .png cache entries are considered."""
    now = 1_000_000_000.0
    _aged(tmp_path / "notes.txt", 400 * _DAY, now)
    assert prune_cache(tmp_path, max_age_s=30 * _DAY, now=now) == 0
    assert (tmp_path / "notes.txt").exists()


def test_prune_tolerates_a_missing_directory(tmp_path: Path) -> None:
    """A cache directory that does not exist yet is a no-op."""
    assert prune_cache(tmp_path / "absent", now=0.0) == 0
