"""Tests for recipe storage."""

from grawji.recipe import Recipe
from grawji.recipes import (
    RecipeLibrary,
    decode_recipes,
    load_recipes,
    save_recipes,
)


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


def _library(tmp_path):
    """A library over a temp file with two recipes saved."""
    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Punchy", Recipe(film_simulation="Velvia"))
    library.add("Mono", Recipe(film_simulation="Acros"))
    return library


def test_library_persists_every_change(tmp_path):
    """Each mutation lands on disk immediately."""
    _library(tmp_path)
    reloaded = RecipeLibrary(tmp_path / "recipes.json")
    assert reloaded.names == ["Punchy", "Mono"]
    assert reloaded.get("Mono") == Recipe(film_simulation="Acros")


def test_library_delete(tmp_path):
    """Deleting removes the recipe; a missing name reports False."""
    library = _library(tmp_path)
    assert library.delete("Punchy") is True
    assert library.names == ["Mono"]
    assert library.delete("Punchy") is False


def test_library_rename_keeps_position(tmp_path):
    """A rename keeps the recipe's place in the display order."""
    library = _library(tmp_path)
    assert library.rename("Punchy", "Vivid") is True
    assert library.names == ["Vivid", "Mono"]
    assert library.get("Vivid") == Recipe(film_simulation="Velvia")


def test_library_rename_collision_drops_loser(tmp_path):
    """Renaming onto an existing name replaces that recipe."""
    library = _library(tmp_path)
    assert library.rename("Punchy", "Mono") is True
    assert library.names == ["Mono"]
    assert library.get("Mono") == Recipe(film_simulation="Velvia")


def test_library_rename_rejects_noop_and_missing(tmp_path):
    """Renames that change nothing (or nothing real) report False."""
    library = _library(tmp_path)
    assert library.rename("Punchy", "Punchy") is False
    assert library.rename("Punchy", "") is False
    assert library.rename("Ghost", "New") is False
    assert library.names == ["Punchy", "Mono"]


def test_library_reorder(tmp_path):
    """A permutation is adopted; anything else is rejected."""
    library = _library(tmp_path)
    assert library.reorder(["Mono", "Punchy"]) is True
    assert library.names == ["Mono", "Punchy"]
    assert library.reorder(["Mono"]) is False
    assert library.reorder(["Mono", "Mono"]) is False
    assert library.names == ["Mono", "Punchy"]
