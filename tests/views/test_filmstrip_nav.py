"""Tests for the filmstrip's arrow keys."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

gi = pytest.importorskip("gi")

from gi.repository import Gdk, Gtk

from grawji.views.filmstrip_nav import FilmStripNav
from tests.gui_support import pump


class Strip:
    """A strip that only remembers whether it is gliding."""

    def __init__(self) -> None:
        """Start at rest."""
        self.gliding = 0
        self.glides: list[int] = []
        self.steps: list[int] = []
        self._adjustment = Gtk.Adjustment(
            value=50, lower=0, upper=100, page_size=10
        )

    def get_hadjustment(self) -> Gtk.Adjustment:
        """The scroll range the arrows look at."""
        return self._adjustment

    def start_glide(self, direction: int) -> None:
        """Glide one way."""
        self.gliding = direction
        self.glides.append(direction)

    def stop_glide(self) -> None:
        """Come to rest."""
        self.gliding = 0

    def scroll_step(self, direction: int) -> None:
        """Step one card."""
        self.steps.append(direction)


class Window:
    """A window with nothing focused."""

    def get_focus(self) -> None:
        """No widget has the focus."""
        return


def wait(seconds: float) -> None:
    """Let the hold timers run."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(5)
        time.sleep(0.005)


def wait_until(done: Callable[[], bool], seconds: float = 3.0) -> None:
    """Let the hold timers run until done, or give up."""
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        pump(5)
        time.sleep(0.005)


def test_the_other_arrow_takes_over_from_a_held_one(gtk: Any) -> None:
    """Two arrows pressed in a row used to leave a glide nobody holds."""
    strip = Strip()
    nav = FilmStripNav(strip)
    nav._window = Window()

    nav._on_key_pressed(None, Gdk.KEY_Left, 0, Gdk.ModifierType(0))
    wait(0.02)
    nav._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
    nav._on_key_released(None, Gdk.KEY_Left, 0, None)
    wait_until(lambda: bool(strip.glides))
    wait(0.1)
    assert strip.glides == [1], "only the arrow still held may glide"
    nav._on_key_released(None, Gdk.KEY_Right, 0, None)
    wait_until(lambda: strip.gliding == 0)

    assert strip.gliding == 0
    assert strip.steps == []


class FocusedWindow:
    """A window whose focus sits on a given widget."""

    def __init__(self, focus: Gtk.Widget) -> None:
        """Remember the focused widget."""
        self._focus = focus

    def get_focus(self) -> Gtk.Widget:
        """The focused widget."""
        return self._focus


def test_arrows_stay_with_a_slider_in_a_popover(gtk: Any) -> None:
    """A slider in a popover keeps Left and Right for itself."""
    slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 10, 1)
    popover = Gtk.Popover()
    popover.set_child(slider)
    strip = Strip()
    nav = FilmStripNav(strip)
    nav._window = FocusedWindow(slider)

    taken = nav._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
    wait(0.4)

    assert not taken
    assert strip.gliding == 0


def test_arrows_move_the_strip_from_a_slider_in_the_window(gtk: Any) -> None:
    """Outside dialogs and popovers the arrows belong to the strip."""
    slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 10, 1)
    strip = Strip()
    nav = FilmStripNav(strip)
    nav._window = FocusedWindow(slider)

    assert nav._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
    nav._on_key_released(None, Gdk.KEY_Right, 0, None)

    assert strip.steps == [1]
