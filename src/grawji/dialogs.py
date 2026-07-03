"""Small informational dialogs: keyboard shortcuts and About."""

from __future__ import annotations

from importlib import metadata

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

_SHORTCUT_GROUPS = {
    "Files": [
        ("Export JPEG", "<Ctrl>E"),
    ],
    "Recipe": [
        ("Save recipe", "<Ctrl>S"),
        ("Reset to default", "<Ctrl>R"),
    ],
    "Navigation": [
        ("Previous image", "Left"),
        ("Next image", "Right"),
    ],
    "View": [
        ("Zoom in", "<Ctrl>plus"),
        ("Zoom out", "<Ctrl>minus"),
        ("Fit to window", "<Ctrl>0"),
        ("Cycle background", "b"),
        ("Show original (before/after)", "backslash"),
        ("Toggle histogram", "h"),
    ],
    "Application": [
        ("Preferences", "<Ctrl>comma"),
        ("Keyboard shortcuts", "<Ctrl>question"),
    ],
}


def app_version() -> str:
    """Return grawji's installed version, or a fallback if not packaged."""
    try:
        return metadata.version("grawji")
    except metadata.PackageNotFoundError:
        return "0.0.1"


def present_shortcuts(parent: Gtk.Widget) -> None:
    """Show a dialog listing the keyboard shortcuts."""
    page = Adw.PreferencesPage()
    for title, items in _SHORTCUT_GROUPS.items():
        group = Adw.PreferencesGroup(title=title)
        for label, accel in items:
            row = Adw.ActionRow(title=label)
            shortcut = Gtk.ShortcutLabel(accelerator=accel)
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
        application_icon="camera-photo-symbolic",
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
        ],
    )
    about.present(parent)
