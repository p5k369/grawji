"""The filter popover."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from grawji.marks import MAX_RATING

_UI = (
    resources.files("grawji")
    .joinpath("ui", "filter_popover.ui")
    .read_text(encoding="utf-8")
)

OnPick = Callable[[str], None]


@Gtk.Template(string=_UI)
class FilterPopover(Gtk.Popover):
    """Camera, lens, focal length, rating, labels and marks."""

    __gtype_name__ = "GrawjiFilterPopover"

    camera_box = Gtk.Template.Child()
    lens_box = Gtk.Template.Child()
    focal_section = Gtk.Template.Child()
    focal_slot = Gtk.Template.Child()
    star_1 = Gtk.Template.Child()
    star_2 = Gtk.Template.Child()
    star_3 = Gtk.Template.Child()
    star_4 = Gtk.Template.Child()
    star_5 = Gtk.Template.Child()

    def __init__(self) -> None:
        """Build the popover with empty choices."""
        super().__init__()
        self._radios: dict[str, dict[str, Gtk.CheckButton]] = {}
        self._syncing = False

    def set_choices(
        self,
        axis: str,
        values: list[str],
        selected: str | None,
        on_pick: OnPick,
    ) -> None:
        """Offer All plus the folder's values as radio buttons."""
        box = self.camera_box if axis == "model" else self.lens_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        radios: dict[str, Gtk.CheckButton] = {}
        group: Gtk.CheckButton | None = None
        for label, value in [("All", ""), *((v, v) for v in values)]:
            radio = Gtk.CheckButton(label=label)
            radio.set_group(group)
            group = group or radio
            radio.connect("toggled", self._on_radio, value, on_pick)
            box.append(radio)
            radios[value] = radio
        self._radios[axis] = radios
        self.select(axis, selected)

    def select(self, axis: str, value: str | None) -> None:
        """Show a choice as picked without reporting it back."""
        radio = self._radios.get(axis, {}).get(value or "")
        if radio is None or radio.get_active():
            return
        self._syncing = True
        radio.set_active(True)
        self._syncing = False

    def _on_radio(
        self, radio: Gtk.CheckButton, value: str, on_pick: OnPick
    ) -> None:
        """Report a radio the person picked."""
        if radio.get_active() and not self._syncing:
            on_pick(value)

    def set_focal(self, sliders: Gtk.Widget | None) -> None:
        """Show the focal length sliders, or hide the section."""
        while (child := self.focal_slot.get_first_child()) is not None:
            self.focal_slot.remove(child)
        if sliders is not None:
            self.focal_slot.append(sliders)
        self.focal_section.set_visible(sliders is not None)

    def show_rating(self, rating: int) -> None:
        """Fill the stars up to the minimum rating."""
        for count in range(1, MAX_RATING + 1):
            star: Gtk.Button = getattr(self, f"star_{count}")
            star.set_icon_name(
                "starred-symbolic"
                if count <= rating
                else "non-starred-symbolic"
            )
