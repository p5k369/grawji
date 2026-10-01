"""The marks of the open image in the preview's status bar."""

from __future__ import annotations

from importlib import resources

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from grawji.marks import MAX_RATING, Label, Marks

_UI = (
    resources.files("grawji")
    .joinpath("ui", "marks_bar.ui")
    .read_text(encoding="utf-8")
)

_ON = "mark-on"


def _set_on(widget: Gtk.Widget, *, on: bool) -> None:
    """Show a mark button as set or unset."""
    if on:
        widget.add_css_class(_ON)
    else:
        widget.remove_css_class(_ON)


@Gtk.Template(string=_UI)
class MarksBar(Gtk.Box):
    """Reject, stars, color labels and the export mark."""

    __gtype_name__ = "GrawjiMarksBar"

    reject_button = Gtk.Template.Child()
    star_1 = Gtk.Template.Child()
    star_2 = Gtk.Template.Child()
    star_3 = Gtk.Template.Child()
    star_4 = Gtk.Template.Child()
    star_5 = Gtk.Template.Child()
    color_red = Gtk.Template.Child()
    color_yellow = Gtk.Template.Child()
    color_green = Gtk.Template.Child()
    color_blue = Gtk.Template.Child()
    color_purple = Gtk.Template.Child()
    export_button = Gtk.Template.Child()

    def show_marks(self, marks: Marks) -> None:
        """Draw the open image's marks."""
        _set_on(self.reject_button, on=marks.rejected)
        for count in range(1, MAX_RATING + 1):
            star: Gtk.Button = getattr(self, f"star_{count}")
            filled = count <= marks.stars
            star.set_icon_name(
                "starred-symbolic" if filled else "non-starred-symbolic"
            )
            _set_on(star, on=filled)
        for label in Label:
            button: Gtk.Button = getattr(self, f"color_{label.value}")
            _set_on(button, on=label in marks.labels)
        _set_on(self.export_button, on=marks.export)
