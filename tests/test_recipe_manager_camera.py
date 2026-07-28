"""GTK smoke tests for the recipe manager's camera-bank pane."""

from __future__ import annotations

from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from grawji.capabilities import capabilities_for_model
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from tests.gui_support import pump

pytestmark = pytest.mark.gui

_NOOP = lambda *_a: None  # noqa: E731


def _dialog(tmp_path, *, model, on_transfer=_NOOP):
    """A manager dialog over a two-recipe library for the given body."""
    from grawji.views.recipe_manager import RecipeManagerDialog

    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Velvia look", Recipe(film_simulation="Velvia"))
    library.add("Acros look", Recipe(film_simulation="Acros"))
    dialog = RecipeManagerDialog(
        library=library,
        on_import=_NOOP,
        on_export=_NOOP,
        on_delete=_NOOP,
        on_rename=_NOOP,
        on_move=_NOOP,
        on_set_baseline=_NOOP,
        on_place_recipe=_NOOP,
        on_create_folder=_NOOP,
        on_rename_folder=_NOOP,
        on_delete_folder=_NOOP,
        on_reorder_folder=_NOOP,
        get_capabilities=lambda: capabilities_for_model(model),
        get_model=lambda: model,
        on_transfer=on_transfer,
    )
    pump()
    return dialog


def test_camera_pane_shows_banks_when_connected(tmp_path: Any) -> None:
    """A connected body shows seven bank cards and enables Transfer."""
    dialog = _dialog(tmp_path, model="X-T3")
    assert dialog.camera_stack.get_visible_child_name() == "banks"
    assert len(dialog._banks) == 7
    assert dialog.transfer_button.get_sensitive()


def test_camera_pane_placeholder_without_camera(tmp_path: Any) -> None:
    """Without a camera the pane shows the placeholder, Transfer off."""
    dialog = _dialog(tmp_path, model=None)
    assert dialog.camera_stack.get_visible_child_name() == "none"
    assert not dialog.transfer_button.get_sensitive()


def test_drop_assigns_recipe_and_transfer_collects(tmp_path: Any) -> None:
    """Dropped recipes land in their slots and Transfer collects them."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-T3",
        on_transfer=lambda a, n, fs: captured.update(
            recipes=a, names=n, fs=fs
        ),
    )
    assert dialog._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0) is True
    assert dialog._on_bank_drop(None, "Acros look", 0.0, 0.0, 3) is True
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["recipes"] == {0: "Velvia look", 3: "Acros look"}
    assert captured["fs"] == {}


def test_drop_rejects_unknown_recipe(tmp_path: Any) -> None:
    """A drop payload that is no saved recipe is refused."""
    dialog = _dialog(tmp_path, model="X-T3")
    assert dialog._on_bank_drop(None, "does not exist", 0.0, 0.0, 0) is False


def test_transfer_finished_clears_assignments(tmp_path: Any) -> None:
    """After a finished transfer the assignments reset to empty."""
    dialog = _dialog(tmp_path, model="X-T3")
    dialog._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    assert dialog._bank_recipe == {0: "Velvia look"}
    dialog.on_transfer_finished()
    assert dialog._bank_recipe == {}


def test_bank_rename_is_collected(tmp_path: Any) -> None:
    """Only names the user changed are handed to the transfer."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-T3",
        on_transfer=lambda a, n, fs: captured.update(
            recipes=a, names=n, fs=fs
        ),
    )
    dialog.set_bank_names(["STD", "PORTRA", "", "", "", "", ""])
    dialog._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    dialog._banks[1]["name_label"].set_label("KODAK")  # rename C2
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["recipes"] == {0: "Velvia look"}
    assert captured["names"] == {1: "KODAK"}


def test_name_row_visible_on_every_body(tmp_path: Any) -> None:
    """Bank cards look identical on every body, name row included."""
    for model in ("X-T3", "X100F"):
        dialog = _dialog(tmp_path, model=model)
        assert all(b["name_row"].get_visible() for b in dialog._banks)


def test_fs_cards_only_on_bodies_with_an_fs_layout(tmp_path: Any) -> None:
    """FS dial cards appear on the X-E5 but not on the X-T3."""
    xe5 = _dialog(tmp_path, model="X-E5")
    assert len(xe5._banks) == 7
    assert len(xe5._fs_cards) == 3  # FS1-FS3
    xt3 = _dialog(tmp_path, model="X-T3")
    assert xt3._fs_cards == []


def test_fs_drop_and_transfer_collects_separately(tmp_path: Any) -> None:
    """FS drops land in their own channel, apart from the C banks."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-E5",
        on_transfer=lambda a, n, fs: captured.update(
            recipes=a, names=n, fs=fs
        ),
    )
    assert dialog._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0) is True
    assert dialog._on_fs_drop(None, "Acros look", 0.0, 0.0, 2) is True
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["recipes"] == {0: "Velvia look"}
    assert captured["fs"] == {2: "Acros look"}


def test_fs_only_transfer_is_collected(tmp_path: Any) -> None:
    """A transfer with only FS assignments still fires."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-E5",
        on_transfer=lambda a, n, fs: captured.update(
            recipes=a, names=n, fs=fs
        ),
    )
    dialog._on_fs_drop(None, "Velvia look", 0.0, 0.0, 0)
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["fs"] == {0: "Velvia look"}
    assert captured["recipes"] == {}


def test_transfer_finished_clears_fs_assignments(tmp_path: Any) -> None:
    """A finished transfer resets FS assignments too."""
    dialog = _dialog(tmp_path, model="X-E5")
    dialog._on_fs_drop(None, "Velvia look", 0.0, 0.0, 1)
    assert dialog._fs_recipe == {1: "Velvia look"}
    dialog.on_transfer_finished()
    assert dialog._fs_recipe == {}
