"""The saved-recipe library UI: the manager dialog and its controller."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from grawji.fp_xml import parse_fp, serialize_fp
from grawji.recipe import Recipe
from grawji.recipes import UNGROUPED, RecipeLibrary
from grawji.views.recipe_panel import RecipePanel

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class RecipeManagerDialog(Adw.Dialog):
    """Manage saved recipes: folders, baseline, rename, export, delete."""

    __gtype_name__ = "GrawjiRecipeManagerDialog"

    import_button = Gtk.Template.Child()
    new_folder_button = Gtk.Template.Child()
    content = Gtk.Template.Child()
    stack = Gtk.Template.Child()

    def __init__(  # noqa: PLR0913
        self,
        *,
        library: RecipeLibrary,
        on_import: Callable[[], None],
        on_export: Callable[[str], None],
        on_delete: Callable[[str], None],
        on_rename: Callable[[str, str], None],
        on_move: Callable[[str, str], None],
        on_set_baseline: Callable[[str | None], None],
        on_place_recipe: Callable[[str, str, str | None], None],
        on_create_folder: Callable[[str], None],
        on_rename_folder: Callable[[str, str], None],
        on_delete_folder: Callable[[str], None],
        on_reorder_folder: Callable[[str, bool], None],
    ) -> None:
        """Wire the dialog to the library (read) and intent callbacks."""
        super().__init__()
        self._library = library
        self._on_import = on_import
        self._on_export = on_export
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._on_move = on_move
        self._on_set_baseline = on_set_baseline
        self._on_place_recipe = on_place_recipe
        self._on_create_folder = on_create_folder
        self._on_rename_folder = on_rename_folder
        self._on_delete_folder = on_delete_folder
        self._on_reorder_folder = on_reorder_folder
        self._dragged: str | None = None
        self._groups: list[Adw.PreferencesGroup] = []

        self.import_button.connect("clicked", lambda *_a: self._on_import())
        self.new_folder_button.connect("clicked", self._on_new_folder)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the grouped view from the current library state."""
        for group in self._groups:
            self.content.remove(group)
        self._groups = []

        has_recipes = bool(self._library.names)
        self.stack.set_visible_child_name("list" if has_recipes else "empty")
        if not has_recipes:
            return

        ungrouped = self._library.names_in(UNGROUPED)
        if ungrouped:
            self._add_group(UNGROUPED, "Recipes", ungrouped)
        for folder in self._library.folders():
            self._add_group(folder, folder, self._library.names_in(folder))

    def _add_group(self, folder: str, title: str, names: list[str]) -> None:
        """Add a titled folder section holding the given recipe rows."""
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        if folder != UNGROUPED:
            group.set_header_suffix(self._folder_header(folder))
        for name in names:
            group.add(self._recipe_row(name))
        # Dropping a recipe onto the section's empty area moves it here.
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_group_drop, folder)
        group.add_controller(drop)
        self.content.add(group)
        self._groups.append(group)

    def _recipe_row(self, name: str) -> Adw.ActionRow:
        """Build one recipe row: a baseline star and an overflow menu."""
        row = Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(name)

        star = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
        star.set_icon_name("starred-symbolic")
        star.set_tooltip_text("Use as compare baseline")
        star.add_css_class("flat")
        star.set_active(self._library.baseline == name)
        star.connect("toggled", self._on_star_toggled, name)
        row.add_prefix(star)

        menu = Gtk.MenuButton(
            icon_name="view-more-symbolic", valign=Gtk.Align.CENTER
        )
        menu.set_tooltip_text("Recipe actions")
        menu.add_css_class("flat")
        menu.set_popover(self._row_popover(name))
        row.add_suffix(menu)

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_recipe_drag, name)
        row.add_controller(source)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_recipe_drop, name)
        row.add_controller(drop)
        return row

    def _row_popover(self, name: str) -> Gtk.Popover:
        """The overflow menu for a recipe: move, rename, export, delete."""
        folder = self._library.folder_of(name)
        popover, box = self._popover()
        destinations = [
            f for f in [UNGROUPED, *self._library.folders()] if f != folder
        ]
        for dest in destinations:
            label = (
                "Move to Ungrouped"
                if dest == UNGROUPED
                else (f"Move to {dest}")
            )
            self._entry(box, popover, label, self._mover(name, dest))
        if destinations:
            self._separator(box)
        self._entry(box, popover, "Rename…", lambda: self._rename(name))
        self._entry(box, popover, "Export…", lambda: self._on_export(name))
        self._entry(
            box,
            popover,
            "Delete",
            lambda: self._on_delete(name),
            destructive=True,
        )
        return popover

    def _folder_header(self, folder: str) -> Gtk.Widget:
        """A folder header menu: reorder (up/down), rename, delete."""
        folders = self._library.folders()
        index = folders.index(folder)
        menu = Gtk.MenuButton(
            icon_name="view-more-symbolic", valign=Gtk.Align.CENTER
        )
        menu.add_css_class("flat")
        popover, box = self._popover()
        if index > 0:
            self._entry(
                box,
                popover,
                "Move Up",
                lambda: self._on_reorder_folder(folder, True),
            )
        if index < len(folders) - 1:
            self._entry(
                box,
                popover,
                "Move Down",
                lambda: self._on_reorder_folder(folder, False),
            )
        self._separator(box)
        self._entry(
            box,
            popover,
            "Rename Folder…",
            lambda: self._on_rename_folder_clicked(None, folder),
        )
        self._entry(
            box,
            popover,
            "Delete Folder",
            lambda: self._on_delete_folder(folder),
            destructive=True,
        )
        menu.set_popover(popover)
        return menu

    def _on_recipe_drag(
        self, _source: Any, _x: float, _y: float, name: str
    ) -> Gdk.ContentProvider:
        """Begin dragging a recipe row."""
        self._dragged = name
        return Gdk.ContentProvider.new_for_value(name)

    def _on_recipe_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, target: str
    ) -> bool:
        """Drop a recipe before target, adopting target's folder."""
        if self._dragged is not None and self._dragged != target:
            self._on_place_recipe(
                self._dragged, self._library.folder_of(target), target
            )
        self._dragged = None
        return True

    def _on_group_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, folder: str
    ) -> bool:
        """Drop a recipe onto a folder's area to append it there."""
        if self._dragged is not None:
            self._on_place_recipe(self._dragged, folder, None)
            self._dragged = None
            return True
        return False

    @staticmethod
    def _popover() -> tuple[Gtk.Popover, Gtk.Box]:
        """A popover holding a vertical button box."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        popover.set_child(box)
        return popover, box

    @staticmethod
    def _separator(box: Gtk.Box) -> None:
        """Append a thin separator to a popover box."""
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    @staticmethod
    def _entry(
        box: Gtk.Box,
        popover: Gtk.Popover,
        label: str,
        handler: Callable[[], None],
        *,
        destructive: bool = False,
    ) -> None:
        """Append a flat button that closes the popover then runs handler."""
        button = Gtk.Button(label=label)
        button.set_halign(Gtk.Align.FILL)
        button.get_first_child().set_halign(Gtk.Align.START)
        button.add_css_class("flat")
        if destructive:
            button.add_css_class("destructive-action")

        def on_clicked(*_a: Any) -> None:
            popover.popdown()
            handler()

        button.connect("clicked", on_clicked)
        box.append(button)

    def _mover(self, name: str, folder: str) -> Callable[[], None]:
        """A handler that moves a recipe into a folder."""
        return lambda: self._on_move(name, folder)

    def _on_star_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        """Set or clear the compare baseline from a row's star."""
        self._on_set_baseline(name if button.get_active() else None)

    def _rename(self, name: str) -> None:
        """Prompt for a new recipe name and rename."""
        self._prompt(
            "Rename recipe",
            "New name",
            name,
            lambda new: self._on_rename(name, new),
        )

    def _on_rename_folder_clicked(self, _button: Any, folder: str) -> None:
        """Prompt for a new folder name and rename it."""
        self._prompt(
            "Rename folder",
            "New name",
            folder,
            lambda new: self._on_rename_folder(folder, new),
        )

    def _on_new_folder(self, _button: Any) -> None:
        """Prompt for a folder name and create it."""
        self._prompt("New folder", "Folder name", "", self._on_create_folder)

    def _prompt(
        self,
        heading: str,
        body: str,
        preset: str,
        done: Callable[[str], None],
    ) -> None:
        """Show a one-entry text dialog; call done with the new value."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        entry = Gtk.Entry(text=preset)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d: Any, response: str) -> None:
            value = entry.get_text().strip()
            if response == "ok" and value and value != preset:
                done(value)

        dialog.connect("response", on_response)
        dialog.present(self)


class RecipeLibraryController:
    """Glue between the library, the panel and the library dialogs.

    Owns the manager dialog's lifecycle plus the save-name prompt and
    the FP1/FP2/FP3 import and export file dialogs, keeping the panel's
    saved-recipe combo in sync with every library change.
    """

    def __init__(  # noqa: PLR0913 - pure wiring, all keyword-only
        self,
        *,
        parent: Gtk.Widget,
        library: RecipeLibrary,
        panel: RecipePanel,
        on_render: Callable[[], None],
        on_status: Callable[[str], None],
        get_iopcode: Callable[[], int | None],
        on_baseline_changed: Callable[[], None] = lambda: None,
    ) -> None:
        """Wire the controller.

        Args:
            parent: The window the dialogs attach to.
            library: The saved-recipe store.
            panel: The recipe panel whose combo mirrors the library.
            on_render: Re-render the preview if an image is open.
            on_status: Sets the window's status line.
            get_iopcode: The open profile's IOPCode for FP export, or
                None when no image is open.
            on_baseline_changed: Called when the compare baseline is set
                or cleared, so the window can update the compare state.
        """
        self._parent = parent
        self._library = library
        self._panel = panel
        self._on_render = on_render
        self._on_status = on_status
        self._get_iopcode = get_iopcode
        self._on_baseline_changed = on_baseline_changed
        self._manager: RecipeManagerDialog | None = None
        self._refresh()

    def manage(self) -> None:
        """Open the recipe manager modal."""
        self._manager = RecipeManagerDialog(
            library=self._library,
            on_import=self.import_fp,
            on_export=self.export_fp,
            on_delete=self._delete,
            on_rename=self._rename,
            on_move=self._move,
            on_set_baseline=self._set_baseline,
            on_place_recipe=self._place_recipe,
            on_create_folder=self._create_folder,
            on_rename_folder=self._rename_folder,
            on_delete_folder=self._delete_folder,
            on_reorder_folder=self._reorder_folder,
        )
        self._manager.connect("closed", self._on_manager_closed)
        self._manager.present(self._parent)

    def save_current(self) -> None:
        """Ask for a name and save the panel's controls as a recipe."""
        self._prompt_save(self._panel.get_recipe())

    def apply(self, name: str) -> None:
        """Apply a saved recipe to the controls and re-render."""
        recipe = self._library.get(name)
        if recipe is None:
            return
        self._panel.set_recipe(recipe)
        self._on_render()
        self._on_status(f"Applied recipe “{name}”.")

    def import_fp(self) -> None:
        """Pick an X RAW Studio FP file and import its recipe."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import recipe")
        fp_filter = Gtk.FileFilter()
        fp_filter.set_name("X RAW Studio recipes (FP1/FP2/FP3)")
        for pattern in ("*.FP1", "*.FP2", "*.FP3", "*.fp1", "*.fp2", "*.fp3"):
            fp_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(fp_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(fp_filter)
        dialog.open(self._parent, None, self._on_import_response)

    def export_fp(self, name: str) -> None:
        """Pick a path and write the named saved recipe as an FP file."""
        recipe = self._library.get(name)
        if recipe is None:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Export recipe")
        dialog.set_initial_name(f"{name}.FP1")
        dialog.save(
            self._parent,
            None,
            lambda dlg, res: self._on_export_response(dlg, res, name, recipe),
        )

    def _refresh(self) -> None:
        """Mirror the library into the panel picker and an open manager.

        The picker lists ungrouped recipes at the top, then one submenu
        per folder that opens from its own entry.
        """
        ungrouped = self._library.names_in(UNGROUPED)
        folders = [
            (folder, self._library.names_in(folder))
            for folder in self._library.folders()
        ]
        self._panel.set_recipe_menu(ungrouped, folders)
        if self._manager is not None:
            self._manager.refresh()

    def _on_manager_closed(self, _dialog: Any) -> None:
        """Forget the manager once it is dismissed."""
        self._manager = None

    def _delete(self, name: str) -> None:
        """Remove a saved recipe and persist the change."""
        if self._library.delete(name):
            self._refresh()
            self._on_status(f"Deleted recipe “{name}”.")

    def _rename(self, old: str, new: str) -> None:
        """Rename a saved recipe, keeping its position, and persist."""
        if not self._library.rename(old, new):
            return
        self._refresh()
        if self._panel.active_label == old:
            renamed = self._library.get(new)
            if renamed is not None:
                self._panel.set_active(renamed, new)

    def _move(self, name: str, folder: str) -> None:
        """Move a recipe into a folder and refresh."""
        if self._library.move(name, folder):
            self._refresh()

    def _place_recipe(
        self, name: str, folder: str, before: str | None
    ) -> None:
        """Place a dragged recipe into folder before another, and refresh."""
        if self._library.place_recipe(name, folder, before):
            self._refresh()

    def _reorder_folder(self, folder: str, up: bool) -> None:
        """Nudge a folder up or down and refresh."""
        if self._library.reorder_folder(folder, up=up):
            self._refresh()

    def _set_baseline(self, name: str | None) -> None:
        """Mark (or clear) the compare baseline and notify the window."""
        if self._library.set_baseline(name):
            self._refresh()
            self._on_baseline_changed()

    def _create_folder(self, name: str) -> None:
        """Create a folder and refresh."""
        if self._library.create_folder(name):
            self._refresh()

    def _rename_folder(self, old: str, new: str) -> None:
        """Rename a folder and refresh."""
        if self._library.rename_folder(old, new):
            self._refresh()

    def _delete_folder(self, name: str) -> None:
        """Delete a folder (its recipes go ungrouped) and refresh."""
        if self._library.delete_folder(name):
            self._refresh()

    def _prompt_save(
        self, recipe: Recipe, default_name: str = "", *, activate: bool = False
    ) -> None:
        """Ask for a name, then store recipe under it and make it active.

        Args:
            recipe: The recipe to store.
            default_name: The name pre-filled in the entry.
            activate: Re-render the preview after saving (used for imports,
                where the saved recipe is new to the controls).
        """
        dialog = Adw.AlertDialog(
            heading="Save recipe", body="Name this recipe:"
        )
        entry = Gtk.Entry(text=default_name)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_default_response("save")
        dialog.set_response_appearance(
            "save", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect(
            "response", self._on_save_response, entry, recipe, activate
        )
        dialog.present(self._parent)

    def _on_save_response(
        self,
        _dialog: Any,
        response: str,
        entry: Any,
        recipe: Recipe,
        activate: bool,
    ) -> None:
        """Store the named recipe when the save dialog is confirmed."""
        if response != "save":
            return
        name = entry.get_text().strip()
        if not name:
            return
        self._library.add(name, recipe)
        self._refresh()
        self._panel.set_active(recipe, name)
        if activate:
            self._on_render()
        verb = "Imported" if activate else "Saved"
        self._on_status(f"{verb} recipe “{name}”.")

    def _on_import_response(self, dialog: Any, result: Any) -> None:
        """Parse the chosen FP file, then save it as a named recipe."""
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        try:
            recipe = parse_fp(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._on_status(f"Could not import recipe: {exc}")
            return
        self._prompt_save(recipe, Path(path).stem, activate=True)

    def _on_export_response(
        self, dialog: Any, result: Any, name: str, recipe: Recipe
    ) -> None:
        """Write the named recipe as an FP file to the chosen path."""
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        if path is None:
            return
        text = serialize_fp(recipe, iopcode=self._get_iopcode(), label=name)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._on_status(f"Could not export recipe: {exc}")
            return
        self._on_status(f"Exported recipe “{name}” to {path}.")
