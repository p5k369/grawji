"""Preferences dialog."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from grawji.settings import Settings

_UI = (
    resources.files("grawji")
    .joinpath("ui", "preferences.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class PreferencesDialog(Adw.PreferencesDialog):
    """Edit the user settings, persisting each change immediately."""

    __gtype_name__ = "GrawjiPreferencesDialog"

    load_recipe_row = Gtk.Template.Child()
    jpeg_quality_scale = Gtk.Template.Child()
    batch_skip_row = Gtk.Template.Child()

    def __init__(
        self, *, settings: Settings, on_change: Callable[[], None]
    ) -> None:
        """Create the dialog bound to settings.

        Args:
            settings: The settings object to read and update in place.
            on_change: Called after every edit so the caller can persist.
        """
        super().__init__()
        self._settings = settings
        self._on_change = on_change

        self.load_recipe_row.set_active(settings.load_recipe_from_image)
        self.jpeg_quality_scale.set_value(settings.jpeg_quality)
        self.batch_skip_row.set_active(settings.batch_skip_foreign)
        self.load_recipe_row.connect("notify::active", self._on_edited)
        self.jpeg_quality_scale.connect("value-changed", self._on_edited)
        self.batch_skip_row.connect("notify::active", self._on_edited)

    def _on_edited(self, *_args: object) -> None:
        """Copy the row values into settings and notify the caller."""
        self._settings.load_recipe_from_image = (
            self.load_recipe_row.get_active()
        )
        self._settings.jpeg_quality = int(self.jpeg_quality_scale.get_value())
        self._settings.batch_skip_foreign = self.batch_skip_row.get_active()
        self._on_change()
