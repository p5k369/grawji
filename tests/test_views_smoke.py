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
    assert window._filmstrip.filter_button is window.filter_button
    window._filmstrip._rebuild_filter_menu(window.filter_button)
    assert window.filter_button.get_popover() is not None
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


def test_recipe_panel_provenance_line() -> None:
    """Provenance is empty in-camera, names the recipe otherwise."""
    from dataclasses import replace

    from grawji.recipe import Recipe
    from grawji.settings import FROM_IMAGE_LABEL
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.set_active(Recipe(), FROM_IMAGE_LABEL)
    panel.set_active(panel.get_recipe(), FROM_IMAGE_LABEL)
    assert panel.provenance == ""
    panel.set_active(Recipe(film_simulation="Velvia"), "Summer")
    panel.set_active(panel.get_recipe(), "Summer")
    assert panel.provenance == "grawji recipe: Summer"
    modified = replace(panel.get_recipe(), film_simulation="Acros")
    panel.set_recipe(modified)
    assert panel.provenance == "grawji recipe: Summer (modified)"


def test_recipe_panel_dr_ceiling_note() -> None:
    """The DR row warns only when the selection exceeds the shot's DR."""
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.set_recipe(Recipe(dynamic_range="DR400"))
    panel.set_dr_ceiling("DR200")
    pump()
    assert "DR200" in panel.dr_row.get_subtitle()
    panel.set_recipe(Recipe(dynamic_range="DR200"))
    pump()
    assert panel.dr_row.get_subtitle() == ""
    # No ceiling known: never warns.
    panel.set_recipe(Recipe(dynamic_range="DR400"))
    panel.set_dr_ceiling(None)
    pump()
    assert panel.dr_row.get_subtitle() == ""


def test_ev_never_counts_as_modified() -> None:
    """The per-image EV is not part of the recipe."""
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.set_active(Recipe(film_simulation="Velvia"), "Summer")
    panel.set_active(panel.get_recipe(), "Summer")
    panel.set_exposure(1.0)
    assert not panel.is_modified
    assert panel.provenance == "grawji recipe: Summer"


def test_modified_rows_get_the_tint() -> None:
    """Only rows that diverge from the applied recipe are tinted."""
    from dataclasses import replace

    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.set_active(Recipe(film_simulation="Velvia"), "Summer")
    assert not panel.film_row.has_css_class("recipe-modified")
    panel.set_recipe(
        replace(panel.get_recipe(), film_simulation="Acros", clarity=2)
    )
    assert panel.film_row.has_css_class("recipe-modified")
    assert panel._clarity_row.has_css_class("recipe-modified")
    assert not panel.dr_row.has_css_class("recipe-modified")
    panel.set_active(panel.get_recipe(), "Autumn")
    assert not panel.film_row.has_css_class("recipe-modified")
    assert not panel._clarity_row.has_css_class("recipe-modified")


def test_unsaved_recipe_arms_the_guards_without_row_marks() -> None:
    """A pasted recipe needs saving but shows no per-row edits."""
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.set_active(
        Recipe(film_simulation="Velvia"), "Kodachrome 64", unsaved=True
    )
    assert panel.needs_save
    assert not panel.is_modified
    assert not panel.film_row.has_css_class("recipe-modified")
    assert "(unsaved)" in panel.recipe_group.get_description()
    panel.set_active(panel.get_recipe(), "Kodachrome 64")
    assert not panel.needs_save


def test_panel_starts_unmodified() -> None:
    """A fresh panel must not count its own defaults as edits."""
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    assert not panel.is_modified


def test_set_active_is_never_born_modified() -> None:
    """Row normalization must not count as a user edit."""
    from grawji.camera.capabilities import capabilities_for_model
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    panel.apply_capabilities(capabilities_for_model("X-Pro2"))
    panel.set_active(Recipe(film_simulation="RealaAce"), "Reala look")
    assert not panel.is_modified


def test_modified_recipe_survives_image_open(
    window: Any, monkeypatch: Any
) -> None:
    """Unsaved recipe edits carry across image switches."""
    from dataclasses import replace

    renders: list[int] = []
    monkeypatch.setattr(window, "_render_preview", lambda: renders.append(1))
    panel = window.recipe_panel
    panel.set_active(panel.get_recipe(), "Summer")
    panel.set_recipe(replace(panel.get_recipe(), film_simulation="Acros"))
    assert panel.is_modified
    window._on_opened(window._generation, None)
    assert panel.active_label == "Summer"
    assert panel.is_modified
    assert renders == [1]


def test_open_raf_routes_files_and_folders(
    window: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    """A folder argument scans, a file scans its folder and selects."""
    scans: list[str] = []
    selected: list[str] = []
    monkeypatch.setattr(window, "_scan_folder", scans.append)
    monkeypatch.setattr(window._foldertree, "reveal_path", lambda _p: None)

    def fake_select(path: str) -> bool:
        selected.append(path)
        return True

    monkeypatch.setattr(window._filmstrip, "select_path", fake_select)
    window.open_raf(str(tmp_path))
    assert scans == [str(tmp_path)]
    assert selected == []
    raf = tmp_path / "a.RAF"
    raf.write_bytes(b"raf")
    window.open_raf(str(raf))
    assert scans == [str(tmp_path), str(tmp_path)]
    assert selected == [str(raf)]


def _manager(tmp_path: Any) -> Any:
    """A RecipeManagerDialog over a library holding ampersand names."""
    from grawji.views.recipe_manager import RecipeManagerDialog

    library = RecipeLibrary(tmp_path / "recipes.json")
    library.add("R&D", Recipe(film_simulation="Acros"), folder="B&W")
    noop1 = lambda *_a: None  # noqa: E731
    dialog = RecipeManagerDialog(
        library=library,
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


def test_mono_color_rows_gate_on_bw_and_capability(tmp_path: Any) -> None:
    """Monochromatic Color rows show only for B&W sims the body supports."""
    from grawji.camera.capabilities import Capabilities
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()

    panel.apply_capabilities(
        Capabilities(has_mono_wc=True, has_mono_mg=True, mono_max=18)
    )
    panel.set_recipe(
        Recipe(
            film_simulation="Acros", mono_warm_cool=12, mono_magenta_green=-6
        )
    )
    assert panel._mono_grid_row.get_visible()
    assert not panel._mono_wc_row.get_visible()
    assert panel.get_recipe().mono_warm_cool == 12
    assert panel.get_recipe().mono_magenta_green == -6

    # A color sim hides the grid.
    panel.set_recipe(Recipe(film_simulation="Velvia"))
    assert not panel._mono_grid_row.get_visible()
    assert not panel._mono_wc_row.get_visible()

    # A warm-cool-only body shows the slider, not the grid.
    panel.apply_capabilities(Capabilities(has_mono_wc=True, mono_max=9))
    panel.set_recipe(Recipe(film_simulation="Monochrome", mono_warm_cool=5))
    assert panel._mono_wc_row.get_visible()
    assert not panel._mono_grid_row.get_visible()
    assert panel.get_recipe().mono_warm_cool == 5
    assert panel.get_recipe().mono_magenta_green == 0


def test_wb_temp_freeform_keeps_arbitrary_kelvin(tmp_path: Any) -> None:
    """XProcessor5 keeps a non-preset Kelvin; older bodies snap to a preset."""
    from dataclasses import replace

    from grawji.camera.capabilities import BASELINE
    from grawji.recipe import Recipe
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()

    # Preset mode: 5100K is not a preset, so it snaps away.
    panel.apply_capabilities(BASELINE)
    panel.set_recipe(Recipe(color_temp=5100))
    assert panel.get_recipe().color_temp != 5100

    # Freeform mode (XProcessor5): the exact Kelvin is preserved.
    panel.apply_capabilities(replace(BASELINE, wb_temp_freeform=True))
    panel.set_recipe(Recipe(color_temp=5100))
    assert panel.get_recipe().color_temp == 5100


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
