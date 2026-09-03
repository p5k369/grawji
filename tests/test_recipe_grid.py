"""The recipe tryout grid tests: picking, rendering, stopping, orientation."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GExiv2", "0.10")

from gi.repository import GdkPixbuf, GExiv2

from grawji.imaging.render import trim_letterbox as _trim_letterbox
from grawji.recipe import Recipe
from grawji.views.recipe_grid import (
    RecipeGridDialog,
    _decode_scaled,
)

pytestmark = pytest.mark.gui


def tiny_jpeg(width: int = 8, height: int = 6) -> bytes:
    """A minimal valid JPEG to stand in for a camera preview."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill(0x808080FF)
    ok, data = pixbuf.save_to_bufferv("jpeg", [], [])
    assert ok
    return bytes(data)


def cards_of(dialog: RecipeGridDialog) -> list[object]:
    """The grid's card widgets, in order."""
    out: list[object] = []
    child = dialog.grid.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def make_dialog(entries, render, picked=None):
    """Build the dialog with a fake render and a pick recorder."""
    picked = picked if picked is not None else []
    return RecipeGridDialog(
        groups=[("", entries)], render=render, on_pick=picked.append
    )


def test_folder_groups_flatten_in_order() -> None:
    """Grouped entries render in group order with one check per row."""
    groups = [
        ("", [("x", "x", Recipe())]),
        ("Portra", [("a", "a", Recipe()), ("b", "b", Recipe())]),
    ]
    dialog = RecipeGridDialog(
        groups=groups, render=lambda *a: None, on_pick=lambda _k: None
    )
    assert len(dialog._checks) == 3
    assert [e[0] for e in dialog._entries] == ["x", "a", "b"]


def test_opens_on_checklist_all_picked() -> None:
    """The dialog starts on the pick page with everything checked."""
    dialog = make_dialog(
        [("a", "a", Recipe()), ("b", "b", Recipe())], lambda *a: None
    )
    assert dialog.stack.get_visible_child_name() == "pick"
    assert all(check.get_active() for check in dialog._checks)
    assert "2 of 2" in dialog.progress_label.get_label()


def test_renders_only_the_picked_recipes() -> None:
    """Unchecked recipes are skipped; cards appear in order."""
    jpeg = tiny_jpeg()
    rendered = []
    picked: list[str] = []

    def render(recipe, on_done, _on_error):
        rendered.append(recipe)
        on_done(jpeg)

    entries = [
        ("__from_image__", "From image", Recipe()),
        ("Kodak", "Kodak", Recipe(film_simulation=13)),
        ("Acros", "Acros", Recipe(film_simulation=14)),
    ]
    dialog = make_dialog(entries, render, picked)
    dialog._checks[1].set_active(False)
    dialog._on_start()
    assert len(rendered) == 2
    cards = cards_of(dialog)
    assert len(cards) == 2
    # FlowBox wraps each card in a GtkFlowBoxChild
    cards[1].get_child().emit("clicked")
    assert picked == ["Acros"]


def test_nothing_picked_stays_on_checklist() -> None:
    """Render with nothing checked does not start."""
    calls = []
    dialog = make_dialog([("a", "a", Recipe())], lambda *a: calls.append(1))
    dialog._checks[0].set_active(False)
    dialog._on_start()
    assert not calls
    assert dialog.stack.get_visible_child_name() == "pick"


def test_stop_bails_out_after_current_render() -> None:
    """Stop halts the queue after the in-flight render."""
    jpeg = tiny_jpeg()
    calls = []

    def render(_recipe, on_done, _on_error):
        calls.append(1)
        dialog._on_stop()  # user hits Stop mid-render
        on_done(jpeg)

    entries = [("a", "a", Recipe()), ("b", "b", Recipe())]
    dialog = make_dialog(entries, render)
    dialog._on_start()
    assert len(calls) == 1
    assert len(cards_of(dialog)) == 1
    assert dialog.progress_label.get_label() == "Stopped."


def test_close_stops_the_run() -> None:
    """No further renders are requested after the dialog closes."""
    jpeg = tiny_jpeg()
    calls = []

    def render(_recipe, on_done, _on_error):
        calls.append(1)
        dialog._on_closed()
        on_done(jpeg)

    entries = [("a", "a", Recipe()), ("b", "b", Recipe())]
    dialog = make_dialog(entries, render)
    dialog._on_start()
    assert len(calls) == 1


def test_render_error_stops_the_run() -> None:
    """A render failure surfaces in the progress line and halts."""
    calls = []

    def render(_recipe, _on_done, on_error):
        calls.append(1)
        on_error(RuntimeError("0x2019"))

    entries = [("a", "a", Recipe()), ("b", "b", Recipe())]
    dialog = make_dialog(entries, render)
    dialog._on_start()
    assert len(calls) == 1
    assert "failed" in dialog.progress_label.get_label()


def test_fallback_previews_use_their_own_orientation(tmp_path) -> None:
    """A JPEG with an EXIF orientation rotates by it, exactly once."""
    source = tmp_path / "p.jpg"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 6)
    pixbuf.fill(0x808080FF)
    pixbuf.savev(str(source), "jpeg", [], [])
    meta = GExiv2.Metadata()
    meta.open_path(str(source))
    meta.try_set_tag_long("Exif.Image.Orientation", 6)
    meta.save_file(str(source))
    decoded = _decode_scaled(source.read_bytes(), 360, 6)
    assert (decoded.get_width(), decoded.get_height()) == (6, 8)


def test_bare_thumbs_use_the_raf_orientation() -> None:
    """A bare camera thumbnail rotates by the RAF's orientation."""
    decoded = _decode_scaled(tiny_jpeg(8, 6), 360, 6)
    assert (decoded.get_width(), decoded.get_height()) == (6, 8)
    upright = _decode_scaled(tiny_jpeg(8, 6), 360, 1)
    assert (upright.get_width(), upright.get_height()) == (8, 6)


def test_letterbox_bars_trimmed() -> None:
    """Black 4:3 letterbox bars are cut off the camera thumbnail."""
    framed = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 24, 18)
    framed.fill(0x000000FF)
    inner = framed.new_subpixbuf(0, 3, 24, 12)
    inner.fill(0x808080FF)
    trimmed = _trim_letterbox(framed)
    assert (trimmed.get_width(), trimmed.get_height()) == (24, 12)


def test_trim_keeps_untouched_images() -> None:
    """An image without bars passes through unchanged."""
    plain = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 24, 18)
    plain.fill(0x808080FF)
    assert _trim_letterbox(plain) is plain


def test_master_checkbox_drives_all_rows() -> None:
    """The All-recipes row toggles everything and mirrors partials."""
    dialog = make_dialog(
        [("a", "a", Recipe()), ("b", "b", Recipe())], lambda *a: None
    )
    assert dialog._master.get_active()
    dialog._master.set_active(False)
    assert not any(check.get_active() for check in dialog._checks)
    dialog._checks[0].set_active(True)
    assert dialog._master.get_inconsistent()
    dialog._checks[1].set_active(True)
    assert dialog._master.get_active()
    assert not dialog._master.get_inconsistent()


def test_master_checks_all_from_none() -> None:
    """From all-off, the master checks every row, not just the first."""
    dialog = make_dialog(
        [("a", "a", Recipe()), ("b", "b", Recipe()), ("c", "c", Recipe())],
        lambda *a: None,
    )
    dialog._master.set_active(False)
    assert not any(check.get_active() for check in dialog._checks)
    dialog._master.set_active(True)
    assert all(check.get_active() for check in dialog._checks)
    assert not dialog._master.get_inconsistent()


def test_folder_check_toggles_only_its_folder() -> None:
    """A folder header check adds/removes just that folder's recipes."""
    groups = [
        ("Color", [("a", "a", Recipe()), ("b", "b", Recipe())]),
        ("B&W", [("c", "c", Recipe())]),
    ]
    dialog = RecipeGridDialog(
        groups=groups, render=lambda *a: None, on_pick=lambda _k: None
    )
    color_check, _ = dialog._group_checks[0]
    bw_check, _ = dialog._group_checks[1]
    color_check.set_active(False)
    assert [c.get_active() for c in dialog._checks] == [False, False, True]
    assert bw_check.get_active()
    assert dialog._master.get_inconsistent()
    color_check.set_active(True)
    assert all(c.get_active() for c in dialog._checks)
    assert dialog._master.get_active()


def test_folder_check_reflects_member_state() -> None:
    """Toggling members drives the folder check to mixed/all/none."""
    groups = [("Color", [("a", "a", Recipe()), ("b", "b", Recipe())])]
    dialog = RecipeGridDialog(
        groups=groups, render=lambda *a: None, on_pick=lambda _k: None
    )
    folder_check, members = dialog._group_checks[0]
    members[0].set_active(False)
    assert folder_check.get_inconsistent()
    members[1].set_active(False)
    assert not folder_check.get_active()
    assert not folder_check.get_inconsistent()


def test_ampersand_titles_survive() -> None:
    """A folder or recipe named with & renders."""
    groups = [("B&W", [("HP5 & friends", "HP5 & friends", Recipe())])]
    dialog = RecipeGridDialog(
        groups=groups, render=lambda *a: None, on_pick=lambda _k: None
    )
    group = dialog.pick_box.get_last_child()
    assert group.get_title() in ("B&amp;W", "B&W")
    assert dialog._entries[0][1] == "HP5 & friends"
