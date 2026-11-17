"""Bring many recipes into the library, or write many out, in one go."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk

from grawji import mainloop
from grawji.camera.fp_xml import parse_fp, serialize_fp
from grawji.imaging import imagemeta
from grawji.photo_recipe import recipe_from_tags
from grawji.recipe import Recipe
from grawji.recipe_import import (
    FP_SUFFIXES,
    PHOTO_SUFFIXES,
    Candidate,
    ImportReport,
    add_candidates,
    recipe_files,
    unique_name,
)
from grawji.recipe_text import parse_recipe_text
from grawji.recipes import RecipeLibrary


def read_recipe_file(path: Path) -> Recipe | None:
    """The recipe an FP file or a Fujifilm photo carries, or None."""
    if path.suffix.lower() in FP_SUFFIXES:
        try:
            return parse_fp(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    tags = imagemeta.photo_tags(str(path))
    found = recipe_from_tags(tags) if tags is not None else None
    return found.recipe if found is not None else None


def read_candidates(
    files: list[tuple[Path, str | None]],
) -> tuple[list[Candidate], list[str]]:
    """Read every file."""
    candidates: list[Candidate] = []
    failed: list[str] = []
    for path, folder in files:
        recipe = read_recipe_file(path)
        if recipe is None:
            failed.append(path.name)
        else:
            candidates.append(Candidate(path.stem, recipe, folder))
    return candidates, failed


def export_files(
    library: RecipeLibrary,
    names: list[str],
    directory: Path,
    iopcode: int | None,
) -> tuple[int, str]:
    """Write the named recipes as FP1 files."""
    taken = {entry.stem for entry in directory.glob("*.FP1")}
    written = 0
    for name in names:
        recipe = library.get(name)
        if recipe is None:
            continue
        stem = unique_name(name.replace("/", "-"), taken)
        text = serialize_fp(recipe, iopcode=iopcode, label=name)
        try:
            (directory / f"{stem}.FP1").write_text(text, encoding="utf-8")
        except OSError as exc:
            return written, str(exc)
        taken.add(stem)
        written += 1
    return written, ""


class RecipeBulk:
    """The dialogs and workers behind the manager's import and export."""

    def __init__(
        self,
        *,
        parent: Gtk.Widget,
        library: RecipeLibrary,
        get_iopcode: Callable[[], int | None],
        on_imported: Callable[[ImportReport], None],
        on_message: Callable[[str], None],
    ) -> None:
        """Wire the helper."""
        self._parent = parent
        self._library = library
        self._get_iopcode = get_iopcode
        self._on_imported = on_imported
        self._on_message = on_message

    def import_files(self, folder: str) -> None:
        """Pick recipe files and photos and store their recipes."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import recipes")
        recipes = Gtk.FileFilter()
        recipes.set_name("Recipes (FP1/FP2/FP3) and Fujifilm photos")
        for suffix in FP_SUFFIXES + PHOTO_SUFFIXES:
            recipes.add_suffix(suffix.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(recipes)
        dialog.set_filters(filters)
        dialog.set_default_filter(recipes)
        dialog.open_multiple(self._window(), None, self._on_files, folder)

    def import_folder(self, folder: str) -> None:
        """Pick a directory and store every recipe below it."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import a folder of recipes")
        dialog.select_folder(self._window(), None, self._on_folder, folder)

    def import_text(self, text: str, folder: str) -> None:
        """Store the recipe a community text describes."""
        parsed = parse_recipe_text(text)
        if parsed is None:
            self._on_message("The clipboard holds no recognizable recipe.")
            return
        candidate = Candidate(parsed.title or "Pasted recipe", parsed.recipe)
        self._on_imported(add_candidates(self._library, [candidate], folder))

    def export(self, names: list[str]) -> None:
        """Pick a directory and write the named recipes into it."""
        dialog = Gtk.FileDialog()
        dialog.set_title(f"Export {len(names)} recipes to a folder")
        dialog.select_folder(self._window(), None, self._on_export_dir, names)

    def _window(self) -> Any:
        """The window file dialogs attach to."""
        root = self._parent.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _on_files(self, dialog: Any, result: Any, folder: str) -> None:
        """Read the picked files."""
        try:
            picked = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [
            Path(path)
            for index in range(picked.get_n_items())
            if (path := picked.get_item(index).get_path())
        ]
        self._start([(path, None) for path in paths], folder)

    def _on_folder(self, dialog: Any, result: Any, folder: str) -> None:
        """Walk the picked directory."""
        try:
            picked = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = picked.get_path() if picked is not None else None
        if path:
            self._start(Path(path), folder)

    def _start(
        self, source: Path | list[tuple[Path, str | None]], folder: str
    ) -> None:
        """Read the recipes."""
        threading.Thread(
            target=self._read,
            args=(source, folder),
            name="grawji-recipe-import",
            daemon=True,
        ).start()

    def _read(
        self, source: Path | list[tuple[Path, str | None]], folder: str
    ) -> None:
        """Find and read the files."""
        files = recipe_files(source) if isinstance(source, Path) else source
        candidates, failed = read_candidates(files)
        mainloop.call(self._store, candidates, failed, folder)

    def _store(
        self, candidates: list[Candidate], failed: list[str], folder: str
    ) -> None:
        """Put the read recipes into the library."""
        report = add_candidates(self._library, candidates, folder)
        report.failed = failed
        self._on_imported(report)

    def _on_export_dir(
        self, dialog: Any, result: Any, names: list[str]
    ) -> None:
        """Write the recipes into the picked directory."""
        try:
            picked = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = picked.get_path() if picked is not None else None
        if not path:
            return
        written, error = export_files(
            self._library, names, Path(path), self._get_iopcode()
        )
        if error:
            self._on_message(f"Exported {written} recipes, then: {error}")
        else:
            self._on_message(f"Exported {written} recipes to {path}.")
