"""The badges in a card's caption rows."""

from __future__ import annotations

from importlib import resources

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from grawji.catalog import Entry
from grawji.marks import MAX_RATING, Label
from grawji.pairs import companion_label


def _ui(name: str) -> str:
    """One bundled .ui template."""
    return (
        resources.files("grawji")
        .joinpath("ui", name)
        .read_text(encoding="utf-8")
    )


@Gtk.Template(string=_ui("rating_badges.ui"))
class RatingBadges(Gtk.Box):
    """The stars, or the reject mark in their place."""

    __gtype_name__ = "GrawjiRatingBadges"

    star_1 = Gtk.Template.Child()
    star_2 = Gtk.Template.Child()
    star_3 = Gtk.Template.Child()
    star_4 = Gtk.Template.Child()
    star_5 = Gtk.Template.Child()
    reject_icon = Gtk.Template.Child()

    def show_entry(self, entry: Entry) -> None:
        """Draw one image's rating."""
        marks = entry.marks
        for count in range(1, MAX_RATING + 1):
            star: Gtk.Widget = getattr(self, f"star_{count}")
            star.set_visible(count <= marks.stars)
        self.reject_icon.set_visible(marks.rejected)


@Gtk.Template(string=_ui("export_badge.ui"))
class ExportBadge(Gtk.Box):
    """The export mark, kept apart from the rating on the other edge."""

    __gtype_name__ = "GrawjiExportBadge"

    export_icon = Gtk.Template.Child()

    def show_entry(self, entry: Entry) -> None:
        """Draw whether the image is marked for export."""
        self.export_icon.set_visible(entry.marks.export)


@Gtk.Template(string=_ui("detail_badges.ui"))
class DetailBadges(Gtk.Box):
    """The color labels, the source, the exposure and crop edits."""

    __gtype_name__ = "GrawjiDetailBadges"

    dot_red = Gtk.Template.Child()
    dot_yellow = Gtk.Template.Child()
    dot_green = Gtk.Template.Child()
    dot_blue = Gtk.Template.Child()
    dot_purple = Gtk.Template.Child()
    source_badge = Gtk.Template.Child()
    ev_icon = Gtk.Template.Child()
    crop_icon = Gtk.Template.Child()

    def show_entry(self, entry: Entry) -> None:
        """Draw one image's color labels and edits."""
        for label in Label:
            dot: Gtk.Widget = getattr(self, f"dot_{label.value}")
            dot.set_visible(label in entry.marks.labels)
        self.source_badge.set_visible(entry.uses_camera_file)
        if entry.uses_camera_file and entry.companion is not None:
            self.source_badge.set_label(companion_label(entry.companion))
        self.ev_icon.set_visible(entry.has_ev)
        self.crop_icon.set_visible(entry.has_crop)


def style_rejected(card: Gtk.Widget, entry: Entry) -> None:
    """Dim a card whose image is marked as a reject."""
    if entry.marks.rejected:
        card.add_css_class("mark-rejected")
    else:
        card.remove_css_class("mark-rejected")
