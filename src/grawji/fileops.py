"""File operations on RAFs.

Every RAF travels with its sidecar and with the camera JPEG of a
RAW+JPEG shot, so the three always keep one shared name.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio

from grawji.pairs import paired_jpeg
from grawji.sidecar import sidecar_path

_log = logging.getLogger("grawji")


def _companions(source: Path) -> list[tuple[Path, Callable[[Path], Path]]]:
    """The files that travel with a RAF, each with its name rule."""
    found: list[tuple[Path, Callable[[Path], Path]]] = []
    side = sidecar_path(source)
    if side.exists():
        found.append((side, sidecar_path))
    jpeg = paired_jpeg(source)
    if jpeg is not None:
        suffix = jpeg.suffix
        found.append((jpeg, lambda raf: raf.with_suffix(suffix)))
    return found


def _unique_destination(
    dest_dir: Path,
    name: str,
    companions: list[tuple[Path, Callable[[Path], Path]]],
) -> Path:
    """A free path for name in dest_dir, numbering duplicates."""
    candidate = dest_dir / name
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while candidate.exists() or any(
        rule(candidate).exists() for _path, rule in companions
    ):
        counter += 1
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
    return candidate


def following(
    order: list[str], trashed: list[str], current: str | None
) -> str | None:
    """The image to show once trashing takes the open one along."""
    if current is None or current not in trashed or current not in order:
        return None
    gone = set(trashed)
    index = order.index(current)
    remaining = order[index + 1 :] + order[:index][::-1]
    for path in remaining:
        if path not in gone:
            return path
    return None


def copy_raf(src: Path | str, dest_dir: Path | str) -> Path:
    """Copy a RAF and its companions into dest_dir."""
    source = Path(src)
    companions = _companions(source)
    target = _unique_destination(Path(dest_dir), source.name, companions)
    shutil.copy2(source, target)
    for path, rule in companions:
        shutil.copy2(path, rule(target))
    return target


def move_raf(src: Path | str, dest_dir: Path | str) -> Path:
    """Move a RAF and its companions into dest_dir.

    Moving into the file's own folder is a no-op. Name collisions get
    a numbered name, like copy_raf.
    """
    source = Path(src)
    directory = Path(dest_dir)
    if directory == source.parent:
        return source
    companions = _companions(source)
    target = _unique_destination(directory, source.name, companions)
    shutil.move(str(source), str(target))
    for path, rule in companions:
        shutil.move(str(path), str(rule(target)))
    return target


def trash_raf(src: Path | str) -> None:
    """Move a RAF and its companions to the trash."""
    source = Path(src)
    companions = _companions(source)
    Gio.File.new_for_path(str(source)).trash(None)
    for path, _rule in companions:
        try:
            Gio.File.new_for_path(str(path)).trash(None)
        except Exception:
            _log.warning("could not trash %s", path)
