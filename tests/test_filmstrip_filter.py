"""Tests the filmstrip's filter visibility, nav and selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
    built._meta = {
        paths[0]: ThumbMeta("X-E5", PRIME, "35 mm"),
        paths[1]: ThumbMeta("X100F", "", "23 mm"),
        paths[2]: ThumbMeta("X-E5", TELE, "183.4 mm"),
    }
    return built


def visible(strip: Any) -> list[str]:
    """Basenames of the cards the filter shows."""
    return [
        Path(path).name
        for path, button in zip(strip._paths, strip._buttons, strict=True)
        if button.get_visible()
    ]


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


def test_keyboard_nav_skips_hidden_cards(strip: Any) -> None:
    """Arrow navigation lands only on visible cards."""
    strip.set_filter(model="X-E5", lens=None, focal=None)
    strip.select_relative(1)
    assert strip.opened[-1].endswith("a.RAF")
    strip.select_relative(1)
    assert strip.opened[-1].endswith("c.RAF")


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
    assert strip._filter_focal == (35.0, 183.4)
    assert visible(strip) == ["a.RAF", "c.RAF"]
    high.set_value(1.0)  # collapse the range onto exactly 35 mm
    assert strip._filter_focal == (35.0, 35.0)
    assert visible(strip) == ["a.RAF"]


def test_focal_sliders_push_each_other(strip: Any) -> None:
    """Dragging one handle past the other carries it along."""
    low, high = _scales(strip._build_focal_sliders())
    high.set_value(0.0)
    assert low.get_value() == 0.0
    assert strip._filter_focal == (23.0, 23.0)


def test_sliders_absent_without_two_focals(strip: Any) -> None:
    """A single distinct focal length offers no range to filter."""
    from grawji.imaging.thumbnails import ThumbMeta

    strip._meta = {p: ThumbMeta("X-E5", PRIME, "35 mm") for p in strip.paths}
    assert strip._build_focal_sliders() is None


def test_edit_badges_are_independent(strip: Any, tmp_path: Path) -> None:
    """The EV and crop badges track their own sidecar keys."""
    import json

    from grawji.sidecar import sidecar_path

    target = strip.paths[0]
    sidecar_path(target).write_text(json.dumps({"exposure": 0.7}))
    strip.refresh_badges(target)
    badges = strip._badges[target]
    assert badges["ev"].get_visible()
    assert not badges["crop"].get_visible()
    sidecar_path(target).write_text(
        json.dumps({"crop": {"angle": 1.0, "rect": [0, 0, 1, 1]}})
    )
    strip.refresh_badges(target)
    assert not badges["ev"].get_visible()
    assert badges["crop"].get_visible()


def test_unknown_metadata_stays_visible(strip: Any) -> None:
    """Cards still decoding keep showing under any filter."""
    del strip._meta[strip.paths[0]]
    strip.set_filter(model="X100F", lens=None, focal=None)
    assert visible(strip) == ["a.RAF", "b.RAF"]
