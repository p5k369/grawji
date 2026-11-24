"""Tests for the filmstrip's arrow keys."""

from __future__ import annotations

import time
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


def test_the_other_arrow_takes_over_from_a_held_one(gtk: Any) -> None:
    """Two arrows pressed in a row used to leave a glide nobody holds."""
    strip = Strip()
    nav = FilmStripNav(strip)
    nav._window = Window()

    nav._on_key_pressed(None, Gdk.KEY_Left, 0, Gdk.ModifierType(0))
    wait(0.1)
    nav._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
    nav._on_key_released(None, Gdk.KEY_Left, 0, None)
    wait(0.3)
    assert strip.gliding == 0, "the first arrow's hold must not fire"
    wait(0.1)
    assert strip.gliding == 1
    nav._on_key_released(None, Gdk.KEY_Right, 0, None)
    wait(0.4)

    assert strip.gliding == 0
    assert strip.steps == []
