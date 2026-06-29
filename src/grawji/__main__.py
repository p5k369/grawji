"""Main."""

from __future__ import annotations

import sys

from grawji.app import GrawjiApp


def main(argv: list[str] | None = None) -> int:
    """Launch the grawji GTK application.

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv``.

    Returns:
        The process exit code.
    """
    app = GrawjiApp()
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
