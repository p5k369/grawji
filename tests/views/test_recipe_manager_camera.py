"""GTK smoke tests for the recipe manager's camera-bank pane."""

from __future__ import annotations

from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from grawji.camera.capabilities import capabilities_for_model
from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from tests.gui_support import StubManagerHost, pump

pytestmark = pytest.mark.gui

_NOOP = lambda *_a: None  # noqa: E731


def _dialog(tmp_path, *, model, on_transfer=_NOOP):
    """A manager dialog over a two-recipe library for the given body."""
    from grawji.views.recipe_manager import RecipeManagerDialog

    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Velvia look", Recipe(film_simulation="Velvia"))
    library.add("Acros look", Recipe(film_simulation="Acros"))
    host = StubManagerHost(
        get_capabilities=lambda: capabilities_for_model(model),
        get_model=lambda: model,
        transfer_banks=on_transfer,
    )
    dialog = RecipeManagerDialog(library=library, host=host)
    pump()
    return dialog


def test_camera_pane_shows_banks_when_connected(tmp_path: Any) -> None:
    """A connected body shows seven bank cards and enables Transfer."""
    dialog = _dialog(tmp_path, model="X-T3")
    assert dialog.camera_pane.camera_stack.get_visible_child_name() == "banks"
    assert len(dialog.camera_pane._banks) == 7
    assert dialog.transfer_button.get_sensitive()


def test_camera_pane_placeholder_without_camera(tmp_path: Any) -> None:
    """Without a camera the pane shows the placeholder, Transfer off."""
    dialog = _dialog(tmp_path, model=None)
    assert dialog.camera_pane.camera_stack.get_visible_child_name() == "none"
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
    assert (
        dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
        is True
    )
    assert (
        dialog.camera_pane._on_bank_drop(None, "Acros look", 0.0, 0.0, 3)
        is True
    )
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["recipes"] == {0: "Velvia look", 3: "Acros look"}
    assert captured["fs"] == {}


def test_drop_rejects_unknown_recipe(tmp_path: Any) -> None:
    """A drop payload that is no saved recipe is refused."""
    dialog = _dialog(tmp_path, model="X-T3")
    assert (
        dialog.camera_pane._on_bank_drop(None, "does not exist", 0.0, 0.0, 0)
        is False
    )


def test_transfer_finished_clears_assignments(tmp_path: Any) -> None:
    """After a finished transfer the assignments reset to empty."""
    dialog = _dialog(tmp_path, model="X-T3")
    dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    assert dialog.camera_pane._bank_recipe == {0: "Velvia look"}
    dialog.on_transfer_finished()
    assert dialog.camera_pane._bank_recipe == {}


def test_a_drop_names_the_bank_after_the_recipe(tmp_path: Any) -> None:
    """Named or empty, a bank takes the name of what goes into it.

    Writing the name is also what materializes an empty slot on the body
    (X-E5: a value-only write leaves the bank "empty" in the menu).
    """
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-E5",
        on_transfer=lambda a, n, fs: captured.update(names=n),
    )
    dialog.set_bank_names("X-E5", ["BW", "", "", "", "", "", ""])
    dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    dialog.camera_pane._on_bank_drop(None, "Acros look", 0.0, 0.0, 4)
    assert dialog.camera_pane._banks[0].bank_name == "Velvia look"
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["names"] == {0: "Velvia look", 4: "Acros look"}


def test_a_second_drop_renames_again(tmp_path: Any) -> None:
    """The name follows the latest drop until the user types one."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-E5",
        on_transfer=lambda a, n, fs: captured.update(names=n),
    )
    dialog.set_bank_names("X-E5", ["BW", "", "", "", "", "", ""])
    pane = dialog.camera_pane
    pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    pane._on_bank_drop(None, "Acros look", 0.0, 0.0, 0)
    assert pane._banks[0].bank_name == "Acros look"
    pane._banks[0].set_bank_name("MINE")
    pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["names"] == {0: "MINE"}


def test_no_default_names_when_none_were_loaded(tmp_path: Any) -> None:
    """Without loaded bank names (gen3 has none) no defaults are sent."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X100F",
        on_transfer=lambda a, n, fs: captured.update(names=n),
    )
    dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 4)
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["names"] == {}


def test_explicit_rename_beats_the_default_name(tmp_path: Any) -> None:
    """A typed name wins over the recipe-name default."""
    captured: dict[str, Any] = {}
    dialog = _dialog(
        tmp_path,
        model="X-E5",
        on_transfer=lambda a, n, fs: captured.update(names=n),
    )
    dialog.set_bank_names("X-E5", [""] * 7)
    dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 2)
    dialog.camera_pane._banks[2].set_bank_name("SUMMER")
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["names"] == {2: "SUMMER"}


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
    dialog.set_bank_names("X-T3", ["STD", "PORTRA", "", "", "", "", ""])
    dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
    dialog.camera_pane._banks[1].set_bank_name("KODAK")  # rename C2
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["recipes"] == {0: "Velvia look"}
    assert captured["names"] == {0: "Velvia look", 1: "KODAK"}


def test_banks_are_renamable_on_every_body(tmp_path: Any) -> None:
    """Bank rows look identical on every body, rename button included."""
    for model in ("X-T3", "X100F"):
        dialog = _dialog(tmp_path, model=model)
        assert all(
            b.edit_button.get_visible() for b in dialog.camera_pane._banks
        )


def test_a_drop_shows_the_recipe_on_the_row(tmp_path: Any) -> None:
    """The row names the pending recipe until the transfer is done."""
    dialog = _dialog(tmp_path, model="X-E5")
    pane = dialog.camera_pane
    pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 1)
    pane._on_fs_drop(None, "Acros look", 0.0, 0.0, 0)
    assert "Velvia look" in pane._banks[1].get_subtitle()
    assert not pane._banks[1].thumb.get_visible()  # it has no picture
    assert "Acros look" in pane._fs_cards[0].get_subtitle()
    assert not pane._fs_cards[0].edit_button.get_visible()
    dialog.on_transfer_finished()
    assert pane._banks[1].get_subtitle() == "Drop a recipe here"
    assert not pane._banks[1].thumb.get_visible()


def test_fs_cards_only_on_bodies_with_an_fs_layout(tmp_path: Any) -> None:
    """FS dial cards appear on the X-E5 but not on the X-T3."""
    xe5 = _dialog(tmp_path, model="X-E5")
    assert len(xe5.camera_pane._banks) == 7
    assert len(xe5.camera_pane._fs_cards) == 3  # FS1-FS3
    xt3 = _dialog(tmp_path, model="X-T3")
    assert xt3.camera_pane._fs_cards == []


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
    assert (
        dialog.camera_pane._on_bank_drop(None, "Velvia look", 0.0, 0.0, 0)
        is True
    )
    assert (
        dialog.camera_pane._on_fs_drop(None, "Acros look", 0.0, 0.0, 2) is True
    )
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
    dialog.camera_pane._on_fs_drop(None, "Velvia look", 0.0, 0.0, 0)
    pump()
    dialog._on_transfer_clicked(None)
    assert captured["fs"] == {0: "Velvia look"}
    assert captured["recipes"] == {}


def test_transfer_finished_clears_fs_assignments(tmp_path: Any) -> None:
    """A finished transfer resets FS assignments too."""
    dialog = _dialog(tmp_path, model="X-E5")
    dialog.camera_pane._on_fs_drop(None, "Velvia look", 0.0, 0.0, 1)
    assert dialog.camera_pane._fs_recipe == {1: "Velvia look"}
    dialog.on_transfer_finished()
    assert dialog.camera_pane._fs_recipe == {}


def test_deviceinfo_model_rebuilds_the_pane(tmp_path: Any) -> None:
    """The camera's own model corrects a USB product-id guess."""
    dialog = _dialog(tmp_path, model="Camera")
    pane = dialog.camera_pane
    assert len(pane._banks) == 7  # baseline guess

    dialog.set_bank_names("X-S20", ["BW", "STD", "", ""])
    assert len(pane._banks) == 4
    assert "X-S20" in pane.camera_header.get_label()
    assert pane._banks[0].loaded == "BW"


def test_matching_model_does_not_rebuild(tmp_path: Any) -> None:
    """A confirming model keeps the built cards untouched."""
    dialog = _dialog(tmp_path, model="X-T3")
    pane = dialog.camera_pane
    first_card = pane._banks[0]
    dialog.set_bank_names("X-T3", ["STD"] + [""] * 6)
    assert pane._banks[0] is first_card
    assert first_card.loaded == "STD"


def _carrier_dialog(tmp_path, **hooks):
    """A manager dialog on a connected X-E5 with the given carrier hooks."""
    from grawji.views.recipe_manager import RecipeManagerDialog

    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("Velvia look", Recipe(film_simulation="Velvia"))
    host = StubManagerHost(get_model=lambda: "X-E5", **hooks)
    dialog = RecipeManagerDialog(library=library, host=host)
    pump()
    return dialog


def test_camera_pane_lists_the_registered_carriers(tmp_path: Any) -> None:
    """Each range names its file or the higher one standing in."""
    dialog = _carrier_dialog(
        tmp_path,
        carriers_for=lambda model: {400: "/carriers/xe5-cap-on.RAF"},
        add_carrier=lambda model, path: None,
        forget_carrier=lambda model, dr: None,
        current_raf=lambda: "/photos/other.RAF",
    )
    pane = dialog.camera_pane
    assert pane.carrier_rows[400].get_subtitle() == "xe5-cap-on.RAF"
    assert pane.carrier_rows[400].clear_button.get_visible()
    assert pane.carrier_rows[200].get_subtitle() == (
        "Missing, served by the DR400 carrier"
    )
    assert not pane.carrier_rows[200].clear_button.get_visible()
    assert pane.carrier_current_button.get_visible()
    assert not pane.carrier_problem.get_visible()


def test_camera_pane_does_not_offer_a_registered_raf(tmp_path: Any) -> None:
    """The open RAF is not offered again once it is a carrier."""
    dialog = _carrier_dialog(
        tmp_path,
        carriers_for=lambda model: {400: "/carriers/xe5.RAF"},
        add_carrier=lambda model, path: None,
        forget_carrier=lambda model, dr: None,
        current_raf=lambda: "/carriers/xe5.RAF",
    )
    assert not dialog.camera_pane.carrier_current_button.get_visible()


def test_camera_pane_reports_a_refused_carrier(tmp_path: Any) -> None:
    """A refusal from the host shows under the list; forgetting clears it."""
    added: list[tuple[str, str]] = []
    forgotten: list[tuple[str, int]] = []

    def add_carrier(model: str, path: str) -> str | None:
        added.append((model, path))
        return "This RAF was shot through a lens."

    dialog = _carrier_dialog(
        tmp_path,
        carriers_for=lambda model: {100: "/carriers/xe5-dr100.RAF"},
        add_carrier=add_carrier,
        forget_carrier=lambda model, dr: forgotten.append((model, dr)),
        current_raf=lambda: "/photos/lensed.RAF",
    )
    pane = dialog.camera_pane
    assert pane.carrier_rows[400].get_subtitle() == "Missing"
    pane.carrier_current_button.emit("clicked")
    pump()
    assert added == [("X-E5", "/photos/lensed.RAF")]
    assert pane.carrier_problem.get_visible()
    assert "lens" in pane.carrier_problem.get_label()
    pane.carrier_rows[100].clear_button.emit("clicked")
    pump()
    assert forgotten == [("X-E5", 100)]
    assert not pane.carrier_problem.get_visible()


def test_camera_pane_hides_the_carrier_row_without_hooks(
    tmp_path: Any,
) -> None:
    """A host without carrier support shows banks only."""
    dialog = _carrier_dialog(tmp_path)
    assert not dialog.camera_pane.carrier_section.get_visible()


def test_a_click_selects_and_modifiers_extend(tmp_path: Any) -> None:
    """Plain selects one, Ctrl toggles, Shift takes the range."""
    dialog = _dialog(tmp_path, model="X-T3")
    dialog.select("Velvia look")
    assert dialog.selected == ["Velvia look"]
    assert dialog.details.get_visible_child_name() == "one"
    assert dialog.details.name_label.get_label() == "Velvia look"
    dialog.select("Acros look", toggle=True)
    assert dialog.selected == ["Velvia look", "Acros look"]
    assert dialog.details.get_visible_child_name() == "none"
    assert dialog.export_button.get_sensitive()
    dialog.select("Acros look", toggle=True)
    assert dialog.selected == ["Velvia look"]
    dialog.select("Velvia look")
    dialog.select("Acros look", extend=True)
    assert dialog.selected == ["Velvia look", "Acros look"]


def test_nothing_selected_disables_the_bulk_actions(tmp_path: Any) -> None:
    """The action bar acts on a selection only."""
    dialog = _dialog(tmp_path, model="X-T3")
    assert dialog.selected == []
    assert not dialog.export_button.get_sensitive()
    assert not dialog.delete_button.get_sensitive()
    assert not dialog.move_button.get_sensitive()


def test_details_buttons_assign_to_the_camera(tmp_path: Any) -> None:
    """A slot button in the details pane assigns like a drop does."""
    dialog = _dialog(tmp_path, model="X-E5")
    dialog.select("Velvia look")
    labels = [label for label, _ in dialog.camera_pane.targets()]
    assert labels[:2] == ["C1", "C2"]
    assert labels[-1] == "FS3"
    dialog._assign_target("Velvia look", 1)
    dialog._assign_target("Velvia look", labels.index("FS1"))
    assert dialog.camera_pane._bank_recipe == {1: "Velvia look"}
    assert dialog.camera_pane._fs_recipe == {0: "Velvia look"}


def test_an_assignment_can_be_taken_back(tmp_path: Any) -> None:
    """Undo drops the pending recipe and the name it gave the bank."""
    dialog = _dialog(tmp_path, model="X-E5")
    dialog.set_bank_names("X-E5", ["BW", "", "", "", "", "", ""])
    pane = dialog.camera_pane
    pane.assign_bank(0, "Velvia look")
    assert pane._banks[0].bank_name == "Velvia look"
    pane._banks[0].clear_button.emit("clicked")
    assert pane._bank_recipe == {}
    assert pane._banks[0].bank_name == "BW"
    assert not pane._banks[0].clear_button.get_visible()


def test_the_selection_survives_a_refresh(tmp_path: Any) -> None:
    """Rebuilding the list keeps what is still there selected."""
    dialog = _dialog(tmp_path, model="X-T3")
    dialog.select_names(["Velvia look", "Acros look"])
    dialog._library.delete("Acros look")
    dialog.refresh()
    assert dialog.selected == ["Velvia look"]


def test_a_drag_near_the_edge_scrolls_the_list(tmp_path: Any) -> None:
    """Hovering a drag at the top or bottom sets the scroll direction."""
    dialog = _dialog(tmp_path, model="X-T3")
    height = dialog.list_scroll.get_height()
    dialog._on_drag_motion(None, 10.0, 4.0)
    assert dialog._scroll_speed < 0
    dialog._on_drag_motion(None, 10.0, height + 200.0)
    assert dialog._scroll_speed > 0
    dialog._on_drag_leave()
    assert dialog._scroll_speed == 0.0
    assert dialog._on_scroll_tick(None, None) is False
