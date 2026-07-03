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
_COLOR_SCHEMES = ["default", "light", "dark"]


@Gtk.Template(string=_UI)
class PreferencesDialog(Adw.PreferencesDialog):
    """Edit the user settings, persisting each change immediately."""

    __gtype_name__ = "GrawjiPreferencesDialog"

    color_scheme_row = Gtk.Template.Child()
    load_recipe_row = Gtk.Template.Child()
    wb_grid_row = Gtk.Template.Child()
    jpeg_quality_scale = Gtk.Template.Child()
    glide_speed_scale = Gtk.Template.Child()

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

        scheme = settings.color_scheme
        index = _COLOR_SCHEMES.index(scheme) if scheme in _COLOR_SCHEMES else 0
        self.color_scheme_row.set_selected(index)
        self.load_recipe_row.set_active(settings.load_recipe_from_image)
        self.wb_grid_row.set_active(settings.wb_grid_tint)
        self.jpeg_quality_scale.set_value(settings.jpeg_quality)
        self.glide_speed_scale.set_value(settings.nav_glide_speed)
        self.color_scheme_row.connect("notify::selected", self._on_edited)
        self.load_recipe_row.connect("notify::active", self._on_edited)
        self.wb_grid_row.connect("notify::active", self._on_edited)
        self.jpeg_quality_scale.connect("value-changed", self._on_edited)
        self.glide_speed_scale.connect("value-changed", self._on_edited)

    def _on_edited(self, *_args: object) -> None:
        """Copy the row values into settings and notify the caller."""
        self._settings.color_scheme = _COLOR_SCHEMES[
            self.color_scheme_row.get_selected()
        ]
        self._settings.load_recipe_from_image = (
            self.load_recipe_row.get_active()
        )
        self._settings.wb_grid_tint = self.wb_grid_row.get_active()
        self._settings.jpeg_quality = int(self.jpeg_quality_scale.get_value())
        self._settings.nav_glide_speed = int(
            self.glide_speed_scale.get_value()
        )
        self._on_change()
