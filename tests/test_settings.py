"""Tests for persistent application settings."""

from grawji.settings import Settings, load_settings, save_settings


def test_defaults():
    """Loading from a non-existent path returns defaults."""
    settings = load_settings_from_missing()
    assert settings.load_recipe_from_image is True


def load_settings_from_missing():
    """Load settings from a path that does not exist."""
    from pathlib import Path

    return load_settings(Path("/no/such/grawji/settings.json"))


def test_round_trip(tmp_path):
    """Settings survive a save/load round-trip."""
    path = tmp_path / "settings.json"
    save_settings(
        Settings(
            load_recipe_from_image=False,
            sidebar_width=200,
            canvas_background="canvas-white",
            last_folder="/photos/raf",
            window_width=1400,
            window_height=900,
            last_export_dir="/photos/export",
        ),
        path,
    )
    loaded = load_settings(path)
    assert loaded.load_recipe_from_image is False
    assert loaded.sidebar_width == 200
    assert loaded.canvas_background == "canvas-white"
    assert loaded.last_folder == "/photos/raf"
    assert loaded.window_width == 1400
    assert loaded.window_height == 900
    assert loaded.last_export_dir == "/photos/export"


def test_last_export_dir_defaults_empty():
    """last_export_dir starts empty so the dialog uses the system default."""
    assert Settings().last_export_dir == ""


def test_view_state_round_trip(tmp_path):
    """Expanded folders and the last image survive a save/load round-trip."""
    path = tmp_path / "settings.json"
    save_settings(
        Settings(
            expanded_folders=["/photos", "/photos/raf"],
            last_image="/photos/raf/DSCF0001.RAF",
        ),
        path,
    )
    loaded = load_settings(path)
    assert loaded.expanded_folders == ["/photos", "/photos/raf"]
    assert loaded.last_image == "/photos/raf/DSCF0001.RAF"


def test_view_state_defaults_empty():
    """View-state fields start empty so a fresh install restores nothing."""
    assert Settings().expanded_folders == []
    assert Settings().last_image == ""


def test_unknown_keys_ignored(tmp_path):
    """Unknown keys in the file are ignored."""
    path = tmp_path / "settings.json"
    path.write_text('{"load_recipe_from_image": false, "bogus": 1}')
    assert load_settings(path).load_recipe_from_image is False


def test_corrupt_file_returns_defaults(tmp_path):
    """A corrupt settings file falls back to defaults."""
    path = tmp_path / "settings.json"
    path.write_text("not json{")
    assert load_settings(path).load_recipe_from_image is True
