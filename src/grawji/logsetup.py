"""Logging setup and console-noise control."""

from __future__ import annotations

import logging
import sys


class _StreamToLogger:
    """A text stream that forwards written lines to a logger."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        """Forward completed lines to logger at level."""
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        """Buffer text and log each completed line."""
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self) -> None:
        """Log any buffered partial line."""
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer.strip())
        self._buffer = ""

    def isatty(self) -> bool:
        """Never a terminal (so callers do not emit colour codes)."""
        return False

    def fileno(self) -> int:
        """Delegate to the real stdout for callers that need a descriptor."""
        if sys.__stdout__ is None:
            raise OSError("no stdout")
        return sys.__stdout__.fileno()


def configure_logging(*, verbose: bool) -> None:
    """Set up logging and quieten (or reveal) rawji's console chatter."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # rawji speaks via print(), capture stdout so its lines follow the log
    # level like everything else instead of always reaching the console.
    sys.stdout = _StreamToLogger(logging.getLogger("rawji"), logging.INFO)
