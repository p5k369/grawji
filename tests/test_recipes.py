"""Tests for recipe storage."""

from grawji.recipe import Recipe
from grawji.recipes import decode_recipes, load_recipes, save_recipes


def test_save_load_round_trip(tmp_path):
    """Recipes survive a save/load round-trip."""
    path = tmp_path / "recipes.json"
    recipes = {
        "Punchy": Recipe(film_simulation="Velvia", color=3),
        "Mono": Recipe(film_simulation="Acros", color=0),
    }
    save_recipes(recipes, path)
    loaded = load_recipes(path)
    assert loaded == recipes


def test_load_missing_returns_empty(tmp_path):
    """Loading a non-existent recipe file returns an empty mapping."""
    assert load_recipes(tmp_path / "nope.json") == {}


def test_decode_skips_non_recipe_entries():
    """Entries that are not recipe dicts are ignored."""
    recipes = decode_recipes({"ok": {"film_simulation": "Astia"}, "bad": 5})
    assert list(recipes) == ["ok"]
    assert recipes["ok"].film_simulation == "Astia"


def test_export_then_import_on_other_path(tmp_path):
    """A file saved as an export imports back to the same recipes."""
    export = tmp_path / "export.json"
    recipes = {"Soft": Recipe(highlights=-2, sharpness=-1)}
    save_recipes(recipes, export)
    assert load_recipes(export) == recipes
