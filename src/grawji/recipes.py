"""Named recipes with JSON storage, organised into folders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grawji.recipe import Recipe
from grawji.settings import config_dir

# Recipes with no folder live at the top level, keyed by this sentinel.
UNGROUPED = ""


def recipes_path() -> Path:
    """Return the path to the recipes JSON file."""
    return config_dir() / "recipes.json"


def decode_recipes(data: object) -> dict[str, Recipe]:
    """Turn a parsed flat JSON object into a name -> Recipe mapping."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Recipe] = {}
    for name, value in data.items():
        if isinstance(name, str) and isinstance(value, dict):
            out[name] = Recipe.from_dict(value)
    return out


def load_recipes(path: Path) -> dict[str, Recipe]:
    """Load recipes from a flat-format file, returning {} if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return decode_recipes(data)


def save_recipes(recipes: dict[str, Recipe], path: Path) -> None:
    """Write recipes to path in the flat format (used by tests/export)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = {name: recipe.to_dict() for name, recipe in recipes.items()}
    path.write_text(json.dumps(encoded, indent=2), encoding="utf-8")


class RecipeLibrary:
    """Saved recipes plus their folders and the compare baseline."""

    def __init__(self, path: Path) -> None:
        """Load the library from path (missing/unreadable gives empty)."""
        self._path = path
        self._recipes: dict[str, Recipe] = {}
        self._folder_of: dict[str, str] = {}
        self._folders: list[str] = []
        self._baseline: str | None = None
        self._load()

    @property
    def names(self) -> list[str]:
        """All recipe names, in display order."""
        return list(self._recipes)

    def get(self, name: str) -> Recipe | None:
        """Return the named recipe, or None."""
        return self._recipes.get(name)

    def folder_of(self, name: str) -> str:
        """The folder a recipe belongs to ("" = ungrouped)."""
        return self._folder_of.get(name, UNGROUPED)

    def folders(self) -> list[str]:
        """The folder names, in display order (excludes ungrouped)."""
        return list(self._folders)

    def names_in(self, folder: str) -> list[str]:
        """Recipe names in a folder, in display order."""
        return [
            name
            for name in self._recipes
            if self._folder_of.get(name, UNGROUPED) == folder
        ]

    @property
    def baseline(self) -> str | None:
        """The name of the recipe marked as compare baseline, or None."""
        return self._baseline

    def baseline_recipe(self) -> Recipe | None:
        """The recipe marked as compare baseline, or None."""
        return self._recipes.get(self._baseline) if self._baseline else None

    def add(self, name: str, recipe: Recipe, folder: str = UNGROUPED) -> None:
        """Store recipe under name (replacing any previous one)."""
        self._recipes[name] = recipe
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        self._save()

    def delete(self, name: str) -> bool:
        """Remove the named recipe; False if it did not exist."""
        if name not in self._recipes:
            return False
        del self._recipes[name]
        self._folder_of.pop(name, None)
        if self._baseline == name:
            self._baseline = None
        self._save()
        return True

    def rename(self, old: str, new: str) -> bool:
        """Rename a recipe, keeping its place and folder, False if invalid.

        A recipe already stored under the new name is dropped: the rename
        wins the collision.
        """
        if old not in self._recipes or not new or new == old:
            return False
        folder = self._folder_of.get(old, UNGROUPED)
        renamed = self._recipes[old]
        rebuilt: dict[str, Recipe] = {}
        for name, value in self._recipes.items():
            if name == old:
                rebuilt[new] = renamed
            elif name != new:
                rebuilt[name] = value
        self._recipes = rebuilt
        self._folder_of.pop(old, None)
        self._folder_of.pop(new, None)
        self._folder_of[new] = folder
        if self._baseline in (old, new):
            self._baseline = new
        self._save()
        return True

    def move(self, name: str, folder: str) -> bool:
        """Move a recipe into folder ("" = ungrouped); False if unknown."""
        if name not in self._recipes:
            return False
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        self._save()
        return True

    def place_recipe(
        self, name: str, folder: str, before: str | None = None
    ) -> bool:
        """Move a recipe into folder, positioned before another recipe."""
        if name not in self._recipes or name == before:
            return False
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        order = [n for n in self._recipes if n != name]
        if before is not None and before in order:
            index = order.index(before)
        else:
            members = [
                i
                for i, n in enumerate(order)
                if self._folder_of.get(n, UNGROUPED) == folder
            ]
            index = members[-1] + 1 if members else len(order)
        order.insert(index, name)
        self._recipes = {n: self._recipes[n] for n in order}
        self._save()
        return True

    def set_baseline(self, name: str | None) -> bool:
        """Mark name as the compare baseline (or None to clear).

        False if name is given but is not a saved recipe.
        """
        if name is not None and name not in self._recipes:
            return False
        self._baseline = name
        self._save()
        return True

    def create_folder(self, name: str) -> bool:
        """Create an empty folder; False if blank or already present."""
        if not name or name in self._folders:
            return False
        self._folders.append(name)
        self._save()
        return True

    def rename_folder(self, old: str, new: str) -> bool:
        """Rename a folder and its members; False if invalid or a clash."""
        if old not in self._folders or not new or new in self._folders:
            return False
        self._folders[self._folders.index(old)] = new
        for name, folder in self._folder_of.items():
            if folder == old:
                self._folder_of[name] = new
        self._save()
        return True

    def delete_folder(self, name: str) -> bool:
        """Delete a folder; its recipes move to ungrouped. False if absent."""
        if name not in self._folders:
            return False
        self._folders.remove(name)
        for recipe, folder in self._folder_of.items():
            if folder == name:
                self._folder_of[recipe] = UNGROUPED
        self._save()
        return True

    def reorder_folder(self, name: str, *, up: bool) -> bool:
        """Swap a folder with its neighbour, False if at the edge."""
        if name not in self._folders:
            return False
        index = self._folders.index(name)
        target = index - 1 if up else index + 1
        if target < 0 or target >= len(self._folders):
            return False
        self._folders[index], self._folders[target] = (
            self._folders[target],
            self._folders[index],
        )
        self._save()
        return True

    def _ensure_folder(self, folder: str) -> None:
        """Register a non-empty folder in the display order if new."""
        if folder and folder not in self._folders:
            self._folders.append(folder)

    def _load(self) -> None:
        """Read the library, migrating the flat format when needed."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get("recipes"), dict):
            self._load_v2(data)
        else:
            self._recipes = decode_recipes(data)
            self._folder_of = dict.fromkeys(self._recipes, UNGROUPED)
        self._normalise()

    def _load_v2(self, data: dict[str, Any]) -> None:
        """Load the versioned format (recipes carry a folder field)."""
        for name, value in data["recipes"].items():
            if not (isinstance(name, str) and isinstance(value, dict)):
                continue
            folder = value.get("folder")
            fields = {k: v for k, v in value.items() if k != "folder"}
            self._recipes[name] = Recipe.from_dict(fields)
            self._folder_of[name] = folder if isinstance(folder, str) else ""
        self._folders = [
            f for f in data.get("folders", []) if isinstance(f, str)
        ]
        baseline = data.get("baseline")
        self._baseline = baseline if isinstance(baseline, str) else None

    def _normalise(self) -> None:
        """Make folders self-consistent after loading."""
        for folder in self._folder_of.values():
            self._ensure_folder(folder)
        if self._baseline not in self._recipes:
            self._baseline = None

    def _save(self) -> None:
        """Persist the library to its path in the v2 format."""
        encoded: dict[str, Any] = {}
        for name, recipe in self._recipes.items():
            entry = recipe.to_dict()
            folder = self._folder_of.get(name, UNGROUPED)
            if folder:
                entry["folder"] = folder
            encoded[name] = entry
        data = {
            "version": 2,
            "baseline": self._baseline,
            "folders": self._folders,
            "recipes": encoded,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
