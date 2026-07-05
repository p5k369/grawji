"""Helpers for GUI tests: pump the main loop, walk the widget tree."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk


def pump(iterations: int = 100) -> None:
    """Drain pending main-loop work so construction side effects settle."""
    context = GLib.MainContext.default()
    for _ in range(iterations):
        if not context.pending():
            break
        context.iteration(may_block=False)


def walk(widget: Gtk.Widget) -> list[Gtk.Widget]:
    """Return widget and all its descendants, depth first."""
    found = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(walk(child))
        child = child.get_next_sibling()
    return found
