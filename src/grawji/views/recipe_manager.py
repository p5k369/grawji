"""The recipe manager dialog: folders, baseline, rename, export."""

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
from grawji.recipes import HOTKEYS, UNGROUPED, RecipeLibrary, recipe_matches
from grawji.views.camera_pane import CameraPane

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)

# Referencing the class registers GrawjiCameraPane for the template.
_ = CameraPane


class RecipeManagerHost(Protocol):
    """The controller surface the manager dialog drives."""

    get_capabilities: Callable[[], Any] | None
    get_model: Callable[[], str | None] | None
    load_bank_names: Callable[[Callable[[list[str]], None]], None] | None

    def export_recipe(self, name: str) -> None:
        """Export the named recipe as an FP file."""

    def delete_recipe(self, name: str) -> None:
        """Delete the named recipe."""

    def rename_recipe(self, old: str, new: str) -> None:
        """Rename a recipe."""

    def move_recipe(self, name: str, folder: str) -> None:
        """Move a recipe into a folder ("" for ungrouped)."""

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

    def clear_recipe_thumb(self, name: str) -> None:
        """Remove the recipe's stored thumbnail."""

    def edit_comment(self, name: str) -> None:
        """Prompt for the recipe's hover comment."""

    def import_recipe(self) -> None:
        """Pick and import an X RAW Studio FP file."""


@Gtk.Template(string=_UI)
class RecipeManagerDialog(Adw.Dialog):
    """Manage saved recipes: folders, baseline, rename, export, delete."""

    __gtype_name__ = "GrawjiRecipeManagerDialog"

    toasts = Gtk.Template.Child()
    new_folder_button = Gtk.Template.Child()
    import_button = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    transfer_button = Gtk.Template.Child()
    content = Gtk.Template.Child()
    stack = Gtk.Template.Child()
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
        self._toast: Adw.Toast | None = None
        self._thumb_textures: dict[str, Any] = {}

        self.new_folder_button.connect("clicked", self._on_new_folder)
        self.search_entry.connect("search-changed", self._apply_search)
        self.transfer_button.connect("clicked", self._on_transfer_clicked)
        self.import_button.connect(
            "clicked", lambda *_a: self._host.import_recipe()
        )
        self.refresh()
        self.camera_pane.wire(
            library=library,
            caps=self._caps,
            get_model=host.get_model,
            load_bank_names=host.load_bank_names,
            take_dragged=self._take_dragged,
        )
        self.transfer_button.set_sensitive(self.camera_pane.refresh())

    def refresh(self) -> None:
        """Rebuild the grouped view from the current library state."""
        for group in self._groups:
            self.content.remove(group)
        self._groups = []
        self._search_rows = []
        self._thumb_textures = {}
        self._duplicates = self._duplicate_map()

        has_recipes = bool(self._library.names)
        self.stack.set_visible_child_name("list" if has_recipes else "empty")
        if not has_recipes:
            return

        ungrouped = self._library.names_in(UNGROUPED)
        if ungrouped:
            self._add_group(UNGROUPED, "Recipes", ungrouped)
        for folder in self._library.folders():
            self._add_group(folder, folder, self._library.names_in(folder))
        self._apply_search()

    def _add_group(self, folder: str, title: str, names: list[str]) -> None:
        """Add a titled folder section holding the given recipe rows."""
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        if folder != UNGROUPED:
            group.set_header_suffix(self._folder_header(folder))
        for name in names:
            row = self._recipe_row(name)
            group.add(row)
            self._search_rows.append((group, row, self._search_text(name)))
        # Dropping a recipe onto the section's empty area moves it here.
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_group_drop, folder)
        group.add_controller(drop)
        self.content.add(group)
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
        """Build one recipe row: a baseline star and an overflow menu."""
        row = Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(name)
        recipe = self._library.get(name)
        if recipe is not None and recipe.origin_body:
            row.set_subtitle(f"from {recipe.origin_body}")

        if recipe is not None and self._caps is not None:
            badge = self._fit_badge(recipe)
            if badge is not None:
                row.add_suffix(badge)

        twins = self._duplicates.get(name)
        if twins:
            row.add_suffix(self._duplicate_badge(twins))

        hotkey = self._library.hotkey_of(name)
        if hotkey is not None:
            row.add_suffix(self._hotkey_badge(hotkey))

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
        source.connect("drag-begin", self._on_drag_begin, name)
        row.add_controller(source)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_recipe_drop, name)
        row.add_controller(drop)

        row.set_has_tooltip(True)
        row.connect("query-tooltip", self._on_row_tooltip, name)
        return row

    def _on_row_tooltip(
        self,
        _row: Any,
        _x: int,
        _y: int,
        _keyboard: bool,
        tooltip: Gtk.Tooltip,
        name: str,
    ) -> bool:
        """Fill a row's hover tooltip: recipe image and its comment."""
        if self._library.get(name) is None:
            return False
        texture = self._thumb_texture(name)
        comment = self._library.comment(name)
        if texture is None and not comment:
            return False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if texture is not None:
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            picture.set_halign(Gtk.Align.CENTER)
            width, height = self._thumb_display_size(texture)
            picture.set_size_request(width, height)
            box.append(picture)
        if comment:
            label = Gtk.Label(label=comment)
            label.set_wrap(True)
            label.set_max_width_chars(34)
            label.set_xalign(0.0)
            box.append(label)
        tooltip.set_custom(box)
        return True

    @staticmethod
    def _thumb_display_size(
        texture: Any, max_edge: int = 180
    ) -> tuple[int, int]:
        """Display size for a thumb texture, capped to max_edge on aspect."""
        width, height = texture.get_width(), texture.get_height()
        longer = max(width, height)
        if longer <= max_edge:
            return width, height
        scale = max_edge / longer
        return max(1, round(width * scale)), max(1, round(height * scale))

    def _thumb_texture(self, name: str) -> Any:
        """The recipe's cached thumbnail texture, or None."""
        if name in self._thumb_textures:
            return self._thumb_textures[name]
        texture = None
        jpeg = self._library.thumb_jpeg(name)
        if jpeg is not None:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(jpeg))
            except GLib.Error:
                texture = None
        self._thumb_textures[name] = texture
        return texture

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
        chip = Gtk.Label(label=label, valign=Gtk.Align.CENTER)
        chip.add_css_class("caption")
        chip.add_css_class(css)
        chip.set_tooltip_text("\n".join(verdict.issues))
        return chip

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

    def _hotkey_badge(self, key: int) -> Gtk.Widget:
        """A chip showing the number key that applies this recipe."""
        chip = Gtk.Label(label=f"key {key}", valign=Gtk.Align.CENTER)
        chip.add_css_class("caption")
        chip.add_css_class("accent")
        chip.set_tooltip_text(
            f"Press {key} in the main window to apply this recipe"
        )
        return chip

    def _duplicate_badge(self, twins: list[str]) -> Gtk.Widget:
        """A chip flagging a recipe that duplicates others' look."""
        chip = Gtk.Label(label="duplicate", valign=Gtk.Align.CENTER)
        chip.add_css_class("caption")
        chip.add_css_class("dim-label")
        joined = ", ".join(twins)
        chip.set_tooltip_text(f"Same look as: {joined}")
        return chip

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

    def set_bank_names(self, names: list[str]) -> None:
        """Forward loaded bank names to the camera pane."""
        self.camera_pane.set_bank_names(names)

    def _take_dragged(self) -> str | None:
        """Return and consume the recipe row currently being dragged."""
        name, self._dragged = self._dragged, None
        return name

    def _on_transfer_clicked(self, _button: Any) -> None:
        """Hand the bank/FS assignments and renames to the controller."""
        recipes, names, fs_recipes = self.camera_pane.collect()
        if recipes or fs_recipes or names:
            self._host.transfer_banks(recipes, names, fs_recipes)

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
        self._entry(
            box, popover, "Export…", lambda: self._host.export_recipe(name)
        )
        self._hotkey_entries(box, popover, name)
        self._image_entries(box, popover, name)
        self._entry(
            box,
            popover,
            "Delete",
            lambda: self._host.delete_recipe(name),
            destructive=True,
        )
        return popover

    def _hotkey_entries(
        self, box: Gtk.Box, popover: Gtk.Popover, name: str
    ) -> None:
        """Append the apply-key picker to a row's menu."""
        self._separator(box)
        caption = Gtk.Label(label="Apply with key", halign=Gtk.Align.START)
        caption.add_css_class("caption")
        caption.add_css_class("dim-label")
        caption.set_margin_start(8)
        box.append(caption)
        keys = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        current = self._library.hotkey_of(name)
        for key in HOTKEYS:
            button = Gtk.Button(label=str(key))
            button.add_css_class("flat")
            if key == current:
                button.add_css_class("suggested-action")
                button.set_tooltip_text("Click to remove the key")
            else:
                holder = self._library.recipe_for_hotkey(key)
                if holder is not None:
                    button.set_tooltip_text(f"Currently: {holder}")
            button.connect(
                "clicked", self._on_hotkey_clicked, popover, name, key
            )
            keys.append(button)
        box.append(keys)
        self._separator(box)

    def _on_hotkey_clicked(
        self, _button: Any, popover: Gtk.Popover, name: str, key: int
    ) -> None:
        """Assign the clicked key, or clear it when already assigned."""
        popover.popdown()
        new = None if self._library.hotkey_of(name) == key else key
        self._host.set_hotkey(name, new)

    def _image_entries(
        self, box: Gtk.Box, popover: Gtk.Popover, name: str
    ) -> None:
        """Append the recipe-image and comment actions to a row's menu."""
        has_image = self._library.thumb_jpeg(name) is not None
        entries: list[tuple[str, Callable[[], None]]] = [
            (
                "Update Image" if has_image else "Generate Image",
                lambda: self._host.render_recipe_thumb(name),
            )
        ]
        if has_image:
            entries.append(
                ("Remove Image", lambda: self._host.clear_recipe_thumb(name))
            )
        entries.append(
            (
                (
                    "Edit Comment…"
                    if self._library.comment(name)
                    else "Add Comment…"
                ),
                lambda: self._host.edit_comment(name),
            )
        )
        self._separator(box)
        for label, handler in entries:
            self._entry(box, popover, label, handler)

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

    def _mover(self, name: str, folder: str) -> Callable[[], None]:
        """A handler that moves a recipe into a folder."""
        return lambda: self._host.move_recipe(name, folder)

    def _on_star_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        """Set or clear the compare baseline from a row's star."""
        self._host.set_baseline(name if button.get_active() else None)

    def _rename(self, name: str) -> None:
        """Prompt for a new recipe name and rename."""
        self._prompt(
            "Rename recipe",
            "New name",
            name,
            lambda new: self._host.rename_recipe(name, new),
        )

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
