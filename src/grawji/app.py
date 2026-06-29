"""Gtk.Application wiring."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from grawji.window import MainWindow  # noqa: E402


class GrawjiApp(Gtk.Application):
    """The grawji GTK4 application."""

    def __init__(self) -> None:
        """Initialise the application with its reverse-DNS id."""
        super().__init__(application_id="io.github.p5k369.grawji")

    def do_activate(self) -> None:
        """Create and present the main window on activation."""
        window = MainWindow(application=self)
        window.present()
