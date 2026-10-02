"""The context menu of the image cards."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from grawji import mainloop
from grawji.marks import MAX_RATING, STAR, Label, Marks

ACTIONS = ("open-with", "export", "copy", "move", "trash")

MarkAction = Callable[[str, object], None]


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


def install_mark_actions(
    widget: Gtk.Widget, on_mark: MarkAction
) -> Gio.SimpleActionGroup:
    """Install the stateful mark actions the menu's mark items use."""
    group = Gio.SimpleActionGroup()
    rate = Gio.SimpleAction.new_stateful(
        "rate", GLib.VariantType.new("i"), GLib.Variant.new_int32(0)
    )
    rate.connect("change-state", partial(_on_rate, on_mark=on_mark))
    group.add_action(rate)
    toggles: list[tuple[str, str, object]] = [
        ("reject", "reject", None),
        ("export", "export", None),
        *((f"label-{label.value}", "label", label) for label in Label),
    ]
    for name, kind, label in toggles:
        action = Gio.SimpleAction.new_stateful(
            name, None, GLib.Variant.new_boolean(False)
        )
        action.connect(
            "change-state",
            partial(_on_toggle, on_mark=on_mark, kind=kind, label=label),
        )
        group.add_action(action)
    widget.insert_action_group("marks", group)
    return group


def _on_rate(
    action: Gio.SimpleAction, value: GLib.Variant, *, on_mark: MarkAction
) -> None:
    """Forward a rating radio pick."""
    action.set_state(value)
    on_mark("rate", value.get_int32())


def _on_toggle(
    action: Gio.SimpleAction,
    value: GLib.Variant,
    *,
    on_mark: MarkAction,
    kind: str,
    label: object,
) -> None:
    """Forward a checkmark item, carrying its label when it has one."""
    action.set_state(value)
    on = value.get_boolean()
    on_mark(kind, (label, on) if label is not None else on)


def _show_marks(group: Gio.SimpleActionGroup, marks: Marks) -> None:
    """Point the mark actions' states at one card's marks."""
    for name, value in (
        ("rate", GLib.Variant.new_int32(marks.rating or 0)),
        ("reject", GLib.Variant.new_boolean(marks.rejected)),
        ("export", GLib.Variant.new_boolean(marks.export)),
        *(
            (
                f"label-{label.value}",
                GLib.Variant.new_boolean(label in marks.labels),
            )
            for label in Label
        ),
    ):
        action = group.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_state(value)


def _marks_section(suffix: str) -> Gio.Menu:
    """Rating, color labels, reject and export, as one menu section."""
    rating = Gio.Menu()
    for stars in range(MAX_RATING + 1):
        title = "No Stars" if stars == 0 else STAR * stars
        item = Gio.MenuItem.new(title, None)
        item.set_action_and_target_value(
            "marks.rate", GLib.Variant.new_int32(stars)
        )
        rating.append_item(item)
    colors = Gio.Menu()
    for label in Label:
        colors.append(label.value.capitalize(), f"marks.label-{label.value}")
    section = Gio.Menu()
    section.append_submenu(f"Rating{suffix}", rating)
    section.append_submenu(f"Color Label{suffix}", colors)
    section.append(f"Reject{suffix}", "marks.reject")
    section.append(f"Mark for Export{suffix}", "marks.export")
    return section


def popup_menu(
    anchor: Gtk.Widget,
    x: float,
    y: float,
    count: int,
    *,
    marks: Marks | None = None,
    mark_actions: Gio.SimpleActionGroup | None = None,
) -> None:
    """Pop the card menu up at a click position on a card."""
    suffix = f" ({count})" if count > 1 else ""
    menu = Gio.Menu()
    if marks is not None and mark_actions is not None:
        _show_marks(mark_actions, marks)
        menu.append_section(None, _marks_section(suffix))
    files = Gio.Menu()
    files.append("Open With…", "fileops.open-with")
    files.append(f"Export{suffix}…", "fileops.export")
    files.append(f"Copy to…{suffix}", "fileops.copy")
    files.append(f"Move to…{suffix}", "fileops.move")
    files.append(f"Move to Trash{suffix}", "fileops.trash")
    menu.append_section(None, files)
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
