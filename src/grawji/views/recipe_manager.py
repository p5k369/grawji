"""The saved-recipe library UI: the manager dialog and its controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from grawji.camera import compatibility as compat
from grawji.camera.fp_xml import parse_fp, serialize_fp
from grawji.recipe import Recipe
from grawji.recipe_dedup import find_duplicate_recipes
from grawji.recipe_text import parse_recipe_text
from grawji.recipes import UNGROUPED, RecipeLibrary
from grawji.views import dialogs
from grawji.views.camera_pane import CameraPane
from grawji.views.recipe_panel import RecipePanel

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_manager.ui")
    .read_text(encoding="utf-8")
)

# Referencing the class registers GrawjiCameraPane for the template.
_ = CameraPane


@Gtk.Template(string=_UI)
class RecipeManagerDialog(Adw.Dialog):
    """Manage saved recipes: folders, baseline, rename, export, delete."""

    __gtype_name__ = "GrawjiRecipeManagerDialog"

    toasts = Gtk.Template.Child()
    new_folder_button = Gtk.Template.Child()
    import_button = Gtk.Template.Child()
    transfer_button = Gtk.Template.Child()
    content = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    camera_pane = Gtk.Template.Child()

    def __init__(  # noqa: PLR0913
        self,
        *,
        library: RecipeLibrary,
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
        get_capabilities: Callable[[], Any] | None = None,
        get_model: Callable[[], str | None] | None = None,
        load_bank_names: (
            Callable[[Callable[[list[str]], None]], None] | None
        ) = None,
        on_transfer: (
            Callable[[dict[int, str], dict[int, str], dict[int, str]], None]
            | None
        ) = None,
        on_render_image: Callable[[str], None] | None = None,
        on_clear_image: Callable[[str], None] | None = None,
        on_edit_comment: Callable[[str], None] | None = None,
        on_import: Callable[[], None] | None = None,
    ) -> None:
        """Wire the dialog to the library (read) and intent callbacks."""
        super().__init__()
        self._library = library
        self._get_capabilities = get_capabilities
        self._caps = get_capabilities() if get_capabilities else None
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
        self._get_model = get_model
        self._load_bank_names = load_bank_names
        self._on_transfer = on_transfer
        self._on_render_image = on_render_image
        self._on_clear_image = on_clear_image
        self._on_edit_comment = on_edit_comment
        self._on_import = on_import
        self._dragged: str | None = None
        self._groups: list[Adw.PreferencesGroup] = []
        self._toast: Adw.Toast | None = None
        self._thumb_textures: dict[str, Any] = {}

        self.new_folder_button.connect("clicked", self._on_new_folder)
        self.transfer_button.connect("clicked", self._on_transfer_clicked)
        if self._on_import is not None:
            self.import_button.connect(
                "clicked", lambda *_a: self._on_import()
            )
        else:
            self.import_button.set_visible(False)
        self.refresh()
        self.camera_pane.wire(
            library=library,
            caps=self._caps,
            get_model=get_model,
            load_bank_names=load_bank_names,
            take_dragged=self._take_dragged,
        )
        can_write = self.camera_pane.refresh() and on_transfer is not None
        self.transfer_button.set_sensitive(can_write)

    def refresh(self) -> None:
        """Rebuild the grouped view from the current library state."""
        for group in self._groups:
            self.content.remove(group)
        self._groups = []
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
        if self._on_transfer is None:
            return
        recipes, names, fs_recipes = self.camera_pane.collect()
        if recipes or fs_recipes or names:
            self._on_transfer(recipes, names, fs_recipes)

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
        self._image_entries(box, popover, name)
        self._entry(
            box,
            popover,
            "Delete",
            lambda: self._on_delete(name),
            destructive=True,
        )
        return popover

    def _image_entries(
        self, box: Gtk.Box, popover: Gtk.Popover, name: str
    ) -> None:
        """Append the recipe-image and comment actions to a row's menu."""
        entries: list[tuple[str, Callable[[], None]]] = []
        render = self._on_render_image
        has_image = self._library.thumb_jpeg(name) is not None
        if render is not None:
            label = "Update Image" if has_image else "Generate Image"
            entries.append((label, lambda: render(name)))
        clear = self._on_clear_image
        if clear is not None and has_image:
            entries.append(("Remove Image", lambda: clear(name)))
        comment = self._on_edit_comment
        if comment is not None:
            label = (
                "Edit Comment…"
                if self._library.comment(name)
                else "Add Comment…"
            )
            entries.append((label, lambda: comment(name)))
        if not entries:
            return
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
        on_closed: Callable[[], None] = lambda: None,
        get_capabilities: Callable[[], Any] | None = None,
        get_model: Callable[[], str | None] | None = None,
        load_bank_names: (
            Callable[[Callable[[list[str]], None]], None] | None
        ) = None,
        run_transfer: Callable[..., None] | None = None,
        render_thumb: (
            Callable[[Recipe, Callable[[bytes | None], None]], None] | None
        ) = None,
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
            on_closed: Called when the manager dialog is dismissed, so the
                window can re-open a session the bank pane closed.
            get_capabilities: Returns the connected body's Capabilities for
                recipe-compatibility badges, or None to disable them.
            get_model: Returns the connected body's model, used to tag a
                saved recipe's origin and drive the camera-bank pane.
            load_bank_names: Reads current bank names off the main thread
                (calls its callback with them), or None.
            run_transfer: Performs the USB bank transfer off the main
                thread (recipes, names, fs_recipes, on_done, on_error),
                or None.
            render_thumb: Renders a recipe against the open RAF and calls
                back with a small thumbnail JPEG. Does nothing when no
                image is open. None disables recipe thumbnails entirely.
        """
        self._parent = parent
        self._library = library
        self._panel = panel
        self._on_render = on_render
        self._on_status = on_status
        self._get_iopcode = get_iopcode
        self._on_baseline_changed = on_baseline_changed
        self._on_closed = on_closed
        self._get_capabilities = get_capabilities
        self._get_model = get_model
        self._load_bank_names = load_bank_names
        self._run_transfer = run_transfer
        self._render_thumb = render_thumb
        self._manager: RecipeManagerDialog | None = None
        self._refresh()

    def manage(self) -> None:
        """Open the recipe manager modal."""
        self._manager = RecipeManagerDialog(
            library=self._library,
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
            get_capabilities=self._get_capabilities,
            get_model=self._get_model,
            load_bank_names=self._load_bank_names,
            on_transfer=self._transfer_banks,
            on_render_image=self._render_thumb_for,
            on_clear_image=self._clear_thumb,
            on_edit_comment=self._edit_comment,
            on_import=self.import_fp,
        )
        self._manager.connect("closed", self._on_manager_closed)
        dialogs.fit_dialog(
            self._manager,
            self._parent,
            width_fraction=0.9,
            height_fraction=0.9,
        )
        self._manager.present(self._parent)

    def _transfer_banks(
        self,
        assignments: dict[int, str],
        names: dict[int, str],
        fs_assignments: dict[int, str],
    ) -> None:
        """Resolve dropped recipe names and run the USB bank transfer."""
        if self._run_transfer is None:
            return

        def resolve(named: dict[int, str]) -> dict[int, Recipe]:
            out: dict[int, Recipe] = {}
            for slot, name in named.items():
                recipe = self._library.get(name)
                if recipe is not None:
                    out[slot] = recipe
            return out

        recipes = resolve(assignments)
        fs_recipes = resolve(fs_assignments)
        if not recipes and not names and not fs_recipes:
            return
        count = len(recipes) + len(fs_recipes)
        msg = f"Transferring {count} recipe(s) to the camera…"
        if self._manager is not None:
            self._manager.set_busy(True)
            self._manager.show_toast(msg)
        self._on_status(msg)
        self._run_transfer(
            recipes, names, fs_recipes, self._on_bank_done, self._on_bank_fail
        )

    def _on_bank_done(self, message: str) -> None:
        """Report a finished bank transfer, refresh and re-enable Transfer."""
        self._on_status(message)
        if self._manager is not None:
            self._manager.set_busy(False)
            self._manager.on_transfer_finished()
            self._manager.show_toast(message)

    def _on_bank_fail(self, message: str) -> None:
        """Report a failed bank transfer and re-enable Transfer."""
        self._on_status(message)
        if self._manager is not None:
            self._manager.set_busy(False)
            self._manager.show_toast(message)

    def save_current(self) -> None:
        """Ask for a name and save the panel's controls as a recipe.

        A modified saved recipe prefills its own name.
        """
        label = self._panel.active_label
        known = self._library.get(label) is not None
        default = label if known or self._panel.active_unsaved else ""
        self._prompt_save(self._panel.get_recipe(), default)

    def apply(self, name: str) -> None:
        """Apply a saved recipe to the controls and re-render."""
        recipe = self._library.get(name)
        if recipe is None:
            return
        exposure = self._panel.get_recipe().exposure
        self._panel.set_active(recipe, name)
        self._panel.set_exposure(exposure)
        self._on_render()
        self._on_status(f"Applied recipe “{name}”.{self._fit_note(recipe)}")

    def _fit_note(self, recipe: Recipe) -> str:
        """A short compatibility note for the connected body, or ""."""
        if self._get_capabilities is None:
            return ""
        verdict = compat.evaluate(recipe, self._get_capabilities())
        if verdict.level == compat.UNAVAILABLE:
            return f" Warning: {verdict.issues[0]}."
        if verdict.level == compat.DEGRADED:
            return f" On this body: {'; '.join(verdict.issues)}."
        return ""

    def paste_text(self) -> None:
        """Create a recipe from community text on the clipboard."""
        clipboard = self._parent.get_clipboard()
        clipboard.read_text_async(None, self._on_paste_text)

    def _on_paste_text(self, clipboard: Any, result: Any) -> None:
        """Parse the clipboard text and apply it right away."""
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        parsed = parse_recipe_text(text or "")
        if parsed is None:
            self._on_status("The clipboard holds no recognizable recipe text.")
            if self._manager is not None:
                self._manager.show_toast("No recipe found in the clipboard.")
            return
        self._apply_unsaved(parsed.recipe, parsed.title or "Pasted recipe")
        if parsed.notes:
            skipped = "; ".join(parsed.notes[:3])
            self._on_status(f"Pasted with notes: {skipped}")

    def _apply_unsaved(self, recipe: Recipe, title: str) -> None:
        """Apply an imported/pasted recipe right away, marked unsaved."""
        self._panel.set_active(recipe, title, unsaved=True)
        self._on_render()
        self._on_status(
            f"Applied “{title}” (not saved yet — the save button "
            "stores it)."
        )
        if self._manager is not None:
            self._manager.show_toast(f"Applied “{title}” (unsaved).")

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
        """Forget the manager and let the window recover the session."""
        self._manager = None
        self._on_closed()

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

    def _render_thumb_for(self, name: str) -> None:
        """Render the saved recipe against the open RAF as its image."""
        recipe = self._library.get(name)
        if recipe is None or self._render_thumb is None:
            return
        if self._manager is not None:
            self._manager.show_toast(f"Rendering picture for “{name}”…")

        def done(jpeg: bytes | None) -> None:
            if jpeg and self._library.set_thumb(name, jpeg):
                self._refresh()
                self._toast(f"Set the picture of “{name}”.")
            elif jpeg is None:
                self._toast("Open an image to generate a recipe picture.")

        self._render_thumb(recipe, done)

    def _toast(self, message: str) -> None:
        """Report to the manager toast if open, else the status line."""
        if self._manager is not None:
            self._manager.show_toast(message)
        else:
            self._on_status(message)

    def _clear_thumb(self, name: str) -> None:
        """Drop a recipe's thumbnail and refresh the views."""
        if self._library.set_thumb(name, None):
            self._refresh()
            self._on_status(f"Removed the picture of “{name}”.")

    def _edit_comment(self, name: str) -> None:
        """Prompt for a recipe's hover comment and store it."""

        def done(text: str) -> None:
            if self._library.set_comment(name, text):
                self._refresh()

        dialog = Adw.AlertDialog(
            heading="Recipe comment",
            body="A short note shown when hovering this recipe:",
        )
        entry = Gtk.Entry(text=self._library.comment(name))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Save")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, resp: done(entry.get_text()) if resp == "ok" else None,
        )
        dialog.present(self._parent)

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
        recipe = replace(recipe, exposure=0.0)
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
        if not recipe.origin_body and self._get_model is not None:
            model = self._get_model()
            if model:
                recipe = replace(recipe, origin_body=model)
        # Overwriting keeps the recipe in its folder.
        self._library.add(name, recipe, folder=self._library.folder_of(name))
        self._refresh()
        if self._render_thumb is not None and (
            self._library.thumb_jpeg(name) is None
        ):

            def store(jpeg: bytes | None) -> None:
                if jpeg and self._library.set_thumb(name, jpeg):
                    self._refresh()

            self._render_thumb(recipe, store)
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
        self._apply_unsaved(recipe, Path(path).stem)

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
