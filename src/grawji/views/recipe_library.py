"""Controller for the saved-recipe library and its dialogs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from grawji.camera import compatibility as compat
from grawji.camera.fp_xml import parse_fp, serialize_fp
from grawji.recipe import Recipe
from grawji.recipe_text import format_recipe_text, parse_recipe_text
from grawji.recipes import UNGROUPED, RecipeLibrary
from grawji.settings import FROM_IMAGE_LABEL
from grawji.views import dialogs
from grawji.views.recipe_manager import RecipeManagerDialog
from grawji.views.recipe_panel import RecipePanel


class RecipeLibraryController:
    """Glue between the library, the panel and the library dialogs."""

    def __init__(  # noqa: PLR0913
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
            load_bank_names: Reads current bank names off the main thread.
            run_transfer: Performs the USB bank transfer off the main thread.
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
        self.get_capabilities = get_capabilities
        self.get_model = get_model
        self.load_bank_names = load_bank_names
        self._run_transfer = run_transfer
        self._render_thumb = render_thumb
        self._manager: RecipeManagerDialog | None = None
        self._refresh()

    def manage(self) -> None:
        """Open the recipe manager modal."""
        self._manager = RecipeManagerDialog(library=self._library, host=self)
        self._manager.connect("closed", self._on_manager_closed)
        dialogs.fit_dialog(
            self._manager,
            self._parent,
            width_fraction=0.9,
            height_fraction=0.9,
        )
        self._manager.present(self._parent)

    def transfer_banks(
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
        """Ask for a name and save the panel's controls as a recipe."""
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
        if self.get_capabilities is None:
            return ""
        verdict = compat.evaluate(recipe, self.get_capabilities())
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

    def copy_text(self) -> None:
        """Copy the current recipe to the clipboard as shareable text."""
        label = self._panel.active_label
        title = "" if label == FROM_IMAGE_LABEL else label
        text = format_recipe_text(self._panel.get_recipe(), title)
        self._parent.get_clipboard().set(text)
        self._on_status("Recipe copied to the clipboard as text.")
        if self._manager is not None:
            self._manager.show_toast("Recipe copied as text.")

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

    def import_recipe(self) -> None:
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

    def export_recipe(self, name: str) -> None:
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
        """Mirror the library into the panel picker and an open manager."""
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

    def delete_recipe(self, name: str) -> None:
        """Remove a saved recipe and persist the change."""
        if self._library.delete(name):
            self._refresh()
            self._on_status(f"Deleted recipe “{name}”.")

    def rename_recipe(self, old: str, new: str) -> None:
        """Rename a saved recipe, keeping its position, and persist."""
        if not self._library.rename(old, new):
            return
        self._refresh()
        if self._panel.active_label == old:
            renamed = self._library.get(new)
            if renamed is not None:
                self._panel.set_active(renamed, new)

    def move_recipe(self, name: str, folder: str) -> None:
        """Move a recipe into a folder and refresh."""
        if self._library.move(name, folder):
            self._refresh()

    def place_recipe(self, name: str, folder: str, before: str | None) -> None:
        """Place a dragged recipe into folder before another, and refresh."""
        if self._library.place_recipe(name, folder, before):
            self._refresh()

    def reorder_folder(self, folder: str, up: bool) -> None:
        """Nudge a folder up or down and refresh."""
        if self._library.reorder_folder(folder, up=up):
            self._refresh()

    def set_baseline(self, name: str | None) -> None:
        """Mark the compare baseline and notify the window."""
        if self._library.set_baseline(name):
            self._refresh()
            self._on_baseline_changed()

    def set_hotkey(self, name: str, key: int | None) -> None:
        """Assign a number key to a recipe and refresh."""
        if self._library.set_hotkey(name, key):
            self._refresh()

    def create_folder(self, name: str) -> None:
        """Create a folder and refresh."""
        if self._library.create_folder(name):
            self._refresh()

    def rename_folder(self, old: str, new: str) -> None:
        """Rename a folder and refresh."""
        if self._library.rename_folder(old, new):
            self._refresh()

    def delete_folder(self, name: str) -> None:
        """Delete a folder and refresh."""
        if self._library.delete_folder(name):
            self._refresh()

    def render_recipe_thumb(self, name: str) -> None:
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

    def clear_recipe_thumb(self, name: str) -> None:
        """Drop a recipe's thumbnail and refresh the views."""
        if self._library.set_thumb(name, None):
            self._refresh()
            self._on_status(f"Removed the picture of “{name}”.")

    def edit_comment(self, name: str) -> None:
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
            activate: Re-render the preview after saving.
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
        if not recipe.origin_body and self.get_model is not None:
            model = self.get_model()
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
