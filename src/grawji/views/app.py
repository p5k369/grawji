"""Adw.Application wiring."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from grawji.views.window import MainWindow


class GrawjiApp(Adw.Application):
    """The grawji application.

    Built on Adw.Application so it follows the system light/dark
    colour scheme and accent colour via libadwaita's style manager.
    """

    def __init__(self) -> None:
        """Initialise the application with its reverse-DNS id."""
        super().__init__(application_id="io.github.p5k369.grawji")

    def do_activate(self) -> None:
        """Present the main window, reusing it if it already exists."""
        window = self.props.active_window or MainWindow(application=self)
        window.present()
