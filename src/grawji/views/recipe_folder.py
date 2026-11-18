"""One folder of the recipe manager's list."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_folder.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class RecipeFolder(Gtk.Box):
    """A header that folds its recipe rows away, and the rows below it.

    Attributes:
        folder: The library folder shown.
        collapsed: Whether the user folded it away.
    """

    __gtype_name__ = "GrawjiRecipeFolder"

    header = Gtk.Template.Child()
    toggle_button = Gtk.Template.Child()
    chevron = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    count_label = Gtk.Template.Child()
    actions = Gtk.Template.Child()
    up_button = Gtk.Template.Child()
    down_button = Gtk.Template.Child()
    menu_popover = Gtk.Template.Child()
    rename_button = Gtk.Template.Child()
    delete_button = Gtk.Template.Child()
    rows = Gtk.Template.Child()
    drop_target = Gtk.Template.Child()

    def __init__(
        self,
        folder: str,
        title: str,
        *,
        collapsed: bool,
        on_toggled: Callable[[str, bool], None],
        **kwargs: Any,
    ) -> None:
        """Build the empty folder section."""
        super().__init__(**kwargs)
        self.folder = folder
        self.collapsed = collapsed
        self._forced_open = False
        self._count = 0
        self._on_toggled = on_toggled
        self.title_label.set_label(title)
        self.header.set_visible(bool(title))
        self.toggle_button.connect("clicked", self._on_clicked)
        self._update()

    @property
    def is_open(self) -> bool:
        """Whether the rows are on show."""
        return self._forced_open or not self.collapsed or not self.has_header

    @property
    def has_header(self) -> bool:
        """Whether there is a header to fold the rows with."""
        return bool(self.header.get_visible())

    def add(self, row: Gtk.Widget) -> None:
        """Append a recipe row."""
        self.rows.append(row)
        self._count += 1
        self._update()

    def enable_actions(
        self,
        *,
        can_move_up: bool,
        can_move_down: bool,
        on_move: Callable[[bool], None],
        on_rename: Callable[[], None],
        on_delete: Callable[[], None],
    ) -> None:
        """Offer to reorder, rename and delete the folder.

        Args:
            can_move_up: Whether a folder sits above this one.
            can_move_down: Whether a folder sits below this one.
            on_move: Called with True to move up, False to move down.
            on_rename: Called to rename the folder.
            on_delete: Called to delete the folder.
        """
        self.actions.set_visible(True)
        self.up_button.set_sensitive(can_move_up)
        self.down_button.set_sensitive(can_move_down)
        self.up_button.connect("clicked", lambda _b: on_move(True))
        self.down_button.connect("clicked", lambda _b: on_move(False))
        for button, handler in (
            (self.rename_button, on_rename),
            (self.delete_button, on_delete),
        ):
            button.connect("clicked", self._on_menu_clicked, handler)

    def _on_menu_clicked(
        self, _button: Any, handler: Callable[[], None]
    ) -> None:
        """Close the folder menu, then act."""
        self.menu_popover.popdown()
        handler()

    def set_collapsed(self, collapsed: bool) -> None:
        """Fold the rows away."""
        self.collapsed = collapsed
        self._update()

    def force_open(self, forced: bool) -> None:
        """Show the rows whatever the fold state."""
        self._forced_open = forced
        self._update()

    def _update(self) -> None:
        """Bring the chevron, the count and the rows in line."""
        is_open = self.is_open
        self.rows.set_visible(is_open and self._count > 0)
        self.chevron.set_from_icon_name(
            "pan-down-symbolic" if is_open else "pan-end-symbolic"
        )
        self.count_label.set_label(str(self._count))
        self.toggle_button.set_tooltip_text(
            "Hide the recipes of this folder"
            if is_open
            else "Show the recipes of this folder"
        )

    def _on_clicked(self, _button: Any) -> None:
        """Fold or unfold."""
        if self._forced_open:
            return
        self.set_collapsed(not self.collapsed)
        self._on_toggled(self.folder, self.collapsed)
