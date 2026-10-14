"""Film simulation hover previews: the panel's list rows and window wiring."""

from __future__ import annotations

import time
from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gtk

from tests.gui_support import pump

pytestmark = pytest.mark.gui


class FakeListItem:
    """The slice of Gtk.ListItem the film list factory uses."""

    def __init__(self, name: str, position: int) -> None:
        """Hold one simulation name at a list position."""
        self._item = Gtk.StringObject.new(name)
        self._position = position
        self._child: Any = None

    def get_item(self) -> Any:
        """The bound StringObject."""
        return self._item

    def get_position(self) -> int:
        """The row's position in the list."""
        return self._position

    def get_child(self) -> Any:
        """The row widget built in setup."""
        return self._child

    def set_child(self, child: Any) -> None:
        """Store the row widget."""
        self._child = child


def _panel() -> Any:
    from grawji.views.recipe_panel import RecipePanel

    panel = RecipePanel()
    pump()
    return panel


def _row(panel: Any, name: str) -> FakeListItem:
    """Set up and bind a fake popup row for name."""
    sims = [
        panel.film_row.get_model().get_string(i)
        for i in range(panel.film_row.get_model().get_n_items())
    ]
    item = FakeListItem(name, sims.index(name))
    panel._on_film_item_setup(None, item)
    panel._on_film_item_bind(None, item)
    return item


def test_hovered_rows_are_announced_once_and_closing_ends_it() -> None:
    """Entering rows names the simulation; the list closing sends ""."""
    panel = _panel()
    heard: list[str] = []
    panel.connect("film-sim-hover", lambda _p, sim: heard.append(sim))
    velvia, astia = _row(panel, "Velvia"), _row(panel, "Astia")

    panel._on_film_item_entered(velvia)
    panel._on_film_item_entered(velvia)
    panel._on_film_item_entered(astia)
    assert panel._film_popover is not None, "combo row popover not found"
    panel._film_popover.emit("closed")
    panel._film_popover.emit("closed")

    assert heard == ["Velvia", "Astia", ""]


def test_popup_rows_show_names_and_follow_the_selection_checkmark() -> None:
    """The custom rows keep libadwaita's label and checkmark."""
    panel = _panel()
    provia, velvia = _row(panel, "Provia"), _row(panel, "Velvia")
    assert panel.film_row.get_list_factory() is not None

    label = velvia.get_child().get_first_child()
    assert label.get_label() == "Velvia"

    def check(item: FakeListItem) -> float:
        return float(item.get_child().get_last_child().get_opacity())

    assert (check(provia), check(velvia)) == (1.0, 0.0)
    panel.film_row.set_selected(velvia.get_position())
    assert (check(provia), check(velvia)) == (0.0, 1.0)


def _jpeg(width: int) -> bytes:
    """A tiny real JPEG whose width tells renders apart."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, 8)
    pixbuf.fill(0x808080FF)
    ok, data = pixbuf.save_to_bufferv("jpeg", [], [])
    assert ok
    return bytes(data)


WIDTHS = {"Provia": 10, "Velvia": 20, "Astia": 30}


class FakeWorker:
    """Renders instantly; records what was asked of the camera."""

    def __init__(self) -> None:
        """Start with no renders."""
        self.calls: list[tuple[str, str]] = []

    def render(self, recipe: Any, **kwargs: Any) -> None:
        """Record the render and deliver a JPEG sized by simulation."""
        self.calls.append(
            (recipe.film_simulation, kwargs.get("coalesce_key", "render"))
        )
        kwargs["on_done"](_jpeg(WIDTHS[recipe.film_simulation]))

    def stop(self) -> None:
        """Nothing to stop."""


def _wait_until(condition: Any, timeout: float = 5.0) -> bool:
    """Pump the main loop until condition() holds or time runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _shown_width(win: Any) -> int | None:
    pixbuf = win.preview_view.source_pixbuf
    return None if pixbuf is None else pixbuf.get_width()


def test_window_previews_hover_restores_and_reuses_it_on_pick(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hover renders on the side, closing restores, picking is instant."""
    win = window
    worker = FakeWorker()
    win._worker.stop(close_session=False)
    monkeypatch.setattr(win, "_worker", worker)
    monkeypatch.setattr(
        type(win._session), "is_open", property(lambda s: True)
    )
    panel = win.recipe_panel

    # The panel's own preview (Provia) is on screen first.
    win._render_preview()
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Provia"])

    panel.emit("film-sim-hover", "Velvia")
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Velvia"])
    assert ("Velvia", "hover") in worker.calls

    # Hovering the selected simulation never asks the camera.
    before = list(worker.calls)
    panel.emit("film-sim-hover", "Provia")
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Provia"])
    panel.emit("film-sim-hover", "Velvia")
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Velvia"])
    assert worker.calls == before, "cached and current previews re-rendered"

    # Closing without a pick puts the panel's picture back, no camera.
    panel.emit("film-sim-hover", "")
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Provia"])
    assert worker.calls == before

    # Picking the simulation previewed before needs no camera either.
    sims = panel._film_sims
    panel.film_row.set_selected(sims.index("Velvia"))
    assert _wait_until(lambda: _shown_width(win) == WIDTHS["Velvia"])
    assert worker.calls == before


def test_a_new_image_drops_cached_hover_previews(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached previews belong to one image only."""
    win = window
    worker = FakeWorker()
    win._worker.stop(close_session=False)
    monkeypatch.setattr(win, "_worker", worker)
    monkeypatch.setattr(
        type(win._session), "is_open", property(lambda s: True)
    )
    win.recipe_panel.emit("film-sim-hover", "Astia")
    assert _wait_until(lambda: ("Astia", "hover") in worker.calls)
    assert len(win._preview_cache) == 1
    win._hover.reset()
    assert len(win._preview_cache) == 0
