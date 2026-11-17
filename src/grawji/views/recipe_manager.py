"""The recipe manager dialog."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any, Protocol

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from grawji.camera import compatibility as compat
from grawji.recipe import Recipe
from grawji.recipe_dedup import find_duplicate_recipes
from grawji.recipe_text import film_sim_label
from grawji.recipes import HOTKEYS, UNGROUPED, RecipeLibrary, recipe_matches
from grawji.views.camera_pane import CameraPane, CarrierHooks
from grawji.views.recipe_details import DetailActions, RecipeDetails, Shown
from grawji.views.recipe_thumb import RecipeThumb, thumb_texture

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)

_ = (CameraPane, RecipeDetails)

_SELECTED_CSS = "recipe-selected"
_DOUBLE_CLICK = 2


class RecipeManagerHost(Protocol):
    """The controller surface the manager dialog drives."""

    get_capabilities: Callable[[], Any] | None
    get_model: Callable[[], str | None] | None
    load_bank_names: (
        Callable[[Callable[[str | None, list[str]], None]], None] | None
    )
    carriers_for: Callable[[str], dict[int, str]] | None
    add_carrier: Callable[[str, str], str | None] | None
    forget_carrier: Callable[[str, int], None] | None
    current_raf: Callable[[], str | None] | None

    def apply(self, name: str) -> None:
        """Apply the named recipe to the open image."""

    def import_files(self, folder: str) -> None:
        """Pick recipe files and store them in a folder."""

    def import_folder(self, folder: str) -> None:
        """Pick a directory and store every recipe below it."""

    def import_clipboard(self, folder: str) -> None:
        """Store the recipe text on the clipboard in a folder."""

    def export_recipes(self, names: list[str]) -> None:
        """Export the named recipes as FP files."""

    def delete_recipes(self, names: list[str]) -> None:
        """Delete the named recipes."""

    def move_recipes(self, names: list[str], folder: str) -> None:
        """Move the named recipes into a folder ("" for ungrouped)."""

    def copy_recipe_text(self, name: str) -> None:
        """Copy the named recipe to the clipboard as text."""

    def clear_recipe_thumb(self, name: str) -> None:
        """Remove the recipe's stored thumbnail."""

    def rename_recipe(self, old: str, new: str) -> None:
        """Rename a recipe."""

    def set_baseline(self, name: str | None) -> None:
        """Set or clear the compare baseline."""

    def set_hotkey(self, name: str, key: int | None) -> None:
        """Assign a number key to a recipe, or clear it with None."""

    def place_recipe(self, name: str, folder: str, before: str | None) -> None:
        """Position a recipe in a folder, before another recipe."""

    def create_folder(self, name: str) -> None:
        """Create a folder."""

    def rename_folder(self, old: str, new: str) -> None:
        """Rename a folder."""

    def delete_folder(self, name: str) -> None:
        """Delete a folder (members return to ungrouped)."""

    def reorder_folder(self, name: str, up: bool) -> None:
        """Nudge a folder one step up or down."""

    def transfer_banks(
        self,
        recipes: dict[int, str],
        names: dict[int, str],
        fs_recipes: dict[int, str],
    ) -> None:
        """Write the assigned recipes to the camera's banks/FS dial."""

    def render_recipe_thumb(self, name: str) -> None:
        """Render the recipe's thumbnail against the open image."""

    def edit_comment(self, name: str) -> None:
        """Prompt for the recipe's hover comment."""


@Gtk.Template(string=_UI)
class RecipeManagerDialog(Adw.Dialog):
    """Manage saved recipes: pick some, look at one, load the camera."""

    __gtype_name__ = "GrawjiRecipeManagerDialog"

    toasts = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    import_button = Gtk.Template.Child()
    new_folder_button = Gtk.Template.Child()
    selection_label = Gtk.Template.Child()
    move_button = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    delete_button = Gtk.Template.Child()
    content = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    details = Gtk.Template.Child()
    camera_pane = Gtk.Template.Child()

    def __init__(self, *, library: RecipeLibrary, host: RecipeManagerHost):
        """Wire the dialog to the library (read) and its intent host."""
        super().__init__()
        self._library = library
        self._host = host
        self._caps = host.get_capabilities() if host.get_capabilities else None
        self._dragged: str | None = None
        self._groups: list[Adw.PreferencesGroup] = []
        self._search_rows: list[
            tuple[Adw.PreferencesGroup, Adw.ActionRow, str]
        ] = []
        self._rows: dict[str, Adw.ActionRow] = {}
        self._order: list[str] = []
        self._selected: list[str] = []
        self._anchor: str | None = None
        self._toast: Adw.Toast | None = None
        self._thumb_textures: dict[str, Any] = {}
        self.transfer_button = self.camera_pane.transfer_button

        self.new_folder_button.connect("clicked", self._on_new_folder)
        self.search_entry.connect("search-changed", self._apply_search)
        self.transfer_button.connect("clicked", self._on_transfer_clicked)
        self.export_button.connect("clicked", self._on_export_clicked)
        self.delete_button.connect("clicked", self._on_delete_clicked)
        self.import_button.set_popover(self._import_popover())
        self.details.set_actions(
            DetailActions(
                apply=self._apply,
                rename=self._rename,
                comment=host.edit_comment,
                picture=host.render_recipe_thumb,
                clear_picture=host.clear_recipe_thumb,
                copy_text=host.copy_recipe_text,
                baseline=self._set_baseline,
                hotkey=host.set_hotkey,
                target=self._assign_target,
            )
        )
        self.camera_pane.wire(
            library=library,
            caps=self._caps,
            get_model=host.get_model,
            load_bank_names=host.load_bank_names,
            take_dragged=self._take_dragged,
            carrier=(
                CarrierHooks(
                    host.carriers_for,
                    host.add_carrier,
                    host.forget_carrier,
                    host.current_raf,
                )
                if host.carriers_for
                and host.add_carrier
                and host.forget_carrier
                and host.current_raf
                else None
            ),
        )
        self.transfer_button.set_sensitive(self.camera_pane.refresh())
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the grouped view from the current library state."""
        for group in self._groups:
            self.content.remove(group)
        self._groups = []
        self._search_rows = []
        self._rows = {}
        self._order = []
        self._thumb_textures = {}
        self._duplicates = self._duplicate_map()
        self.move_button.set_popover(self._move_popover())

        has_recipes = bool(self._library.names)
        self.stack.set_visible_child_name("list" if has_recipes else "empty")
        ungrouped = self._library.names_in(UNGROUPED)
        if ungrouped:
            self._add_group(UNGROUPED, "Recipes", ungrouped)
        for folder in self._library.folders():
            self._add_group(folder, folder, self._library.names_in(folder))
        self._apply_search()
        self.select_names([n for n in self._selected if n in self._rows])

    def _add_group(self, folder: str, title: str, names: list[str]) -> None:
        """Add a titled folder section holding the given recipe rows."""
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        if folder != UNGROUPED:
            group.set_header_suffix(self._folder_header(folder))
        for name in names:
            row = self._recipe_row(name)
            group.add(row)
            self._rows[name] = row
            self._order.append(name)
            self._search_rows.append((group, row, self._search_text(name)))
        # Dropping a recipe onto the section's empty area moves it here.
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_group_drop, folder)
        group.add_controller(drop)
        self.content.append(group)
        self._groups.append(group)

    def _search_text(self, name: str) -> str:
        """The text a recipe is searched by."""
        recipe = self._library.get(name)
        film_sim = recipe.film_simulation if recipe is not None else ""
        return f"{name} {self._library.comment(name)} {film_sim}"

    def _apply_search(self, *_args: object) -> None:
        """Show only recipes and folders matching the search entry."""
        query = self.search_entry.get_text()
        visible_in: dict[Adw.PreferencesGroup, bool] = dict.fromkeys(
            self._groups, False
        )
        for group, row, text in self._search_rows:
            match = recipe_matches(query, text)
            row.set_visible(match)
            visible_in[group] = visible_in[group] or match
        for group, has_match in visible_in.items():
            group.set_visible(has_match or not query.strip())

    def _recipe_row(self, name: str) -> Adw.ActionRow:
        """Build one recipe row: picture, name and badges."""
        row = Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(name)
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        recipe = self._library.get(name)
        if recipe is not None:
            parts = [film_sim_label(recipe.film_simulation)]
            if recipe.origin_body:
                parts.append(f"from {recipe.origin_body}")
            row.set_subtitle(" · ".join(parts))
        comment = self._library.comment(name)
        if comment:
            row.set_tooltip_text(comment)

        thumb = RecipeThumb(valign=Gtk.Align.CENTER)
        thumb.set_texture(self._thumb_texture(name))
        row.add_prefix(thumb)

        if self._library.baseline == name:
            row.add_suffix(
                self._chip(
                    "baseline",
                    "accent",
                    "The split view compares against this recipe",
                )
            )
        if recipe is not None and self._caps is not None:
            badge = self._fit_badge(recipe)
            if badge is not None:
                row.add_suffix(badge)
        twins = self._duplicates.get(name)
        if twins:
            joined = ", ".join(twins)
            row.add_suffix(
                self._chip("duplicate", "dim-label", f"Same look as: {joined}")
            )
        hotkey = self._library.hotkey_of(name)
        if hotkey is not None:
            row.add_suffix(
                self._chip(
                    f"key {hotkey}",
                    "accent",
                    f"Press {hotkey} in the main window to apply this recipe",
                )
            )

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_row_pressed, name)
        row.add_controller(click)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_recipe_drag, name)
        source.connect("drag-begin", self._on_drag_begin, name)
        row.add_controller(source)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_recipe_drop, name)
        row.add_controller(drop)
        return row

    @staticmethod
    def _chip(label: str, css: str, tooltip: str) -> Gtk.Widget:
        """A small caption chip for a row's suffix."""
        chip = Gtk.Label(label=label, valign=Gtk.Align.CENTER)
        chip.add_css_class("caption")
        chip.add_css_class(css)
        chip.set_tooltip_text(tooltip)
        return chip

    def _on_row_pressed(
        self, gesture: Any, n_press: int, _x: float, _y: float, name: str
    ) -> None:
        """Select the clicked recipe, or apply it on a double click."""
        state = gesture.get_current_event_state()
        toggle = bool(state & Gdk.ModifierType.CONTROL_MASK)
        extend = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if n_press == _DOUBLE_CLICK and not (toggle or extend):
            self._apply(name)
            return
        self.select(name, toggle=toggle, extend=extend)

    def select(
        self, name: str, *, toggle: bool = False, extend: bool = False
    ) -> None:
        """Select a recipe the way a click with Ctrl or Shift does."""
        order = self._order
        if extend and self._anchor in order and name in order:
            start, end = sorted((order.index(self._anchor), order.index(name)))
            chosen = order[start : end + 1]
        elif toggle:
            chosen = [n for n in self._selected if n != name]
            if name not in self._selected:
                chosen.append(name)
            self._anchor = name
        else:
            chosen = [name]
            self._anchor = name
        self.select_names(chosen)

    def select_names(self, names: list[str]) -> None:
        """Make exactly these recipes the selection."""
        self._selected = [n for n in self._order if n in set(names)]
        for name, row in self._rows.items():
            if name in self._selected:
                row.add_css_class(_SELECTED_CSS)
            else:
                row.remove_css_class(_SELECTED_CSS)
        count = len(self._selected)
        for widget in (self.move_button, self.export_button):
            widget.set_sensitive(count > 0)
        self.delete_button.set_sensitive(count > 0)
        self.selection_label.set_label(
            f"{count} selected" if count > 1 else ""
        )
        self._show_details()

    @property
    def selected(self) -> list[str]:
        """The selected recipes, in list order."""
        return list(self._selected)

    def _show_details(self) -> None:
        """Show the one selected recipe in the middle pane."""
        name = self._selected[0] if len(self._selected) == 1 else None
        recipe = self._library.get(name) if name else None
        if name is None or recipe is None:
            self.details.show_none(len(self._selected))
            return
        fit, fit_css = "", ""
        if self._caps is not None:
            verdict = compat.evaluate(recipe, self._caps)
            if verdict.level != compat.FULL:
                fit = "On the connected body: " + ", ".join(verdict.issues)
                unavailable = verdict.level == compat.UNAVAILABLE
                fit_css = "error" if unavailable else "warning"
        holders = {
            key: holder
            for key in HOTKEYS
            if (holder := self._library.recipe_for_hotkey(key)) is not None
        }
        self.details.show_recipe(
            Shown(
                name=name,
                recipe=recipe,
                texture=self._thumb_texture(name),
                comment=self._library.comment(name),
                hotkey=self._library.hotkey_of(name),
                key_holders=holders,
                is_baseline=self._library.baseline == name,
                fit=fit,
                fit_css=fit_css,
                targets=[label for label, _ in self.camera_pane.targets()],
            )
        )

    def _apply(self, name: str) -> None:
        """Apply a recipe to the open image and say so."""
        self._host.apply(name)
        self.show_toast(f"Applied “{name}”.")

    def _set_baseline(self, name: str, active: bool) -> None:
        """Set or clear the compare baseline."""
        self._host.set_baseline(name if active else None)

    def _assign_target(self, name: str, index: int) -> None:
        """Put a recipe into the camera slot of a details button."""
        targets = self.camera_pane.targets()
        if index < len(targets):
            targets[index][1](name)

    def _target_folder(self) -> str:
        """Where an import lands: beside the selection, else ungrouped."""
        if self._selected:
            return self._library.folder_of(self._selected[0])
        return UNGROUPED

    def _import_popover(self) -> Gtk.Popover:
        """The import menu: files, a whole folder, clipboard text."""
        popover, box = self._popover()
        self._entry(
            box,
            popover,
            "Recipe files or photos…",
            lambda: self._host.import_files(self._target_folder()),
        )
        self._entry(
            box,
            popover,
            "A folder of recipes…",
            lambda: self._host.import_folder(self._target_folder()),
        )
        self._entry(
            box,
            popover,
            "Recipe text from the clipboard",
            lambda: self._host.import_clipboard(self._target_folder()),
        )
        return popover

    def _move_popover(self) -> Gtk.Popover:
        """The folders the selection can move into."""
        popover, box = self._popover()
        for folder in [UNGROUPED, *self._library.folders()]:
            self._entry(
                box,
                popover,
                "Ungrouped" if folder == UNGROUPED else folder,
                self._mover(folder),
            )
        return popover

    def _mover(self, folder: str) -> Callable[[], None]:
        """A handler that moves the selection into a folder."""
        return lambda: self._host.move_recipes(self.selected, folder)

    def _on_export_clicked(self, _button: Any) -> None:
        """Export the selection."""
        if self._selected:
            self._host.export_recipes(self.selected)

    def _on_delete_clicked(self, _button: Any) -> None:
        """Delete the selection after a confirmation."""
        names = self.selected
        if not names:
            return
        if len(names) == 1:
            heading = f"Delete “{names[0]}”?"
        else:
            heading = f"Delete {len(names)} recipes?"
        dialog = Adw.AlertDialog(
            heading=heading, body="This cannot be undone."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance(
            "delete", Adw.ResponseAppearance.DESTRUCTIVE
        )

        def on_response(_d: Any, response: str) -> None:
            if response == "delete":
                self._host.delete_recipes(names)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _thumb_texture(self, name: str) -> Any:
        """The recipe's cached thumbnail texture, or None."""
        if name not in self._thumb_textures:
            self._thumb_textures[name] = thumb_texture(self._library, name)
        return self._thumb_textures[name]

    def _fit_badge(self, recipe: Recipe) -> Gtk.Widget | None:
        """A compatibility chip for the connected body, or None if full fit."""
        if self._caps is None:
            return None
        verdict = compat.evaluate(recipe, self._caps)
        if verdict.level == compat.FULL:
            return None
        if verdict.level == compat.UNAVAILABLE:
            label, css = "sim N/A", "error"
        else:
            label, css = f"{len(verdict.issues)} dropped", "warning"
        return self._chip(label, css, "\n".join(verdict.issues))

    def _duplicate_map(self) -> dict[str, list[str]]:
        """Map each recipe to the others sharing its look."""
        recipes = {
            name: recipe
            for name in self._library.names
            if (recipe := self._library.get(name)) is not None
        }
        mapping: dict[str, list[str]] = {}
        for group in find_duplicate_recipes(recipes):
            for name in group:
                mapping[name] = [other for other in group if other != name]
        return mapping

    def set_busy(self, busy: bool) -> None:
        """Disable Transfer while a transfer runs."""
        self.transfer_button.set_sensitive(not busy)

    def show_toast(self, message: str) -> None:
        """Show an in-dialog toast, replacing any previous one."""
        if self._toast is not None:
            self._toast.dismiss()
        self._toast = Adw.Toast.new(message)
        self.toasts.add_toast(self._toast)

    def on_transfer_finished(self) -> None:
        """Refresh the bank pane after a transfer (reload names, clear)."""
        self.camera_pane.on_transfer_finished()

    def set_bank_names(self, model: str | None, names: list[str]) -> None:
        """Forward the loaded body model and bank names to the pane."""
        self.camera_pane.set_bank_names(model, names)
        self._show_details()

    def _take_dragged(self) -> str | None:
        """Return and consume the recipe row currently being dragged."""
        name, self._dragged = self._dragged, None
        return name

    def _on_transfer_clicked(self, _button: Any) -> None:
        """Hand the bank/FS assignments and renames to the controller."""
        recipes, names, fs_recipes = self.camera_pane.collect()
        if recipes or fs_recipes or names:
            self._host.transfer_banks(recipes, names, fs_recipes)

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
                lambda: self._host.reorder_folder(folder, True),
            )
        if index < len(folders) - 1:
            self._entry(
                box,
                popover,
                "Move Down",
                lambda: self._host.reorder_folder(folder, False),
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
            lambda: self._host.delete_folder(folder),
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

    def _on_drag_begin(self, _source: Any, drag: Any, name: str) -> None:
        """Attach a compact recipe chip as the drag icon."""
        chip = Gtk.Box()
        chip.add_css_class("card")
        label = Gtk.Label(
            label=name,
            margin_top=8,
            margin_bottom=8,
            margin_start=14,
            margin_end=14,
        )
        label.add_css_class("heading")
        chip.append(label)
        icon = Gtk.DragIcon.get_for_drag(drag)
        icon.set_child(chip)
        drag.set_hotspot(-8, -8)

    def _on_recipe_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, target: str
    ) -> bool:
        """Drop a recipe before target, adopting target's folder."""
        if self._dragged is not None and self._dragged != target:
            self._host.place_recipe(
                self._dragged, self._library.folder_of(target), target
            )
        self._dragged = None
        return True

    def _on_group_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, folder: str
    ) -> bool:
        """Drop a recipe onto a folder's area to append it there."""
        if self._dragged is not None:
            self._host.place_recipe(self._dragged, folder, None)
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

    def _rename(self, name: str) -> None:
        """Prompt for a new recipe name and rename."""
        self._prompt(
            "Rename recipe",
            "New name",
            name,
            lambda new: self._renamed(name, new),
        )

    def _renamed(self, old: str, new: str) -> None:
        """Rename a recipe and keep it selected."""
        self._host.rename_recipe(old, new)
        if new in self._rows:
            self.select_names([new])

    def _on_rename_folder_clicked(self, _button: Any, folder: str) -> None:
        """Prompt for a new folder name and rename it."""
        self._prompt(
            "Rename folder",
            "New name",
            folder,
            lambda new: self._host.rename_folder(folder, new),
        )

    def _on_new_folder(self, _button: Any) -> None:
        """Prompt for a folder name and create it."""
        self._prompt("New folder", "Folder name", "", self._host.create_folder)

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
