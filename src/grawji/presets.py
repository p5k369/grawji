"""Named recipe presets with JSON storage and import/export."""

from __future__ import annotations

import json
from pathlib import Path

from grawji.recipe import Recipe
from grawji.settings import config_dir


def presets_path() -> Path:
    """Return the path to the presets JSON file."""
    return config_dir() / "presets.json"


def decode_presets(data: object) -> dict[str, Recipe]:
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


def load_presets(path: Path) -> dict[str, Recipe]:
    """Load presets from ``path``, returning ``{}`` if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return decode_presets(data)


def save_presets(presets: dict[str, Recipe], path: Path) -> None:
    """Write ``presets`` to ``path`` as JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = {name: recipe.to_dict() for name, recipe in presets.items()}
    path.write_text(json.dumps(encoded, indent=2), encoding="utf-8")
