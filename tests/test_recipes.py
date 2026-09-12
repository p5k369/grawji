"""Tests for recipe storage."""

import json

from grawji.recipe import Recipe
from grawji.recipes import (
    RecipeLibrary,
    decode_recipes,
    recipe_matches,
)


def _save_flat(recipes: dict[str, Recipe], path) -> None:
    """Write recipes in the old flat v1 format."""
    encoded = {name: recipe.to_dict() for name, recipe in recipes.items()}
    path.write_text(json.dumps(encoded, indent=2), encoding="utf-8")


def test_decode_skips_non_recipe_entries():
    """Entries that are not recipe dicts are ignored."""
    recipes = decode_recipes({"ok": {"film_simulation": "Astia"}, "bad": 5})
    assert list(recipes) == ["ok"]
    assert recipes["ok"].film_simulation == "Astia"


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
    _save_flat({"Old": Recipe(film_simulation="Astia")}, path)
    library = RecipeLibrary(path)
    assert library.names == ["Old"]
    assert library.folder_of("Old") == ""
    assert library.baseline is None


def test_library_thumb_round_trip(tmp_path):
    """A stored thumbnail survives reload byte-exact."""
    library = _library(tmp_path)
    assert library.thumb_jpeg("Punchy") is None
    assert library.set_thumb("Punchy", b"\xff\xd8jpeg-bytes") is True
    reloaded = RecipeLibrary(tmp_path / "recipes.json")
    assert reloaded.thumb_jpeg("Punchy") == b"\xff\xd8jpeg-bytes"
    assert reloaded.thumb_jpeg("Mono") is None


def test_library_thumb_requires_known_recipe(tmp_path):
    """Thumbs cannot be attached to unknown names."""
    library = _library(tmp_path)
    assert library.set_thumb("Nope", b"x") is False


def test_library_thumb_clear(tmp_path):
    """Setting None drops the thumbnail."""
    library = _library(tmp_path)
    library.set_thumb("Punchy", b"x")
    assert library.set_thumb("Punchy", None) is True
    assert library.thumb_jpeg("Punchy") is None
    assert library.set_thumb("Punchy", None) is False


def test_library_thumb_survives_overwrite(tmp_path):
    """Re-saving a recipe under its name keeps the stored picture."""
    library = _library(tmp_path)
    library.set_thumb("Punchy", b"x")
    library.add("Punchy", Recipe(film_simulation="Provia"))
    assert library.thumb_jpeg("Punchy") == b"x"


def test_library_thumb_follows_rename_and_delete(tmp_path):
    """Rename moves the thumbnail with the recipe."""
    library = _library(tmp_path)
    library.set_thumb("Punchy", b"x")
    library.rename("Punchy", "Portra")
    assert library.thumb_jpeg("Portra") == b"x"
    assert library.thumb_jpeg("Punchy") is None
    library.delete("Portra")
    library.add("Portra", Recipe())
    assert library.thumb_jpeg("Portra") is None


def test_library_thumb_bad_base64_reads_none(tmp_path):
    """A corrupted stored thumb decodes to None instead of raising."""
    library = _library(tmp_path)
    library.set_thumb("Punchy", b"x")
    path = tmp_path / "recipes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["recipes"]["Punchy"]["thumb"] = "not base64 !!!"
    path.write_text(json.dumps(data), encoding="utf-8")
    reloaded = RecipeLibrary(path)
    assert reloaded.thumb_jpeg("Punchy") is None


def test_library_comment_round_trip(tmp_path):
    """A comment survives reload and clears with an empty string."""
    library = _library(tmp_path)
    assert library.comment("Punchy") == ""
    assert library.set_comment("Punchy", "  Sunny day look  ") is True
    reloaded = RecipeLibrary(tmp_path / "recipes.json")
    assert reloaded.comment("Punchy") == "Sunny day look"  # trimmed
    assert reloaded.set_comment("Punchy", "") is True
    assert reloaded.comment("Punchy") == ""


def test_library_comment_requires_known_recipe(tmp_path):
    """Comments cannot be attached to unknown names."""
    assert _library(tmp_path).set_comment("Nope", "x") is False


def test_library_comment_follows_rename_and_delete(tmp_path):
    """Rename carries the comment."""
    library = _library(tmp_path)
    library.set_comment("Punchy", "note")
    library.rename("Punchy", "Portra")
    assert library.comment("Portra") == "note"
    library.delete("Portra")
    library.add("Portra", Recipe())
    assert library.comment("Portra") == ""


def test_library_hotkey_round_trip(tmp_path):
    """An assigned number key survives reload and clears with None."""
    library = _library(tmp_path)
    assert library.hotkey_of("Punchy") is None
    assert library.set_hotkey("Punchy", 3) is True
    reloaded = RecipeLibrary(tmp_path / "recipes.json")
    assert reloaded.hotkey_of("Punchy") == 3
    assert reloaded.recipe_for_hotkey(3) == "Punchy"
    assert reloaded.set_hotkey("Punchy", None) is True
    assert reloaded.hotkey_of("Punchy") is None
    assert reloaded.recipe_for_hotkey(3) is None


def test_library_hotkey_moves_to_the_new_recipe(tmp_path):
    """Assigning a taken key takes it away from the previous holder."""
    library = _library(tmp_path)
    library.set_hotkey("Punchy", 1)
    assert library.set_hotkey("Mono", 1) is True
    assert library.hotkey_of("Mono") == 1
    assert library.hotkey_of("Punchy") is None


def test_library_hotkey_rejects_invalid_input(tmp_path):
    """Unknown recipes, keys outside 1 to 9, and no-ops all refuse."""
    library = _library(tmp_path)
    assert library.set_hotkey("Nope", 1) is False
    assert library.set_hotkey("Punchy", 0) is False
    assert library.set_hotkey("Punchy", 10) is False
    assert library.set_hotkey("Punchy", None) is False
    library.set_hotkey("Punchy", 2)
    assert library.set_hotkey("Punchy", 2) is False


def test_library_hotkey_follows_rename_and_delete(tmp_path):
    """Rename carries the key; delete frees it."""
    library = _library(tmp_path)
    library.set_hotkey("Punchy", 5)
    library.rename("Punchy", "Portra")
    assert library.hotkey_of("Portra") == 5
    library.delete("Portra")
    assert library.recipe_for_hotkey(5) is None


def test_library_hotkey_load_drops_duplicates_and_garbage(tmp_path):
    """A stored duplicate or out-of-range key is ignored on load."""
    path = tmp_path / "recipes.json"
    data = {
        "version": 2,
        "baseline": None,
        "folders": [],
        "recipes": {
            "A": {"film_simulation": "Provia", "hotkey": 4},
            "B": {"film_simulation": "Velvia", "hotkey": 4},
            "C": {"film_simulation": "Astia", "hotkey": 12},
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    library = RecipeLibrary(path)
    assert library.hotkey_of("A") == 4
    assert library.hotkey_of("B") is None
    assert library.hotkey_of("C") is None


def test_recipe_matches_is_case_insensitive():
    """Query words match regardless of case."""
    assert recipe_matches("velvia", "Landscape", "", "Velvia")
    assert recipe_matches("LAND", "Landscape", "", "Velvia")
    assert not recipe_matches("acros", "Landscape", "", "Velvia")


def test_recipe_matches_requires_every_word():
    """All query words must match, across any of the fields."""
    assert recipe_matches("velvia land", "Landscape", "punchy", "Velvia")
    assert not recipe_matches("velvia sea", "Landscape", "punchy", "Velvia")


def test_recipe_matches_searches_the_comment():
    """The comment is part of the searched text."""
    assert recipe_matches("punchy", "Landscape", "punchy greens", "Velvia")


def test_recipe_matches_empty_query_matches_everything():
    """An empty or blank query never filters anything out."""
    assert recipe_matches("", "Landscape", "", "Velvia")
    assert recipe_matches("   ", "anything", "", "")
