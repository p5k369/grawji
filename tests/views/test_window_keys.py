"""Bare keys reach the window after the focused widget, never before."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from tests.gui_support import pump

pytestmark = pytest.mark.gui


def _shortcut_controllers(window: Any) -> list[Gtk.ShortcutController]:
    controllers = window.observe_controllers()
    return [
        controllers.get_item(i)
        for i in range(controllers.get_n_items())
        if isinstance(controllers.get_item(i), Gtk.ShortcutController)
    ]


def test_bare_keys_are_bubble_phase_shortcuts(window: Any) -> None:
    """The window's bare keys are shortcuts that run after the focus."""
    bare = []
    for controller in _shortcut_controllers(window):
        for i in range(controller.get_n_items()):
            shortcut = controller.get_item(i)
            action = shortcut.get_action()
            if not isinstance(action, Gtk.NamedAction):
                continue
            trigger = shortcut.get_trigger()
            assert isinstance(trigger, Gtk.KeyvalTrigger)
            if trigger.get_modifiers() == 0:
                phase = controller.get_propagation_phase()
                assert phase == Gtk.PropagationPhase.BUBBLE, (
                    trigger.to_string()
                )
                bare.append(trigger.to_string())
    assert "h" in bare


def test_no_bare_key_is_an_application_accelerator() -> None:
    """GTK runs application accelerators in the capture phase, so none."""
    from grawji.views import window as window_module

    text = Path(window_module.__file__).read_text(encoding="utf-8")
    accel_lists = r'set_accels_for_action\([^)]*\["([^<"]+)"\]'
    bare = re.findall(r'\("([^<"]+)",\)', text)
    bare += re.findall(accel_lists, text)
    assert bare == [], bare


def test_window_keys_stay_out_of_entries_and_dialogs(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete, handled by the window's key controller, respects focus."""
    calls: list[int] = []

    def handle_delete() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(window._fileops, "handle_delete", handle_delete)
    assert window._on_window_key(None, Gdk.KEY_Delete, 0, 0) is True
    assert len(calls) == 1

    entry = Gtk.Entry()
    monkeypatch.setattr(window, "get_focus", lambda: entry)
    assert window._on_window_key(None, Gdk.KEY_Delete, 0, 0) is False

    dialog = Adw.AlertDialog(heading="Save recipe")
    button = Gtk.Button()
    dialog.set_extra_child(button)
    dialog.present(window)
    pump()
    assert button.get_ancestor(Adw.Dialog) is dialog
    monkeypatch.setattr(window, "get_focus", lambda: button)
    assert window._on_window_key(None, Gdk.KEY_Delete, 0, 0) is False
    assert len(calls) == 1
