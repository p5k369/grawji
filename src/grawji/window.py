"""Main application window."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402


class MainWindow(Gtk.ApplicationWindow):
    """grawji main window: open RAF, tune recipe, preview, export."""

    def __init__(self, **kwargs: object) -> None:
        """Build the window shell (placeholder layout)."""
        super().__init__(**kwargs)
        self.set_title("grawji")
        self.set_default_size(1024, 768)
