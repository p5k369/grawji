"""One image of the filmstrip."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from grawji.catalog import Entry
from grawji.views.card_badges import (
    DetailBadges,
    ExportBadge,
    RatingBadges,
    style_rejected,
)

_UI = (
    resources.files("grawji")
    .joinpath("ui", "film_card.ui")
    .read_text(encoding="utf-8")
)

_ = (DetailBadges, ExportBadge, RatingBadges)
SLOT_RATIO = 4 / 3


@Gtk.Template(string=_UI)
class FilmCard(Gtk.Box):
    """A thumbnail between two caption rows of names and badges."""

    __gtype_name__ = "GrawjiFilmCard"

    rating = Gtk.Template.Child()
    camera_label = Gtk.Template.Child()
    export = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    name_label = Gtk.Template.Child()
    details = Gtk.Template.Child()
    primary_click = Gtk.Template.Child()
    menu_click = Gtk.Template.Child()
    drag_source = Gtk.Template.Child()

    def __init__(self, thumb_height: int, **kwargs: Any) -> None:
        """Build an empty card for thumbnails of the given height."""
        super().__init__(**kwargs)
        self.picture.set_size_request(
            int(thumb_height * SLOT_RATIO), thumb_height
        )

    def show_entry(self, entry: Entry) -> None:
        """Name the card after an image and draw its badges."""
        path = Path(entry.path)
        self.name_label.set_text(path.stem)
        self.set_tooltip_text(path.name)
        self.show_marks(entry)

    def show_marks(self, entry: Entry) -> None:
        """Draw the camera, the marks and the edit badges."""
        self.camera_label.set_text(entry.model)
        self.rating.show_entry(entry)
        self.export.show_entry(entry)
        self.details.show_entry(entry)
        style_rejected(self, entry)
