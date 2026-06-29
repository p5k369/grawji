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
            show_info_panel=True,
            canvas_background="canvas-white",
            last_folder="/photos/raf",
            window_width=1400,
            window_height=900,
        ),
        path,
    )
    loaded = load_settings(path)
    assert loaded.load_recipe_from_image is False
    assert loaded.show_info_panel is True
    assert loaded.canvas_background == "canvas-white"
    assert loaded.last_folder == "/photos/raf"
    assert loaded.window_width == 1400
    assert loaded.window_height == 900


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
