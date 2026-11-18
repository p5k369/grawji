"""Small informational dialogs."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

import grawji
from grawji import shortcuts


def fit_dialog(
    dialog: Adw.Dialog,
    parent: Gtk.Widget,
    *,
    width_fraction: float = 0.0,
    height_fraction: float = 0.0,
) -> None:
    """Scale a dialog to fractions of its parent window, per axis."""
    root = parent.get_root()
    if not isinstance(root, Gtk.Window):
        return
    width = round(root.get_width() * width_fraction)
    height = round(root.get_height() * height_fraction)
    if width > dialog.get_content_width():
        dialog.set_content_width(width)
    if height > dialog.get_content_height():
        dialog.set_content_height(height)


def app_version() -> str:
    """Return grawji's version."""
    return grawji.__version__


def present_shortcuts(parent: Gtk.Widget) -> None:
    """Show a dialog listing the keyboard shortcuts."""
    page = Adw.PreferencesPage()
    for title, entries in shortcuts.GROUPS.items():
        group = Adw.PreferencesGroup(title=title)
        for entry in entries:
            row = Adw.ActionRow(title=entry.label)
            if entry.accelerator:
                shortcut = Gtk.ShortcutLabel(accelerator=entry.accelerator)
                shortcut.set_valign(Gtk.Align.CENTER)
                row.add_suffix(shortcut)
            group.add(row)
        page.add(group)
    dialog = Adw.PreferencesDialog()
    dialog.set_title("Keyboard Shortcuts")
    dialog.add(page)
    dialog.present(parent)


def present_about(parent: Gtk.Widget) -> None:
    """Show the About dialog."""
    about = Adw.AboutDialog(
        application_name="grawji",
        application_icon="io.github.p5k369.grawji",
        version=app_version(),
        website="https://github.com/p5k369/grawji",
        issue_url="https://github.com/p5k369/grawji/issues",
        license_type=Gtk.License.GPL_3_0,
        copyright="© 2026 Patrick Zwerschke",
    )
    about.add_credit_section(
        "Credits",
        [
            "rawji by pinpox https://github.com/pinpox/rawji",
            "petabyt https://github.com/petabyt",
            "darktable https://github.com/darktable-org/darktable",
            "Michael F. Hutt https://github.com/huttmf/nelder-mead",
        ],
    )
    about.add_legal_section(
        "Automatic perspective correction",
        "© 2016-2026 darktable developers",
        Gtk.License.GPL_3_0,
        None,
    )
    about.add_legal_section(
        "Nelder-Mead simplex",
        "© 1997-2011 Michael F. Hutt",
        Gtk.License.MIT_X11,
        None,
    )
    about.present(parent)
