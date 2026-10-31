"""One dynamic range's carrier slot of a body."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

_UI = (
    resources.files("grawji")
    .joinpath("ui", "carrier_row.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class CarrierRow(Adw.ActionRow):
    """A row naming the carrier of one dynamic range, or what covers it."""

    __gtype_name__ = "GrawjiCarrierRow"

    clear_button = Gtk.Template.Child()

    def show(self, path: str | None, *, served_by: int | None) -> None:
        """Name the registered file, or the higher range standing in."""
        if path:
            self.set_subtitle(Path(path).name)
        elif served_by is not None:
            self.set_subtitle(f"Missing, served by the DR{served_by} carrier")
        else:
            self.set_subtitle("Missing")
        self.clear_button.set_visible(bool(path))
