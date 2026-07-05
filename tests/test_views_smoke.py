"""GTK construction smoke tests plus targeted regression checks."""

from __future__ import annotations

from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk

from grawji.recipe import Recipe
from grawji.recipes import RecipeLibrary
from tests.gui_support import pump, walk

pytestmark = pytest.mark.gui


def test_main_window_builds(window: Any) -> None:
    """The main window and its composite children build from .ui cleanly."""
    assert window.preview_view is not None
    assert window.recipe_panel is not None
    assert window.export_button is not None
    # The composite children are the custom template types, proving their
    # own .ui files parsed and registered.
    assert window.preview_view.__gtype_name__ == "GrawjiPreviewView"
    assert window.recipe_panel.__gtype_name__ == "GrawjiRecipePanel"


def test_preview_view_builds_standalone() -> None:
    """PreviewView builds on its own and exposes its expected surface."""
    from grawji.views.preview_view import PreviewView

    view = PreviewView()
    pump()
    assert view.scroll is not None
    view.set_background("canvas-dark")
    view.set_show_histogram(True)
    view.set_status("ready")
    assert view.rotation == 0


def test_recipe_panel_builds_and_signals() -> None:
    """RecipePanel builds and carries its changed/apply-recipe signals."""
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    assert GObject.signal_lookup("changed", type(panel)) != 0
    assert GObject.signal_lookup("apply-recipe", type(panel)) != 0


def _manager(tmp_path: Any) -> Any:
    """A RecipeManagerDialog over a library holding ampersand names."""
    from grawji.views.recipe_manager import RecipeManagerDialog

    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("R&D", Recipe(film_simulation="Acros"), folder="B&W")
    noop1 = lambda *_a: None  # noqa: E731
    dialog = RecipeManagerDialog(
        library=library,
        on_import=noop1,
        on_export=noop1,
        on_delete=noop1,
        on_rename=noop1,
        on_move=noop1,
        on_set_baseline=noop1,
        on_place_recipe=noop1,
        on_create_folder=noop1,
        on_rename_folder=noop1,
        on_delete_folder=noop1,
        on_reorder_folder=noop1,
    )
    pump()
    return dialog


def test_recipe_manager_escapes_ampersand_names(tmp_path: Any) -> None:
    """Folder and recipe names with '&' render literally, not as markup.

    A folder group title is always Pango markup, so it must be escaped.
    A recipe row disables markup instead and keeps the raw title. We check
    the rendered label text a user actually sees, not the stored title,
    since libadwaita versions differ on how the title round-trips. A
    regression that fails to escape "&" breaks Pango markup and the header
    stops rendering that text at all.
    """
    dialog = _manager(tmp_path)
    root = dialog.get_child()
    widgets = walk(root) if root else []

    label_texts = {w.get_text() for w in widgets if isinstance(w, Gtk.Label)}
    assert "B&W" in label_texts, "folder header must render '&' literally"
    assert "R&D" in label_texts, "recipe row must render '&' literally"

    # The recipe row keeps its raw title with markup disabled.
    rows = [w for w in widgets if isinstance(w, Adw.ActionRow)]
    recipe_row = next((r for r in rows if r.get_title() == "R&D"), None)
    assert recipe_row is not None, "recipe row title must be raw text"
    assert recipe_row.get_use_markup() is False


def test_recipe_panel_menu_handles_ampersand(tmp_path: Any) -> None:
    """Building the picker menu with '&' names does not crash or garble."""
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    panel.set_recipe_menu(["Plain"], [("B&W", ["R&D"])])
    pump()
    panel.sync_combo("R&D")
    assert panel.recipe_button.get_label() == "R&D"


def test_export_button_stays_wired(window: Any) -> None:
    """The Export button keeps a click handler."""
    signal_id = GObject.signal_lookup("clicked", type(window.export_button))
    handler = GObject.signal_handler_find(
        window.export_button,
        GObject.SignalMatchType.ID,
        signal_id,
        0,
        None,
        None,
        None,
    )
    assert handler != 0, "Export button has no clicked handler"


def test_compare_guard_drops_stale_and_toggled_off(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late baseline render is ignored unless still current and enabled."""
    calls: list[Any] = []
    monkeypatch.setattr(
        window.preview_view,
        "set_compare_baseline",
        lambda jpeg: calls.append(("baseline", jpeg)),
    )
    monkeypatch.setattr(
        window.preview_view,
        "set_compare",
        lambda *, on: calls.append(("compare", on)),
    )

    window._generation = 5

    # Stale generation: dropped outright.
    window._on_baseline_rendered(4, b"jpeg")
    assert calls == []

    # Current generation but compare toggled off: still dropped.
    window._compare_action.set_state(GLib.Variant.new_boolean(False))
    window._on_baseline_rendered(5, b"jpeg")
    assert calls == []

    # Current and enabled: the baseline is fed into the split view.
    window._compare_action.set_state(GLib.Variant.new_boolean(True))
    window._on_baseline_rendered(5, b"jpeg")
    assert ("baseline", b"jpeg") in calls
    assert ("compare", True) in calls
