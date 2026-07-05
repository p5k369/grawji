"""Shared fixtures; GUI tests run under a virtual X display.

pytest-xvfb starts an Xvfb display automatically when it is installed, so
the GUI tests need no manual display setup. We force GTK onto the X11
backend (before any gi import) because GDK otherwise reaches for Wayland
and ignores the virtual display.

gi is imported lazily inside the GUI fixtures, never at module scope, so
the pure-module suite still runs on machines and CI jobs without the GTK
stack. GUI tests carry the gui marker; the display guard below keys
on it and skips them cleanly when no display is available.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# Must be set before GDK initialises. Harmless when GTK is absent.
os.environ.setdefault("GDK_BACKEND", "x11")


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point config and cache at a temp dir so tests never touch ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture(scope="session")
def gtk() -> Any:
    """Import gi and initialise GTK/libadwaita once for the GUI session."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK cannot initialise")
    Adw.init()
    return None


@pytest.fixture(autouse=True)
def _gui_guard(request: pytest.FixtureRequest) -> None:
    """Skip gui-marked tests without a display; otherwise ensure GTK is up.

    We force the X11 backend (above), so a present DISPLAY is the reliable
    signal that a display exists - pytest-xvfb sets one. Without it, GTK on
    some builds reports init success but then segfaults on the first real
    widget, so we must skip before constructing anything. Non-gui tests are
    left untouched and never import gi.
    """
    if request.node.get_closest_marker("gui") is None:
        return
    if not os.environ.get("DISPLAY"):
        pytest.skip("no display; GUI tests need one (pytest-xvfb provides it)")
    request.getfixturevalue("gtk")


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fully built MainWindow with the camera boundary stubbed out.

    The window is built without an application on purpose: attaching an
    Adw.ApplicationWindow to an app whose startup never ran crashes GTK,
    and _install_actions already no-ops the accelerator wiring when there
    is no application. Every .ui template and widget still builds.
    """
    from grawji.views import window as window_module
    from tests.gui_support import pump

    # Never touch USB during a GUI test, report "no camera".
    monkeypatch.setattr(
        window_module.camera_info, "detect_camera", lambda: None
    )
    win = window_module.MainWindow()
    pump()
    yield win
    win._worker.stop()
    win.destroy()
    pump()
