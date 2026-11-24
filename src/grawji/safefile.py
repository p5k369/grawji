"""Writing a file so that a failed write leaves the old one intact."""

from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger("grawji")


def write_text(path: Path, text: str) -> None:
    """Replace a file's text in one step.

    Args:
        path: The file to replace.
        text: Its new content.
    """
    pending = path.with_name(path.name + ".tmp")
    try:
        pending.write_text(text, encoding="utf-8")
        pending.replace(path)
    except OSError:
        pending.unlink(missing_ok=True)
        raise


def set_aside(path: Path) -> Path | None:
    """Move an unreadable file out of the way instead of overwriting it.

    Args:
        path: The file that could not be read.
    """
    kept = path.with_name(path.name + ".damaged")
    try:
        path.replace(kept)
    except OSError as exc:
        _log.warning("could not keep the damaged %s: %s", path, exc)
        return None
    _log.warning("%s is unreadable, kept as %s", path, kept)
    return kept
