"""Writing a file so that a failed write leaves the old one intact."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from pathlib import Path

_log = logging.getLogger("grawji")


def write_text(path: Path, text: str) -> None:
    """Replace a file's text in one step.

    Args:
        path: The file to replace.
        text: Its new content.
    """
    target = path.resolve() if path.is_symlink() else path
    pending = target.with_name(target.name + ".tmp")
    try:
        with pending.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            shutil.copymode(target, pending)
        pending.replace(target)
    except OSError:
        pending.unlink(missing_ok=True)
        raise


def set_aside(path: Path) -> Path | None:
    """Move an unreadable file out of the way instead of overwriting it.

    Args:
        path: The file that could not be read.
    """
    kept = path.with_name(path.name + ".damaged")
    count = 1
    while kept.exists():
        count += 1
        kept = path.with_name(f"{path.name}.damaged.{count}")
    try:
        path.replace(kept)
    except OSError as exc:
        _log.warning("could not keep the damaged %s: %s", path, exc)
        return None
    _log.warning("%s is unreadable, kept as %s", path, kept)
    return kept
