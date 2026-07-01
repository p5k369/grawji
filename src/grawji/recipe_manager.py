"""Modal dialog to manage saved recipes: reorder, delete, import, export."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)


class _RecipeItem(GObject.Object):
    """A recipe name in the list model (name is a GObject property)."""

    __gtype_name__ = "GrawjiRecipeItem"

    name = GObject.Property(type=str, default="")

    def __init__(self, name: str) -> None:
        """Wrap a recipe name for the list store."""
        super().__init__()
        self.name = name


@Gtk.Template(string=_UI)
class RecipeManagerDialog(Adw.Dialog):
    """Reorder (live drag and drop), delete, import and export recipes."""

    __gtype_name__ = "GrawjiRecipeManagerDialog"

    import_button = Gtk.Template.Child()
    recipe_list = Gtk.Template.Child()
    stack = Gtk.Template.Child()

    def __init__(
        self,
        *,
        list_recipes: Callable[[], list[str]],
        on_reorder: Callable[[list[str]], None],
        on_delete: Callable[[str], None],
        on_rename: Callable[[str, str], None],
        on_import: Callable[[], None],
        on_export: Callable[[str], None],
    ) -> None:
        """Wire the dialog to its data source and intent callbacks.

        Args:
            list_recipes: Returns the saved recipe names, in display order.
            on_reorder: Called with the full new order after a drag-drop.
            on_delete: Called with a name to delete that recipe.
            on_rename: Called with (old_name, new_name) to rename a recipe.
            on_import: Called to start importing a recipe.
            on_export: Called with a name to export that recipe.
        """
        super().__init__()
        self._list_recipes = list_recipes
        self._on_reorder = on_reorder
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._on_import = on_import
        self._on_export = on_export
        self._drag_item: _RecipeItem | None = None

        self._store = Gio.ListStore.new(_RecipeItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        self.recipe_list.set_model(Gtk.NoSelection(model=self._store))
        self.recipe_list.set_factory(factory)

        self.import_button.connect("clicked", lambda *_a: self._on_import())
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list model from the current saved recipes."""
        names = self._list_recipes()
        self._store.remove_all()
        for name in names:
            self._store.append(_RecipeItem(name))
        self.stack.set_visible_child_name("list" if names else "empty")

    def _on_setup(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Build a recipe row: drag handle, name, export and delete."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        label = Gtk.Label(xalign=0.0, hexpand=True)
        rename = self._icon_button("document-edit-symbolic", "Rename…")
        rename.connect("clicked", self._on_row_rename, item)
        export = self._icon_button("document-save-symbolic", "Export…")
        export.connect("clicked", self._on_row_export, item)
        delete = self._icon_button("user-trash-symbolic", "Delete")
        delete.add_css_class("destructive-action")
        delete.connect("clicked", self._on_row_delete, item)
        for child in (handle, label, rename, export, delete):
            box.append(child)
        box.label = label
        item.set_child(box)
        self._add_drag_and_drop(box, item)

    @staticmethod
    def _on_bind(_factory: Any, item: Gtk.ListItem) -> None:
        """Show the bound recipe's name in the row."""
        item.get_child().label.set_text(item.get_item().name)

    def _add_drag_and_drop(self, box: Gtk.Widget, item: Gtk.ListItem) -> None:
        """Make the row draggable and a live drop point for reordering."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, item)
        source.connect("drag-begin", self._on_drag_begin)
        source.connect("drag-end", self._on_drag_end)
        box.add_controller(source)
        drop = Gtk.DropTarget.new(_RecipeItem, Gdk.DragAction.MOVE)
        drop.connect("enter", self._on_drop_enter, item)
        drop.connect("drop", self._on_drop)
        box.add_controller(drop)

    def _on_drag_prepare(
        self, _source: Any, _x: float, _y: float, item: Gtk.ListItem
    ) -> Gdk.ContentProvider:
        """Remember the dragged recipe and carry it as the payload."""
        self._drag_item = item.get_item()
        return Gdk.ContentProvider.new_for_value(self._drag_item)

    @staticmethod
    def _on_drag_begin(_source: Any, drag: Any) -> None:
        """Suppress the drag icon: the row itself moves live in the list."""
        Gtk.DragIcon.get_for_drag(drag).set_child(Gtk.Box())

    def _on_drag_end(self, _source: Any, _drag: Any, _delete: bool) -> None:
        """Clear the drag state when the drag finishes."""
        self._drag_item = None

    def _on_drop_enter(
        self, _target: Any, _x: float, _y: float, item: Gtk.ListItem
    ) -> Gdk.DragAction:
        """Live-move the dragged recipe to the row being hovered."""
        self._move_dragged_to(item.get_position())
        return Gdk.DragAction.MOVE

    def _on_drop(
        self, _target: Any, _value: Any, _x: float, _y: float
    ) -> bool:
        """Commit the reordered list when the recipe is dropped."""
        order = [
            self._store.get_item(i).name
            for i in range(self._store.get_n_items())
        ]
        self._drag_item = None
        self._on_reorder(order)
        return True

    def _move_dragged_to(self, dest: int) -> None:
        """Move the in-flight dragged item to position dest in the store."""
        if self._drag_item is None:
            return
        found, src = self._store.find(self._drag_item)
        if not found or src == dest:
            return
        self._store.remove(src)
        dest = min(dest, self._store.get_n_items())
        self._store.insert(dest, self._drag_item)

    def _on_row_export(self, _button: Any, item: Gtk.ListItem) -> None:
        """Export the recipe of the clicked row."""
        self._on_export(item.get_item().name)

    def _on_row_delete(self, _button: Any, item: Gtk.ListItem) -> None:
        """Delete the recipe of the clicked row."""
        self._on_delete(item.get_item().name)

    def _on_row_rename(self, _button: Any, item: Gtk.ListItem) -> None:
        """Prompt for a new name and rename the clicked recipe."""
        old = item.get_item().name
        entry = Gtk.Entry(text=old)
        dialog = Adw.AlertDialog(heading="Rename recipe", body="New name:")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_default_response("rename")
        dialog.set_response_appearance(
            "rename", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect("response", self._on_rename_response, old, entry)
        dialog.present(self)

    def _on_rename_response(
        self, _dialog: Any, response: str, old: str, entry: Gtk.Entry
    ) -> None:
        """Apply the rename when the dialog is confirmed."""
        if response != "rename":
            return
        new = entry.get_text().strip()
        if new and new != old:
            self._on_rename(old, new)

    @staticmethod
    def _icon_button(icon: str, tooltip: str) -> Gtk.Button:
        """Create a flat, vertically-centred icon button."""
        button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text(tooltip)
        return button
