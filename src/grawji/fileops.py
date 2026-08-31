"""File operations on RAFs. Every RAF travels with its sidecar."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from gi.repository import Gio

from grawji.sidecar import sidecar_path

_log = logging.getLogger("grawji")


def _unique_destination(dest_dir: Path, name: str) -> Path:
    """A free path for name in dest_dir, numbering duplicates."""
    candidate = dest_dir / name
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
    return candidate


def copy_raf(src: Path | str, dest_dir: Path | str) -> Path:
    """Copy a RAF and its sidecar into dest_dir and returns the new path.

    An existing file of the same name is never overwritten: the copy
    gets a numbered name, and the sidecar follows the chosen name.
    """
    source = Path(src)
    target = _unique_destination(Path(dest_dir), source.name)
    shutil.copy2(source, target)
    side = sidecar_path(source)
    if side.exists():
        shutil.copy2(side, sidecar_path(target))
    return target


def move_raf(src: Path | str, dest_dir: Path | str) -> Path:
    """Move a RAF and its sidecar into dest_dir and returns the new path.

    Moving into the file's own folder is a no-op. Name collisions get
    a numbered name, like copy_raf.
    """
    source = Path(src)
    directory = Path(dest_dir)
    if directory == source.parent:
        return source
    target = _unique_destination(directory, source.name)
    shutil.move(str(source), str(target))
    side = sidecar_path(source)
    if side.exists():
        shutil.move(str(side), str(sidecar_path(target)))
    return target


def trash_raf(src: Path | str) -> None:
    """Move a RAF and its sidecar to the trash."""
    source = Path(src)
    Gio.File.new_for_path(str(source)).trash(None)
    side = sidecar_path(source)
    if side.exists():
        try:
            Gio.File.new_for_path(str(side)).trash(None)
        except Exception:
            _log.warning("could not trash sidecar %s", side)
