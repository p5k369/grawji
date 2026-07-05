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


def test_library_rename_updates_baseline_and_folder(tmp_path):
    """A rename follows the baseline marker and keeps the folder."""
    library = _library(tmp_path)
    library.move("Punchy", "Vivid looks")
    library.set_baseline("Punchy")
    assert library.rename("Punchy", "Portra") is True
    assert library.baseline == "Portra"
    assert library.folder_of("Portra") == "Vivid looks"


def test_library_folders(tmp_path):
    """Recipes move between folders; membership and order hold."""
    library = _library(tmp_path)
    assert library.folder_of("Punchy") == ""
    assert library.move("Punchy", "Bold") is True
    assert library.folders() == ["Bold"]
    assert library.names_in("Bold") == ["Punchy"]
    assert library.names_in("") == ["Mono"]
    assert library.move("Ghost", "Bold") is False


def test_library_folder_lifecycle(tmp_path):
    """Create, rename and delete folders; members follow."""
    library = _library(tmp_path)
    library.move("Punchy", "A")
    assert library.create_folder("Empty") is True
    assert library.create_folder("Empty") is False  # already exists
    assert library.rename_folder("A", "Bold") is True
    assert library.folder_of("Punchy") == "Bold"
    assert library.delete_folder("Bold") is True
    assert library.folder_of("Punchy") == ""  # back to ungrouped
    assert "Bold" not in library.folders()


def test_library_baseline(tmp_path):
    """The baseline marks one recipe; delete/rename keep it consistent."""
    library = _library(tmp_path)
    assert library.baseline is None
    assert library.set_baseline("Ghost") is False
    assert library.set_baseline("Mono") is True
    assert library.baseline == "Mono"
    assert library.baseline_recipe() == Recipe(film_simulation="Acros")
    library.delete("Mono")
    assert library.baseline is None  # cleared when its recipe goes


def test_library_v2_round_trip(tmp_path):
    """Folders and baseline survive a save/reload."""
    library = _library(tmp_path)
    library.move("Punchy", "Bold")
    library.create_folder("Empty")
    library.set_baseline("Mono")
    reloaded = RecipeLibrary(tmp_path / "recipes.json")
    assert reloaded.folder_of("Punchy") == "Bold"
    assert "Empty" in reloaded.folders()
    assert reloaded.baseline == "Mono"


def test_library_place_recipe_before(tmp_path):
    """Dropping a recipe before another positions it there."""
    library = _library(tmp_path)  # Punchy, Mono (ungrouped)
    library.add("Third", Recipe())
    assert library.place_recipe("Third", "", before="Punchy") is True
    assert library.names_in("") == ["Third", "Punchy", "Mono"]
    # Dropping onto itself is a no-op failure.
    assert library.place_recipe("Third", "", before="Third") is False


def test_library_place_recipe_into_folder(tmp_path):
    """Dropping a recipe onto a folder appends it there."""
    library = _library(tmp_path)
    library.create_folder("Film")
    assert library.place_recipe("Punchy", "Film") is True
    assert library.folder_of("Punchy") == "Film"
    assert library.names_in("Film") == ["Punchy"]
    # A second one lands after the first (folder end).
    assert library.place_recipe("Mono", "Film") is True
    assert library.names_in("Film") == ["Punchy", "Mono"]


def test_library_reorder_folder(tmp_path):
    """Folders nudge up/down one step; edges report False."""
    library = _library(tmp_path)
    for f in ("A", "B", "C"):
        library.create_folder(f)
    assert library.reorder_folder("C", up=True) is True
    assert library.folders() == ["A", "C", "B"]
    assert library.reorder_folder("A", up=True) is False
    assert library.reorder_folder("A", up=False) is True
    assert library.folders() == ["C", "A", "B"]


def test_library_migrates_flat_format(tmp_path):
    """An old flat recipes.json loads with everything ungrouped."""
    path = tmp_path / "recipes.json"
    save_recipes({"Old": Recipe(film_simulation="Astia")}, path)  # flat
    library = RecipeLibrary(path)
    assert library.names == ["Old"]
    assert library.folder_of("Old") == ""
    assert library.baseline is None
