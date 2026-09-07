"""Main."""

from __future__ import annotations

import sys

from gi.repository import GLib

from grawji.logsetup import configure_logging
from grawji.views.app import GrawjiApp

_VERBOSE_FLAGS = ("--verbose", "-v")
_APP_ID = "io.github.p5k369.grawji"


def main(argv: list[str] | None = None) -> int:
    """Launch the grawji GTK application.

    Args:
        argv: Command-line arguments. Defaults to sys.argv.

    Returns:
        The process exit code.
    """
    argv = list(sys.argv if argv is None else argv)
    verbose = any(flag in argv for flag in _VERBOSE_FLAGS)
    argv = [arg for arg in argv if arg not in _VERBOSE_FLAGS]
    configure_logging(verbose=verbose)
    GLib.set_prgname(_APP_ID)
    app = GrawjiApp()
    return app.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
