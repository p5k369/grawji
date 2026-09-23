"""The file-operations context menu gor the image cards."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from grawji import mainloop

ACTIONS = ("open-with", "export", "copy", "move", "trash")


def install_actions(
    widget: Gtk.Widget, on_action: Callable[[str], None]
) -> None:
    """Install the fileops action group popup_menu items point at."""
    group = Gio.SimpleActionGroup()
    for kind in ACTIONS:
        action = Gio.SimpleAction.new(kind, None)
        action.connect("activate", partial(lambda k, *_a: on_action(k), kind))
        group.add_action(action)
    widget.insert_action_group("fileops", group)


def popup_menu(anchor: Gtk.Widget, x: float, y: float, count: int) -> None:
    """Pop the file operations up at a click position on a card."""
    suffix = f" ({count})" if count > 1 else ""
    menu = Gio.Menu()
    menu.append("Open With…", "fileops.open-with")
    menu.append(f"Export{suffix}…", "fileops.export")
    menu.append(f"Copy to…{suffix}", "fileops.copy")
    menu.append(f"Move to…{suffix}", "fileops.move")
    menu.append(f"Move to Trash{suffix}", "fileops.trash")
    popover = Gtk.PopoverMenu.new_from_model(menu)
    popover.set_parent(anchor)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    popover.set_pointing_to(rect)
    popover.connect("closed", lambda p: mainloop.call(_drop_popover, p))
    popover.popup()


def _drop_popover(popover: Gtk.PopoverMenu) -> bool:
    """Unparent a dismissed context menu so it can be collected."""
    popover.unparent()
    return GLib.SOURCE_REMOVE
