"""Adw.Application wiring."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from grawji.views.window import MainWindow


class GrawjiApp(Adw.Application):
    """The grawji application.

    Built on Adw.Application so it follows the system light/dark
    colour scheme and accent colour via libadwaita's style manager.
    """

    def __init__(self) -> None:
        """Initialise the application with its reverse-DNS id."""
        super().__init__(
            application_id="io.github.p5k369.grawji",
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )

    def _window(self) -> MainWindow:
        """The main window, created on first use."""
        return self.props.active_window or MainWindow(application=self)

    def do_activate(self) -> None:
        """Present the main window, reusing it if it already exists."""
        self._window().present()

    def do_open(self, files: list[Gio.File], _n: int, _hint: str) -> None:
        """Open a RAF passed by the file manager or command line."""
        window = self._window()
        window.present()
        path = files[0].get_path() if files else None
        if path is not None:
            window.open_raf(path)
