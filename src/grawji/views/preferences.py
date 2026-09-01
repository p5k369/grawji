"""Preferences dialog."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from grawji.settings import Settings

_UI = (
    resources.files("grawji")
    .joinpath("ui", "preferences.ui")
    .read_text(encoding="utf-8")
)
_COLOR_SCHEMES = ["default", "light", "dark"]
_DRAG_ACTIONS = ["move", "copy"]


def _split_aspect(label: str) -> tuple[str, bool]:
    """A stored "W:H" aspect as (canonical landscape label, portrait)."""
    wide, sep, tall = label.partition(":")
    try:
        portrait = bool(sep) and float(wide) < float(tall)
    except ValueError:
        return "None", False
    return (f"{tall}:{wide}", True) if portrait else (label, False)


@Gtk.Template(string=_UI)
class PreferencesDialog(Adw.PreferencesDialog):
    """Edit the user settings, persisting each change immediately."""

    __gtype_name__ = "GrawjiPreferencesDialog"

    color_scheme_row = Gtk.Template.Child()
    wb_grid_row = Gtk.Template.Child()
    jpeg_quality_scale = Gtk.Template.Child()
    glide_speed_scale = Gtk.Template.Child()
    drag_action_row = Gtk.Template.Child()
    max_edge_row = Gtk.Template.Child()
    artist_row = Gtk.Template.Child()
    copyright_row = Gtk.Template.Child()
    provenance_row = Gtk.Template.Child()
    border_row = Gtk.Template.Child()
    border_percent_row = Gtk.Template.Child()
    border_color_button = Gtk.Template.Child()
    border_aspect_row = Gtk.Template.Child()
    border_portrait_row = Gtk.Template.Child()

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
        self.wb_grid_row.set_active(settings.wb_grid_tint)
        self.jpeg_quality_scale.set_value(settings.jpeg_quality)
        self.glide_speed_scale.set_value(settings.nav_glide_speed)
        drag = settings.drag_action
        self.drag_action_row.set_selected(
            _DRAG_ACTIONS.index(drag) if drag in _DRAG_ACTIONS else 0
        )
        self.max_edge_row.set_value(settings.export_max_edge)
        self.artist_row.set_text(settings.export_artist)
        self.copyright_row.set_text(settings.export_copyright)
        self.provenance_row.set_active(settings.export_provenance)
        self.border_row.set_enable_expansion(settings.export_border_enabled)
        self.border_row.set_expanded(settings.export_border_enabled)
        self.border_row.connect(
            "notify::enable-expansion", self._on_border_toggled
        )
        self.border_percent_row.set_value(settings.export_border_percent)
        self.border_color_button.set_dialog(Gtk.ColorDialog())
        rgba = Gdk.RGBA()
        if not rgba.parse(settings.export_border_color):
            rgba.parse("#ffffff")
        self.border_color_button.set_rgba(rgba)
        canonical, portrait = _split_aspect(settings.export_border_aspect)
        aspect_model = self.border_aspect_row.get_model()
        for i in range(aspect_model.get_n_items()):
            if aspect_model.get_string(i) == canonical:
                self.border_aspect_row.set_selected(i)
                break
        self.border_portrait_row.set_active(portrait)
        self._update_portrait_sensitivity()
        self.color_scheme_row.connect("notify::selected", self._on_edited)
        self.wb_grid_row.connect("notify::active", self._on_edited)
        self.jpeg_quality_scale.connect("value-changed", self._on_edited)
        self.glide_speed_scale.connect("value-changed", self._on_edited)
        self.drag_action_row.connect("notify::selected", self._on_edited)
        self.max_edge_row.connect("notify::value", self._on_edited)
        self.artist_row.connect("changed", self._on_edited)
        self.copyright_row.connect("changed", self._on_edited)
        self.provenance_row.connect("notify::active", self._on_edited)
        self.border_row.connect("notify::enable-expansion", self._on_edited)
        self.border_percent_row.connect("notify::value", self._on_edited)
        self.border_color_button.connect("notify::rgba", self._on_edited)
        self.border_aspect_row.connect("notify::selected", self._on_edited)
        self.border_portrait_row.connect("notify::active", self._on_edited)

    def _on_border_toggled(self, *_args: object) -> None:
        """Reveal the border controls whenever the switch turns on."""
        self.border_row.set_expanded(self.border_row.get_enable_expansion())

    def _update_portrait_sensitivity(self) -> None:
        """The portrait switch only matters for non-square ratios."""
        item = self.border_aspect_row.get_selected_item()
        label = item.get_string() if item is not None else "None"
        self.border_portrait_row.set_sensitive(label not in ("None", "1:1"))

    def _on_edited(self, *_args: object) -> None:
        """Copy the row values into settings and notify the caller."""
        self._settings.color_scheme = _COLOR_SCHEMES[
            self.color_scheme_row.get_selected()
        ]
        self._settings.wb_grid_tint = self.wb_grid_row.get_active()
        self._settings.jpeg_quality = int(self.jpeg_quality_scale.get_value())
        self._settings.nav_glide_speed = int(
            self.glide_speed_scale.get_value()
        )
        self._settings.drag_action = _DRAG_ACTIONS[
            self.drag_action_row.get_selected()
        ]
        self._settings.export_max_edge = int(self.max_edge_row.get_value())
        self._settings.export_artist = self.artist_row.get_text().strip()
        self._settings.export_copyright = self.copyright_row.get_text().strip()
        self._settings.export_provenance = self.provenance_row.get_active()
        self._settings.export_border_enabled = (
            self.border_row.get_enable_expansion()
        )
        self._settings.export_border_percent = round(
            self.border_percent_row.get_value(), 1
        )
        aspect_item = self.border_aspect_row.get_selected_item()
        aspect = (
            aspect_item.get_string() if aspect_item is not None else "None"
        )
        if self.border_portrait_row.get_active() and ":" in aspect:
            wide, _, tall = aspect.partition(":")
            aspect = f"{tall}:{wide}"
        self._settings.export_border_aspect = aspect
        self._update_portrait_sensitivity()
        rgba = self.border_color_button.get_rgba()
        red = round(rgba.red * 255)
        green = round(rgba.green * 255)
        blue = round(rgba.blue * 255)
        self._settings.export_border_color = f"#{red:02x}{green:02x}{blue:02x}"
        self._on_change()
