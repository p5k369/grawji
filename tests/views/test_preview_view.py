"""The preview viewport tests."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.gui


def _pixbuf(width: int, height: int) -> Any:
    """A plain pixbuf of the given size."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill(0x3060A0FF)
    return pixbuf


def _gaps(view: Any) -> tuple[float, float, float, float]:
    """Space left of, right of, above and below the shown picture."""
    found, rect = view.picture.compute_bounds(view.scroll)
    assert found
    return (
        rect.origin.x,
        view.scroll.get_width() - (rect.origin.x + rect.size.width),
        rect.origin.y,
        view.scroll.get_height() - (rect.origin.y + rect.size.height),
    )


def _skew(view: Any) -> float:
    """How lopsided the shown image sits in the viewport, in pixels."""
    left, right, top, bottom = _gaps(view)
    return max(abs(left - right), abs(top - bottom))


@pytest.fixture
def shown(gtk: Any) -> Any:
    """A preview view in a window, showing a large image."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.preview_view import PreviewView

    view = PreviewView()
    pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
    pane.set_start_child(view)
    pane.set_end_child(Gtk.Box())
    pane.set_position(760)
    window = Gtk.Window()
    window.set_child(pane)
    window.set_default_size(1000, 700)
    window.present()
    settle(view)
    view.show_pixbuf(_pixbuf(3000, 2000))
    settle(view, 0.2)
    view.window = window
    view.pane = pane
    return view


def settle(_view: Any, seconds: float = 0.5) -> None:
    """Let the layout, the refit and the scroll clamp work through."""
    import time

    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    context = GLib.MainContext.default()
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        context.iteration(False)
        time.sleep(0.002)


def test_a_zoomed_image_stays_centred_across_a_resize(shown: Any) -> None:
    """Shrinking the window used to leave the corner of the image."""
    shown.set_zoom(2.0)
    settle(shown)
    assert shown._zoom == 2.0
    assert _skew(shown) <= 2
    was = shown.scroll.get_width()
    shown.pane.set_position(400)
    settle(shown)
    assert shown.scroll.get_width() != was, "the viewport did not change"
    assert _skew(shown) <= 2
    shown.pane.set_position(700)
    settle(shown)
    assert _skew(shown) <= 2


def test_a_resize_keeps_the_zoom_relative_to_the_fit(shown: Any) -> None:
    """200% has to stay 200% of what fits, not of the old window."""
    shown.set_zoom(2.0)
    settle(shown)
    before = shown._content_w / shown.scroll.get_width()
    shown.pane.set_position(420)
    settle(shown)
    after = shown._content_w / shown.scroll.get_width()
    assert abs(after - before) < 0.1


def test_a_narrow_image_that_grows_wider_stays_centred(shown: Any) -> None:
    """An image smaller than the viewport must not poison the middle."""
    # Proportions that fit the wide viewport and overflow the narrow one.
    shown.show_pixbuf(_pixbuf(1240, 2000))
    settle(shown, 0.2)
    shown.set_zoom(2.0)
    settle(shown)
    left, right, _top, _bottom = _gaps(shown)
    assert left > 0, "the image has to be narrower than the viewport here"
    assert abs(left - right) <= 2
    shown.pane.set_position(300)
    settle(shown)
    left, right, _top, _bottom = _gaps(shown)
    assert left < 0, "the image has to overflow the viewport now"
    assert abs(left - right) <= 2


def test_the_middle_reading_ignores_the_empty_canvas() -> None:
    """A viewport wider than the image is centred on it, by definition."""
    from grawji.views.preview_view import _fraction

    class Adjustment:
        """The two numbers the reading is taken from."""

        def __init__(self, value: float, page: float) -> None:
            self._value, self._page = value, page

        def get_value(self) -> float:
            return self._value

        def get_page_size(self) -> float:
            return self._page

    assert _fraction(Adjustment(0, 1443), 940) == 0.5
    assert _fraction(Adjustment(100, 400), 1000) == 0.3
    assert _fraction(Adjustment(0, 400), 0) == 0.5


def test_the_preview_column_can_get_narrow(gtk: Any) -> None:
    """A hidden crop bar or a long readout must not hold the window open."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.preview_view import PreviewView

    view = PreviewView()
    view.set_status("A rather long status message that says what happened")
    view.set_native_size((7728, 5152))
    minimum, _natural, _a, _b = view.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert minimum < 450, f"the preview column cannot shrink below {minimum}"


def test_the_crop_bar_appears_and_stops_reserving_width(shown: Any) -> None:
    """It starts hidden now, so it has to come back when editing."""
    shown.show_pixbuf(_pixbuf(1200, 800))
    settle(shown, 0.2)
    assert not shown.crop_bar.get_visible()
    assert shown._crop_editor._start_edit()
    settle(shown, 0.6)
    assert shown.crop_bar.get_visible()
    assert shown.crop_bar.get_height() > 0
    shown.cancel_crop()
    settle(shown, 0.8)
    assert not shown.crop_bar.get_visible()


def test_an_image_left_hanging_off_one_edge_is_put_back(shown: Any) -> None:
    """The viewport can keep an offset from a bigger image."""
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    shown.set_zoom(0.5)
    settle(shown, 0.3)
    forced = Graphene.Rect().init(4, 100, 200, 120)
    shown.picture.compute_bounds = lambda _target: (True, forced)
    relayouts: list[str] = []
    shown.scroll.queue_allocate = lambda: relayouts.append("scroll")
    budget = shown._repairs
    shown._verify_placement()
    assert relayouts, "an off-centre image has to be put back"
    assert shown._repairs == budget - 1
    for _ in range(budget + 3):
        shown._verify_placement()
    assert shown._repairs == 0


def test_a_scroll_left_over_from_a_bigger_image_is_taken_back(
    shown: Any,
) -> None:
    """The range can outlive the image it belonged to."""
    shown.set_zoom(2.0)
    settle(shown, 0.3)
    adjustment = shown.scroll.get_hadjustment()
    shown.set_zoom(0.5)
    settle(shown, 0.3)
    assert shown._content_w < adjustment.get_page_size(), "it has to fit now"
    adjustment.set_upper(adjustment.get_page_size() * 3)
    adjustment.set_value(132)
    settle(shown, 0.3)
    assert adjustment.get_value() == 0
    assert _skew(shown) <= 2


def test_an_image_that_fits_gets_no_room_to_slide(shown: Any) -> None:
    """Room in the scroll range is what lets a small image slide."""
    shown.set_zoom(0.5)
    settle(shown, 0.3)
    adjustment = shown.scroll.get_hadjustment()
    assert shown._content_w < adjustment.get_page_size()
    assert adjustment.get_upper() == adjustment.get_page_size()
    adjustment.set_upper(adjustment.get_page_size() * 2)
    settle(shown, 0.3)
    assert adjustment.get_upper() == adjustment.get_page_size()
    assert _skew(shown) <= 2


def test_a_zoomed_image_keeps_its_room_to_scroll(shown: Any) -> None:
    """The rule is for images that fit, panning must still work."""
    shown.set_zoom(3.0)
    settle(shown, 0.3)
    adjustment = shown.scroll.get_hadjustment()
    assert adjustment.get_upper() > adjustment.get_page_size()
    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    settle(shown, 0.3)
    assert adjustment.get_value() > 0
