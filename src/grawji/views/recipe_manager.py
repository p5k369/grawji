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
from grawji.recipe_text import recipe_summary
from grawji.recipes import HOTKEYS, UNGROUPED, RecipeLibrary, recipe_matches
from grawji.views.camera_pane import CameraPane, CarrierHooks
from grawji.views.recipe_details import DetailActions, RecipeDetails, Shown
from grawji.views.recipe_folder import RecipeFolder
from grawji.views.recipe_thumb import RecipeThumb, thumb_texture

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)

_ = (CameraPane, RecipeDetails)

_SELECTED_CSS = "recipe-selected"
_DOUBLE_CLICK = 2
_SCROLL_EDGE = 56
_SCROLL_STEP = 18.0
_ROW_THUMB = (54, 36)
_UNDO_SECONDS = 8


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

    def apply_saved(self, name: str) -> None:
        """Apply the named recipe to the open image and remember it."""

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

    def place_recipes(
        self, names: list[str], folder: str, before: str | None
    ) -> None:
        """Position recipes in a folder, before another recipe."""

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

    def render_missing_thumbs(self, names: list[str]) -> None:
        """Render a picture for each named recipe, one after another."""

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
    pictures_button = Gtk.Template.Child()
    bulk_box = Gtk.Template.Child()
    selection_label = Gtk.Template.Child()
    move_button = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    delete_button = Gtk.Template.Child()
    list_scroll = Gtk.Template.Child()
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
        self._dragged: list[str] = []
        self._cursor: str | None = None
        self._groups: list[RecipeFolder] = []
        self._search_rows: list[tuple[RecipeFolder, Adw.ActionRow, str]] = []
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
        self.pictures_button.connect("clicked", self._on_pictures_clicked)
        self.import_button.set_popover(self._import_popover())
        self._scroll_speed = 0.0
        self._scroll_tick: int | None = None
        motion = Gtk.DropControllerMotion()
        motion.connect("motion", self._on_drag_motion)
        motion.connect("leave", self._on_drag_leave)
        self.list_scroll.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_list_key)
        self.list_scroll.add_controller(keys)
        find = Gtk.EventControllerKey()
        find.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        find.connect("key-pressed", self._on_dialog_key)
        self.add_controller(find)
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
        self.camera_pane.refresh()
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
            title = "Ungrouped" if self._library.folders() else ""
            self._add_group(UNGROUPED, title, ungrouped)
        for folder in self._library.folders():
            self._add_group(folder, folder, self._library.names_in(folder))
        self._apply_search()
        self._refresh_pictures_button()
        self.select_names([n for n in self._selected if n in self._rows])

    def _add_group(self, folder: str, title: str, names: list[str]) -> None:
        """Add a titled folder section holding the given recipe rows."""
        group = RecipeFolder(
            folder,
            title,
            collapsed=self._library.is_collapsed(folder),
            on_toggled=self._on_folder_toggled,
        )
        if folder != UNGROUPED:
            group.set_suffix(self._folder_header(folder))
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

    def _on_folder_toggled(self, folder: str, collapsed: bool) -> None:
        """Remember the fold, and let go of the recipes it hides."""
        self._library.set_collapsed(folder, collapsed)
        if collapsed:
            hidden = set(self._library.names_in(folder))
            self.select_names([n for n in self._selected if n not in hidden])

    def _shown(self) -> list[str]:
        """The recipes on show, in list order."""
        hidden: set[str] = set()
        for group in self._groups:
            if not group.is_open:
                hidden.update(self._library.names_in(group.folder))
        return [
            name
            for name in self._order
            if name not in hidden and self._rows[name].get_visible()
        ]

    def _search_text(self, name: str) -> str:
        """The text a recipe is searched by."""
        recipe = self._library.get(name)
        film_sim = recipe.film_simulation if recipe is not None else ""
        return f"{name} {self._library.comment(name)} {film_sim}"

    def _apply_search(self, *_args: object) -> None:
        """Show only recipes and folders matching the search entry."""
        query = self.search_entry.get_text()
        searching = bool(query.strip())
        visible_in: dict[RecipeFolder, bool] = dict.fromkeys(
            self._groups, False
        )
        for group, row, text in self._search_rows:
            match = recipe_matches(query, text)
            row.set_visible(match)
            visible_in[group] = visible_in[group] or match
        for group, has_match in visible_in.items():
            group.set_visible(has_match or not searching)
            group.force_open(searching)
        if self._library.names:
            found = any(visible_in.values()) or not searching
            self.stack.set_visible_child_name("list" if found else "nomatch")

    def _recipe_row(self, name: str) -> Adw.ActionRow:
        """Build one recipe row: picture, name and badges."""
        row = Adw.ActionRow()
        row.add_css_class("recipe-row")
        row.set_use_markup(False)
        row.set_title(name)
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        recipe = self._library.get(name)
        if recipe is not None:
            row.set_subtitle(recipe_summary(recipe))
        comment = self._library.comment(name)
        if comment:
            row.set_tooltip_text(comment)

        thumb = RecipeThumb(
            valign=Gtk.Align.CENTER,
            tile_width=_ROW_THUMB[0],
            tile_height=_ROW_THUMB[1],
        )
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
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._on_row_menu, name)
        row.add_controller(menu)
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
        self._rows[name].grab_focus()

    def _on_dialog_key(
        self, _keys: Any, keyval: int, _code: int, state: Gdk.ModifierType
    ) -> bool:
        """Jump into the search entry."""
        if keyval == Gdk.KEY_f and state & Gdk.ModifierType.CONTROL_MASK:
            self.search_entry.grab_focus()
            return True
        return False

    def _on_row_menu(
        self, _gesture: Any, _n_press: int, x: float, y: float, name: str
    ) -> None:
        """Open the context menu of the clicked recipe or the selection."""
        if name not in self._selected:
            self.select(name)
        popover, box = self._popover()
        for label, handler, destructive in self._menu_entries():
            self._entry(box, popover, label, handler, destructive=destructive)
        spot = Gdk.Rectangle()
        spot.x, spot.y, spot.width, spot.height = int(x), int(y), 1, 1
        popover.set_parent(self._rows[name])
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        popover.set_pointing_to(spot)
        popover.connect("closed", self._on_menu_closed)
        popover.popup()

    @staticmethod
    def _on_menu_closed(popover: Gtk.Popover) -> None:
        """Drop a context menu once it has closed."""
        GLib.idle_add(popover.unparent)

    def _menu_entries(self) -> list[tuple[str, Callable[[], None], bool]]:
        """What the context menu offers for the selection."""
        names = self.selected
        entries: list[tuple[str, Callable[[], None], bool]] = []
        if len(names) == 1:
            name = names[0]
            entries.append(
                ("Apply to image", lambda: self._apply(name), False)
            )
            entries.append(("Rename…", lambda: self._rename(name), False))
        here = {self._library.folder_of(name) for name in names}
        for folder in [UNGROUPED, *self._library.folders()]:
            if here == {folder}:
                continue
            target = "Ungrouped" if folder == UNGROUPED else f"“{folder}”"
            entries.append((f"Move to {target}", self._mover(folder), False))
        entries.append(
            ("Export…", lambda: self._on_export_clicked(None), False)
        )
        entries.append(("Delete", lambda: self._on_delete_clicked(None), True))
        return entries

    def _on_list_key(
        self, _keys: Any, keyval: int, _code: int, state: Gdk.ModifierType
    ) -> bool:
        """Drive the selection from the keyboard while a recipe has focus."""
        if self.get_focus() not in self._rows.values():
            return False
        shown = self._shown()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_a and ctrl:
            self.select_names(shown)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if len(self._selected) == 1:
                self._apply(self._selected[0])
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._on_delete_clicked(None)
            return True
        last = len(shown) - 1
        at = shown.index(self._cursor) if self._cursor in shown else 0
        target = {
            Gdk.KEY_Up: max(at - 1, 0),
            Gdk.KEY_Down: min(at + 1, last),
            Gdk.KEY_Home: 0,
            Gdk.KEY_End: last,
        }.get(keyval)
        if target is None or not shown:
            return False
        extend = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self.select(shown[target], extend=extend)
        self._rows[shown[target]].grab_focus()
        return True

    def select(
        self, name: str, *, toggle: bool = False, extend: bool = False
    ) -> None:
        """Select a recipe the way a click with Ctrl or Shift does."""
        order = self._shown()
        self._cursor = name
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
        folders = {self._library.folder_of(n) for n in self._selected}
        for group in self._groups:
            if group.folder in folders and not group.is_open:
                group.set_collapsed(False)
                self._library.set_collapsed(group.folder, False)
        for name, row in self._rows.items():
            if name in self._selected:
                row.add_css_class(_SELECTED_CSS)
            else:
                row.remove_css_class(_SELECTED_CSS)
        count = len(self._selected)
        for widget in (self.move_button, self.export_button):
            widget.set_sensitive(count > 0)
        self.delete_button.set_sensitive(count > 0)
        self.bulk_box.set_visible(count > 0)
        self.camera_pane.set_candidate(
            self._selected[0] if count == 1 else None
        )
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
            )
        )

    def _apply(self, name: str) -> None:
        """Apply a recipe to the open image and say so."""
        self._host.apply_saved(name)
        self.show_toast(f"Applied “{name}”.")

    def _set_baseline(self, name: str, active: bool) -> None:
        """Set or clear the compare baseline."""
        self._host.set_baseline(name if active else None)

    def _missing_pictures(self) -> list[str]:
        """The recipes without a picture."""
        return [
            name
            for name in self._library.names
            if self._library.thumb_jpeg(name) is None
        ]

    def _refresh_pictures_button(self) -> None:
        """Offer to render the missing pictures while an image is open."""
        missing = len(self._missing_pictures())
        current = self._host.current_raf
        has_image = bool(current and current())
        self.pictures_button.set_visible(missing > 0)
        self.pictures_button.set_label(f"Make pictures ({missing})")
        self.pictures_button.set_sensitive(has_image)
        self.pictures_button.set_tooltip_text(
            f"Make pictures for the {missing} recipes without one,"
            " rendered on the open image"
            if has_image
            else "Open an image to make pictures for the recipes without one"
        )

    def _on_pictures_clicked(self, _button: Any) -> None:
        """Render a picture for every recipe that has none."""
        names = self._missing_pictures()
        if names:
            self._host.render_missing_thumbs(names)

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
        """Delete the selection, which the toast offers to undo."""
        if self._selected:
            self._host.delete_recipes(self.selected)

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
        self.camera_pane.set_busy(busy)

    def show_toast(
        self, message: str, undo: Callable[[], None] | None = None
    ) -> None:
        """Show an in-dialog toast, replacing any previous one."""
        if self._toast is not None:
            self._toast.dismiss()
        self._toast = Adw.Toast.new(message)
        if undo is not None:
            self._toast.set_button_label("Undo")
            self._toast.set_timeout(_UNDO_SECONDS)
            self._toast.connect("button-clicked", lambda _toast: undo())
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
        names, self._dragged = self._dragged, []
        return names[0] if names else None

    def _on_transfer_clicked(self, _button: Any) -> None:
        """Hand the bank/FS assignments and renames to the controller."""
        recipes, names, fs_recipes = self.camera_pane.collect()
        if recipes or fs_recipes or names:
            self._host.transfer_banks(recipes, names, fs_recipes)

    def _folder_header(self, folder: str) -> Gtk.Widget:
        """A folder's header: up and down arrows, then rename and delete."""
        folders = self._library.folders()
        index = folders.index(folder)
        header = Gtk.Box(valign=Gtk.Align.CENTER)
        for icon, tooltip, up, possible in (
            ("go-up-symbolic", "Move the folder up", True, index > 0),
            (
                "go-down-symbolic",
                "Move the folder down",
                False,
                index < len(folders) - 1,
            ),
        ):
            arrow = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            arrow.add_css_class("flat")
            arrow.set_sensitive(possible)
            arrow.connect("clicked", self._on_reorder_clicked, folder, up)
            header.append(arrow)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu.set_tooltip_text("Folder actions")
        menu.add_css_class("flat")
        popover, box = self._popover()
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
        header.append(menu)
        return header

    def _on_reorder_clicked(self, _button: Any, folder: str, up: bool) -> None:
        """Nudge a folder one step up or down."""
        self._host.reorder_folder(folder, up)

    def _on_recipe_drag(
        self, _source: Any, _x: float, _y: float, name: str
    ) -> Gdk.ContentProvider:
        """Begin dragging a recipe row, with the selection it belongs to."""
        together = name in self._selected and len(self._selected) > 1
        self._dragged = self.selected if together else [name]
        return Gdk.ContentProvider.new_for_value(name)

    def _on_drag_begin(self, _source: Any, drag: Any, name: str) -> None:
        """Attach a compact recipe chip as the drag icon."""
        chip = Gtk.Box()
        chip.add_css_class("card")
        count = len(self._dragged)
        label = Gtk.Label(
            label=f"{count} recipes" if count > 1 else name,
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

    def _on_drag_motion(self, _motion: Any, _x: float, y: float) -> None:
        """Scroll the list while a drag hovers near its top or bottom."""
        height = self.list_scroll.get_height()
        if y < _SCROLL_EDGE:
            self._scroll_speed = -(_SCROLL_EDGE - y) / _SCROLL_EDGE
        elif y > height - _SCROLL_EDGE:
            self._scroll_speed = (y - (height - _SCROLL_EDGE)) / _SCROLL_EDGE
        else:
            self._scroll_speed = 0.0
        if self._scroll_speed and self._scroll_tick is None:
            self._scroll_tick = self.list_scroll.add_tick_callback(
                self._on_scroll_tick
            )

    def _on_drag_leave(self, *_args: Any) -> None:
        """Stop scrolling once the drag leaves the list or ends."""
        self._scroll_speed = 0.0

    def _on_scroll_tick(self, _widget: Any, _clock: Any) -> bool:
        """Move the list one frame's worth, until the drag lets go."""
        if not self._scroll_speed:
            self._scroll_tick = None
            return False
        adjustment = self.list_scroll.get_vadjustment()
        adjustment.set_value(
            adjustment.get_value() + self._scroll_speed * _SCROLL_STEP
        )
        return True

    def _on_recipe_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, target: str
    ) -> bool:
        """Drop a recipe before target, adopting target's folder."""
        self._scroll_speed = 0.0
        names, self._dragged = self._dragged, []
        if names and target not in names:
            self._host.place_recipes(
                names, self._library.folder_of(target), target
            )
        return True

    def _on_group_drop(
        self, _target: Any, _value: Any, _x: float, _y: float, folder: str
    ) -> bool:
        """Drop a recipe onto a folder's area to append it there."""
        self._scroll_speed = 0.0
        names, self._dragged = self._dragged, []
        if names:
            self._host.place_recipes(names, folder, None)
        return bool(names)

    @staticmethod
    def _popover() -> tuple[Gtk.Popover, Gtk.Box]:
        """A popover holding a vertical button box."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        popover.set_child(box)
        return popover, box

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
