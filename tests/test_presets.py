"""Tests for recipe preset storage and import/export."""

from grawji.presets import decode_presets, load_presets, save_presets
from grawji.recipe import Recipe


def test_save_load_round_trip(tmp_path):
    """Presets survive a save/load round-trip."""
    path = tmp_path / "presets.json"
    presets = {
        "Punchy": Recipe(film_simulation="Velvia", color=3),
        "Mono": Recipe(film_simulation="Acros", color=0),
    }
    save_presets(presets, path)
    loaded = load_presets(path)
    assert loaded == presets


def test_load_missing_returns_empty(tmp_path):
    """Loading a non-existent preset file returns an empty mapping."""
    assert load_presets(tmp_path / "nope.json") == {}


def test_decode_skips_non_recipe_entries():
    """Entries that are not recipe dicts are ignored."""
    presets = decode_presets({"ok": {"film_simulation": "Astia"}, "bad": 5})
    assert list(presets) == ["ok"]
    assert presets["ok"].film_simulation == "Astia"


def test_export_then_import_on_other_path(tmp_path):
    """A file saved as an export imports back to the same presets."""
    export = tmp_path / "export.json"
    presets = {"Soft": Recipe(highlights=-2, sharpness=-1)}
    save_presets(presets, export)
    assert load_presets(export) == presets
