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
