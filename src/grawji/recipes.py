"""Named recipes with JSON storage."""

from __future__ import annotations

import json
from pathlib import Path

from grawji.recipe import Recipe
from grawji.settings import config_dir


def recipes_path() -> Path:
    """Return the path to the recipes JSON file."""
    return config_dir() / "recipes.json"


def decode_recipes(data: object) -> dict[str, Recipe]:
    """Turn a parsed JSON object into a name -> Recipe mapping.

    Entries that are not recipe dicts are skipped.
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, Recipe] = {}
    for name, value in data.items():
        if isinstance(name, str) and isinstance(value, dict):
            out[name] = Recipe.from_dict(value)
    return out


def load_recipes(path: Path) -> dict[str, Recipe]:
    """Load recipes from path, returning {} if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return decode_recipes(data)


def save_recipes(recipes: dict[str, Recipe], path: Path) -> None:
    """Write recipes to path as JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = {name: recipe.to_dict() for name, recipe in recipes.items()}
    path.write_text(json.dumps(encoded, indent=2), encoding="utf-8")


class RecipeLibrary:
    """The saved recipes, in display order, persisted on every change."""

    def __init__(self, path: Path) -> None:
        """Load the library from path (missing or unreadable gives {})."""
        self._path = path
        self._recipes = load_recipes(path)

    @property
    def names(self) -> list[str]:
        """The saved recipe names, in display order."""
        return list(self._recipes)

    def get(self, name: str) -> Recipe | None:
        """Return the named recipe, or None."""
        return self._recipes.get(name)

    def add(self, name: str, recipe: Recipe) -> None:
        """Store recipe under name (replacing any previous one)."""
        self._recipes[name] = recipe
        self._save()

    def delete(self, name: str) -> bool:
        """Remove the named recipe; False if it did not exist."""
        if name not in self._recipes:
            return False
        del self._recipes[name]
        self._save()
        return True

    def rename(self, old: str, new: str) -> bool:
        """Rename a recipe, keeping its position; False if not applicable.

        A recipe already stored under the new name is dropped: the
        rename wins the collision.
        """
        if old not in self._recipes or not new or new == old:
            return False
        renamed = self._recipes[old]
        rebuilt: dict[str, Recipe] = {}
        for name, value in self._recipes.items():
            if name == old:
                rebuilt[new] = renamed
            elif name != new:
                rebuilt[name] = value
        self._recipes = rebuilt
        self._save()
        return True

    def reorder(self, order: list[str]) -> bool:
        """Adopt a new display order; False unless order is a permutation."""
        if set(order) != set(self._recipes) or len(order) != len(
            self._recipes
        ):
            return False
        self._recipes = {name: self._recipes[name] for name in order}
        self._save()
        return True

    def _save(self) -> None:
        """Persist the library to its path."""
        save_recipes(self._recipes, self._path)
