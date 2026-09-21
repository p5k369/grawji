"""One catalog entry."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject

from grawji import catalog


class EntryItem(GObject.Object):
    """A catalog entry as a list item."""

    __gtype_name__ = "GrawjiEntryItem"

    def __init__(self, entry: catalog.Entry) -> None:
        """Wrap entry."""
        super().__init__()
        self.entry = entry
