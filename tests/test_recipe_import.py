"""Tests for importing many recipes at once."""

from __future__ import annotations

from pathlib import Path

from grawji.recipe import Recipe
from grawji.recipe_import import (
    Candidate,
    ImportReport,
    add_candidates,
    recipe_files,
    unique_name,
)
from grawji.recipes import RecipeLibrary


def _library(tmp_path: Path) -> RecipeLibrary:
    """An empty library."""
    return RecipeLibrary(tmp_path / "recipes.json")


def test_unique_name_numbers_a_taken_name() -> None:
    """A taken name gets the first free number."""
    assert unique_name("Portra", set()) == "Portra"
    assert unique_name("Portra", {"Portra"}) == "Portra 2"
    assert unique_name("Portra", {"Portra", "Portra 2"}) == "Portra 3"
    assert unique_name("  ", set()) == "Recipe"


def test_candidates_land_in_their_folders(tmp_path: Path) -> None:
    """A candidate's own folder wins over the default one."""
    library = _library(tmp_path)
    report = add_candidates(
        library,
        [
            Candidate("Warm", Recipe(film_simulation="Velvia")),
            Candidate("Mono", Recipe(film_simulation="Acros"), "B&W"),
        ],
        default_folder="Imported",
    )
    assert report.added == ["Warm", "Mono"]
    assert library.folder_of("Warm") == "Imported"
    assert library.folder_of("Mono") == "B&W"


def test_a_known_look_is_skipped(tmp_path: Path) -> None:
    """Neither the library's looks nor the batch's own are doubled."""
    library = _library(tmp_path)
    library.add("Mine", Recipe(film_simulation="Velvia", color=2))
    report = add_candidates(
        library,
        [
            Candidate("Same", Recipe(film_simulation="Velvia", color=2)),
            Candidate("New", Recipe(film_simulation="Astia")),
            Candidate("New again", Recipe(film_simulation="Astia")),
        ],
    )
    assert report.added == ["New"]
    assert report.duplicates == ["Same", "New again"]
    assert library.names == ["Mine", "New"]


def test_a_taken_name_is_never_overwritten(tmp_path: Path) -> None:
    """A different look under a taken name is stored beside it."""
    library = _library(tmp_path)
    library.add("Portra", Recipe(film_simulation="ClassicChrome"))
    report = add_candidates(
        library, [Candidate("Portra", Recipe(film_simulation="Astia"))]
    )
    assert report.added == ["Portra 2"]
    kept = library.get("Portra")
    assert kept is not None
    assert kept.film_simulation == "ClassicChrome"


def test_the_exposure_is_dropped(tmp_path: Path) -> None:
    """Exposure is per image and never enters the library."""
    library = _library(tmp_path)
    add_candidates(library, [Candidate("Bright", Recipe(exposure=1.0))])
    stored = library.get("Bright")
    assert stored is not None
    assert stored.exposure == 0.0


def test_recipe_files_map_subfolders_to_library_folders(
    tmp_path: Path,
) -> None:
    """The first directory level names the folder, deeper ones flatten."""
    (tmp_path / "Warm" / "Summer").mkdir(parents=True)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "top.FP1").write_text("x")
    (tmp_path / "Warm" / "gold.fp2").write_text("x")
    (tmp_path / "Warm" / "Summer" / "beach.JPG").write_text("x")
    (tmp_path / "Warm" / "notes.txt").write_text("x")
    (tmp_path / ".hidden" / "skip.FP1").write_text("x")
    found = {path.name: folder for path, folder in recipe_files(tmp_path)}
    assert found == {"top.FP1": None, "gold.fp2": "Warm", "beach.JPG": "Warm"}


def test_the_summary_names_every_outcome() -> None:
    """The toast line counts what was added, skipped and unreadable."""
    assert ImportReport().summary() == "Nothing to import."
    report = ImportReport(added=["a", "b"], duplicates=["c"], failed=["d"])
    assert report.summary() == (
        "2 imported, 1 already in the library, 1 not readable."
    )
