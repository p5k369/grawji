"""Tests the filmstrip's filter visibility, nav and selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grawji import catalog

pytestmark = pytest.mark.gui

PRIME = "XF35mmF1.4 R"
TELE = "XF70-300mmF4-5.6 R LM OIS WR"


@pytest.fixture
def strip(gtk: Any, tmp_path: Path) -> Any:
    """A scanned strip over three RAFs with synthetic filter metadata."""
    from grawji.imaging.thumbnails import ThumbMeta
    from grawji.views.filmstrip import FilmStrip

    for name in ("a.RAF", "b.RAF", "c.RAF"):
        (tmp_path / name).write_bytes(b"not a real raf")
    opened: list[str] = []
    built = FilmStrip(on_select=opened.append)
    built.opened = opened
    built.scan(str(tmp_path))
    paths = built.paths
    meta = {
        paths[0]: ThumbMeta("X-E5", PRIME, "35 mm"),
        paths[1]: ThumbMeta("X100F", "", "23 mm"),
        paths[2]: ThumbMeta("X-E5", TELE, "183.4 mm"),
    }
    for path, seen in meta.items():
        built._entries[path] = catalog.with_meta(
            built._entries[path], seen.model, seen.lens, seen.focal
        )
    return built


def settle(strip: Any) -> None:
    """Apply a focal range at once, the slider debounce aside."""
    strip._apply_focal_now()


def visible(strip: Any) -> list[str]:
    """Basenames of the frames the filter leaves."""
    return [Path(path).name for path in strip.visible_paths]


def _descendants(widget: Any) -> list[Any]:
    """All widgets under widget, depth-first."""
    found = []
    child = widget.get_first_child()
    while child is not None:
        found.append(child)
        found.extend(_descendants(child))
        child = child.get_next_sibling()
    return found


def _scales(widget: Any) -> list[Any]:
    """The two focal slider scales inside the custom menu widget."""
    return [
        child
        for child in _descendants(widget)
        if child.__class__.__name__ == "Scale"
    ]


def test_filter_by_model(strip: Any) -> None:
    """A model filter hides the other bodies' cards."""
    strip.set_filter(model="X-E5", lens=None, focal=None)
    assert visible(strip) == ["a.RAF", "c.RAF"]
    strip.set_filter(model=None, lens=None, focal=None)
    assert visible(strip) == ["a.RAF", "b.RAF", "c.RAF"]


def test_filter_by_lens(strip: Any) -> None:
    """A lens filter shows only that lens's shots."""
    strip.set_filter(model=None, lens=TELE, focal=None)
    assert visible(strip) == ["c.RAF"]


def test_focal_range_is_inclusive(strip: Any) -> None:
    """The focal filter is an inclusive millimeter range."""
    strip.set_filter(model=None, lens=None, focal=(23.0, 35.0))
    assert visible(strip) == ["a.RAF", "b.RAF"]
    strip.set_filter(model=None, lens=None, focal=(100.0, 300.0))
    assert visible(strip) == ["c.RAF"]


def test_filter_axes_combine(strip: Any) -> None:
    """Model, lens and focal range filter together."""
    strip.set_filter(model="X-E5", lens=None, focal=(20.0, 40.0))
    assert visible(strip) == ["a.RAF"]


def test_known_values_come_from_the_folder(strip: Any) -> None:
    """The menu choices reflect what is actually present."""
    assert strip.known_models() == ["X-E5", "X100F"]
    assert strip.known_lenses() == [PRIME, TELE]
    assert strip.known_focals() == ["23 mm", "35 mm", "183.4 mm"]


def test_filter_drops_hidden_marks(strip: Any) -> None:
    """Marks on cards the filter hides are dropped."""
    strip._selected = set(strip.paths)
    strip.set_filter(model="X100F", lens=None, focal=None)
    assert [Path(p).name for p in strip.selected_paths] == ["b.RAF"]


def test_select_all_selects_only_visible(strip: Any) -> None:
    """Select All in batch mode honors the filter."""
    strip.set_filter(model="X-E5", lens=None, focal=None)
    strip.enter_select_mode()
    strip.select_all()
    names = [Path(p).name for p in strip.selected_paths]
    assert names == ["a.RAF", "c.RAF"]


def test_menu_actions_drive_the_filter(strip: Any) -> None:
    """Picking a radio item filters, Clear resets, states stay in sync."""
    from gi.repository import GLib, Gtk

    button = Gtk.MenuButton()
    strip.adopt_filter_button(button)
    strip._rebuild_filter_menu(button)
    assert button.get_popover() is not None
    lens_action = strip._filter_actions["lens"]
    lens_action.change_state(GLib.Variant.new_string(PRIME))
    assert visible(strip) == ["a.RAF"]
    assert button.has_css_class("accent")
    strip._on_filter_cleared()
    assert visible(strip) == ["a.RAF", "b.RAF", "c.RAF"]
    assert lens_action.get_state().get_string() == ""
    assert not button.has_css_class("accent")


def test_focal_sliders_snap_to_folder_values(strip: Any) -> None:
    """The slider stops are the folder's focal lengths, in order."""
    widget = strip._build_focal_sliders()
    assert widget is not None
    low, high = _scales(widget)
    assert (low.get_value(), high.get_value()) == (0.0, 2.0)
    low.set_value(1.0)  # from "35 mm" upward
    settle(strip)
    assert strip._filter_focal == (35.0, 183.4)
    assert visible(strip) == ["a.RAF", "c.RAF"]
    high.set_value(1.0)  # collapse the range onto exactly 35 mm
    settle(strip)
    assert strip._filter_focal == (35.0, 35.0)
    assert visible(strip) == ["a.RAF"]


def test_focal_sliders_push_each_other(strip: Any) -> None:
    """Dragging one handle past the other carries it along."""
    low, high = _scales(strip._build_focal_sliders())
    high.set_value(0.0)
    settle(strip)
    assert low.get_value() == 0.0
    assert strip._filter_focal == (23.0, 23.0)


def test_sliders_absent_without_two_focals(strip: Any) -> None:
    """A single distinct focal length offers no range to filter."""
    strip._entries = {
        path: catalog.with_meta(strip._entries[path], "X-E5", PRIME, "35 mm")
        for path in strip.paths
    }
    assert strip._build_focal_sliders() is None


def test_edit_badges_are_independent(strip: Any, tmp_path: Path) -> None:
    """The EV and crop badges track their own sidecar keys."""
    import json

    from grawji.sidecar import sidecar_path

    target = strip.paths[0]
    sidecar_path(target).write_text(json.dumps({"exposure": 0.7}))
    strip.refresh_badges(target)
    entry = strip._entries[target]
    assert entry.has_ev
    assert not entry.has_crop
    sidecar_path(target).write_text(
        json.dumps({"crop": {"angle": 1.0, "rect": [0, 0, 1, 1]}})
    )
    strip.refresh_badges(target)
    entry = strip._entries[target]
    assert not entry.has_ev
    assert entry.has_crop


def test_unknown_metadata_stays_visible(strip: Any) -> None:
    """Cards still decoding keep showing under any filter."""
    pending = strip.paths[0]
    strip._entries[pending] = catalog.with_meta(
        strip._entries[pending], "", "", ""
    )
    strip.set_filter(model="X100F", lens=None, focal=None)
    assert visible(strip) == ["a.RAF", "b.RAF"]


def test_the_focal_slider_waits_for_the_drag_to_settle(strip: Any) -> None:
    """Every pixel of a drag must not re-filter the folder."""
    low, _high = _scales(strip._build_focal_sliders())
    low.set_value(1.0)
    assert strip._filter_focal is None
    assert strip._focal_pending
    settle(strip)
    assert strip._filter_focal == (35.0, 183.4)


def test_a_saved_crop_lights_the_badge_on_the_card(
    window: Any, tmp_path: Path
) -> None:
    """The card on screen shows the edit, not only the entry behind it."""
    import json
    import time

    from grawji.sidecar import sidecar_path
    from tests.gui_support import pump

    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    window.set_default_size(900, 700)
    window.present()
    window._scan_folder(str(tmp_path))
    for _ in range(20):
        pump()
        time.sleep(0.004)
    strip = window._filmstrip
    target = strip.paths[0]
    card = next(b for b, path in strip._card_path.items() if path == target)
    assert not strip._cards[card]["crop"].get_visible()
    sidecar_path(target).write_text(
        json.dumps({"crop": {"angle": 1.0, "rect": [0, 0, 1, 1]}})
    )
    strip.refresh_badges(target)
    assert strip._cards[card]["crop"].get_visible()


def test_a_stale_glide_cannot_fight_the_next_scroll(
    window: Any, tmp_path: Path
) -> None:
    """A missed key release must not leave the strip gliding forever."""
    import time

    from tests.gui_support import pump

    for index in range(40):
        (tmp_path / f"{index:03}.RAF").write_bytes(b"not a real raf")
    window.set_size_request(900, 700)
    window.present()
    window._scan_folder(str(tmp_path))
    for _ in range(20):
        pump()
        time.sleep(0.004)
    strip = window._filmstrip
    strip.start_glide(1)  # as if the release never arrived
    for _ in range(10):
        pump()
        time.sleep(0.004)
    strip.scroll_step(1)
    resting = strip.get_hadjustment().get_value()
    for _ in range(20):
        pump()
        time.sleep(0.004)
    assert abs(strip.get_hadjustment().get_value() - resting) <= 2


def test_leaving_the_window_ends_a_glide(window: Any, tmp_path: Path) -> None:
    """Losing focus with a key down would otherwise glide on."""
    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    strip = window._filmstrip
    strip.start_glide(-1)
    assert strip._glide_tick is not None
    window._nav.cancel_hold()
    assert strip._glide_tick is None


def test_a_selection_made_before_the_window_shows_still_lays_out(
    window: Any, tmp_path: Path
) -> None:
    """Startup picks a frame before the window appears, and that broke it."""
    import time

    from tests.gui_support import pump

    for index in range(60):
        (tmp_path / f"{index:03}.RAF").write_bytes(b"not a real raf")
    strip = window._filmstrip
    window._scan_folder(str(tmp_path))
    strip.select_path(str(tmp_path / "030.RAF"))
    window.set_size_request(900, 700)
    window.present()
    end = time.perf_counter() + 1.0
    while time.perf_counter() < end:
        pump()
        time.sleep(0.004)
    placed = 0
    item = strip._view.get_first_child()
    while item is not None:
        button = item.get_first_child()
        if button is not None and button.get_width() > 0:
            placed += 1
        item = item.get_next_sibling()
    assert placed > 1


def test_the_current_card_is_marked_and_the_css_can_reach_it(
    strip: Any,
) -> None:
    """Cards are boxes now, so the rules must not name a button."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.window import _CANVAS_CSS
    from tests.gui_support import pump

    holder = Gtk.Window()
    holder.set_default_size(800, 200)
    holder.set_child(strip)
    holder.present()
    for _ in range(40):
        pump()
        if strip._card_path:
            break
    assert strip._card_path, "no card was bound"
    strip.select_path(strip.paths[1], notify=False)
    marked = [
        card
        for card, path in strip._card_path.items()
        if card.has_css_class("thumb-selected")
    ]
    assert [strip._card_path[card] for card in marked] == [strip.paths[1]]
    assert "button.thumb" not in _CANVAS_CSS
    assert ".thumb.thumb-selected" in _CANVAS_CSS


def test_selecting_a_far_frame_brings_the_strip_to_it(
    gtk: Any, tmp_path: Path
) -> None:
    """The list defers its own scroll, so the strip jumps there itself."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.filmstrip import FilmStrip
    from tests.gui_support import pump

    for index in range(300):
        (tmp_path / f"{index:03}.RAF").write_bytes(b"not a real raf")
    strip = FilmStrip(on_select=lambda _p: None)
    holder = Gtk.Window()
    holder.set_default_size(800, 200)
    holder.set_child(strip)
    holder.present()
    strip.scan(str(tmp_path))
    for _ in range(60):
        pump()
        if strip._card_path:
            break
    adjustment = strip.get_hadjustment()
    far = adjustment.get_upper() - adjustment.get_page_size()
    assert far > 0, "the strip has to be scrollable for this to mean anything"
    adjustment.set_value(far)
    strip.select_path(strip.paths[0], notify=False)
    assert adjustment.get_value() < adjustment.get_page_size()


def test_one_arrow_press_moves_the_strip_by_one_card(
    gtk: Any, tmp_path: Path
) -> None:
    """A step used to land mid card, which reads as barely moving."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.filmstrip import FilmStrip
    from tests.gui_support import pump

    for index in range(60):
        (tmp_path / f"{index:03}.RAF").write_bytes(b"not a real raf")
    strip = FilmStrip(on_select=lambda _p: None)
    holder = Gtk.Window()
    holder.set_default_size(800, 200)
    holder.set_child(strip)
    holder.present()
    strip.scan(str(tmp_path))
    for _ in range(60):
        pump()
        if strip._card_path:
            break
    card = next(iter(strip._card_path))
    width = card.get_width()
    assert width > 0
    adjustment = strip.get_hadjustment()
    strip.scroll_step(1)
    moved = adjustment.get_value()
    assert moved >= width / 2, f"a step moved {moved} of a {width} card"
    strip.scroll_step(1)
    assert adjustment.get_value() - moved >= width / 2


def test_a_hand_on_the_scrollbar_outranks_pending_moves(
    gtk: Any, tmp_path: Path
) -> None:
    """A reveal still in flight must not fight the user's drag."""
    from grawji.views.filmstrip import FilmStrip

    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    strip = FilmStrip(on_select=lambda _p: None)
    strip.scan(str(tmp_path))
    strip._reveal_wanted = ("wanted", True)
    strip._center_path = "wanted"
    strip._center_frames = 30
    strip._glide_dir = 1
    strip._user_takes_over()
    assert strip._reveal_wanted is None
    assert strip._center_path is None
    assert strip._center_frames == 0
