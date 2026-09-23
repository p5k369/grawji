"""Tests for RAF file operations."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("gi")

from gi.repository import GLib

from grawji import fileops
from grawji.crop import CropRotate
from grawji.fileops import copy_raf, move_raf, trash_raf
from grawji.sidecar import save_crop, sidecar_path


def _raf_with_sidecar(folder: Path, name: str) -> Path:
    raf = folder / name
    raf.write_bytes(b"raf-bytes")
    save_crop(raf, CropRotate(orientation=90))
    return raf


def test_copy_takes_the_sidecar(tmp_path: Path) -> None:
    """Copying a RAF copies its sidecar under the new name."""
    src_dir = tmp_path / "a"
    dest_dir = tmp_path / "b"
    src_dir.mkdir()
    dest_dir.mkdir()
    raf = _raf_with_sidecar(src_dir, "DSCF0001.RAF")
    target = copy_raf(raf, dest_dir)
    assert target == dest_dir / "DSCF0001.RAF"
    assert target.read_bytes() == b"raf-bytes"
    assert sidecar_path(target).exists()
    assert raf.exists()  # the source stays

    # A second copy never overwrites: it gets a numbered name.
    second = copy_raf(raf, dest_dir)
    assert second.name == "DSCF0001 (2).RAF"
    assert sidecar_path(second).exists()


def test_move_takes_the_sidecar(tmp_path: Path) -> None:
    """Moving a RAF moves its sidecar. Same-folder moves are no-ops."""
    src_dir = tmp_path / "a"
    dest_dir = tmp_path / "b"
    src_dir.mkdir()
    dest_dir.mkdir()
    raf = _raf_with_sidecar(src_dir, "DSCF0002.RAF")
    assert move_raf(raf, src_dir) == raf  # no-op into its own folder
    target = move_raf(raf, dest_dir)
    assert target == dest_dir / "DSCF0002.RAF"
    assert not raf.exists()
    assert not sidecar_path(raf).exists()
    assert sidecar_path(target).exists()


def test_copy_without_sidecar(tmp_path: Path) -> None:
    """A RAF without a sidecar copies alone."""
    raf = tmp_path / "DSCF0003.RAF"
    raf.write_bytes(b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    target = copy_raf(raf, dest)
    assert target.exists()
    assert not sidecar_path(target).exists()


def test_trash_takes_the_sidecar(tmp_path: Path) -> None:
    """Trashing removes RAF and sidecar from the folder."""
    raf = _raf_with_sidecar(tmp_path, "DSCF0004.RAF")
    try:
        trash_raf(raf)
    except GLib.Error as exc:  # pragma: no cover
        pytest.skip(f"no trash available here: {exc}")
    assert not raf.exists()
    assert not sidecar_path(raf).exists()


def test_following_prefers_the_next_image() -> None:
    """The survivor after the open image wins."""
    order = ["a", "b", "c"]
    assert fileops.following(order, ["b"], "b") == "c"


def test_following_falls_back_to_the_previous() -> None:
    """At the end of the strip the view walks backwards."""
    order = ["a", "b", "c"]
    assert fileops.following(order, ["c"], "c") == "b"


def test_following_skips_trashed_neighbors() -> None:
    """A batch trash lands on the nearest survivor."""
    order = ["a", "b", "c", "d"]
    assert fileops.following(order, ["b", "c"], "b") == "d"
    assert fileops.following(order, ["c", "d"], "c") == "b"


def test_following_is_none_when_the_open_image_survives() -> None:
    """Trashing other images never steals the view."""
    assert fileops.following(["a", "b"], ["b"], "a") is None
    assert fileops.following(["a", "b"], ["b"], None) is None


def test_following_is_none_when_nothing_survives() -> None:
    """An emptied folder has nothing to advance to."""
    assert fileops.following(["a", "b"], ["a", "b"], "a") is None
