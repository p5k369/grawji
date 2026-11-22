"""The choice between a shot's RAW and its camera file."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk

from grawji import sidecar
from grawji.pairs import Source, companion_label
from grawji.views.folder_model import FolderModel


@dataclass(frozen=True)
class SourceHost:
    """What the source switch needs from the window.

    Attributes:
        open_path: The image the window has open.
        selected_path: The image selected in the filmstrip.
        reload: Opens an image again, from its new source.
        on_shown: Called with whether the selected shot is developed from
            its camera file.
    """

    open_path: Callable[[], Path | None]
    selected_path: Callable[[], str | None]
    reload: Callable[[str], None]
    on_shown: Callable[[bool], None]


class SourceSwitch:
    """Offers the RAW or camera file switch for a RAW+JPEG pair."""

    def __init__(
        self,
        actions: Gio.ActionMap,
        folder: FolderModel,
        widgets: tuple[Gtk.Widget, Any],
        host: SourceHost,
    ) -> None:
        """Add the source actions and bind the switch to its widgets.

        Args:
            actions: The window the two actions are added to.
            folder: Where the shots and their companions are known.
            widgets: The switch and the button naming the camera file.
            host: The window's side of the switch.
        """
        self._folder = folder
        self._switch, self._camera_button = widgets
        self._host = host
        self._action = Gio.SimpleAction.new_stateful(
            "source",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(Source.RAW.value),
        )
        self._action.connect("change-state", self._on_picked)
        self._action.set_enabled(False)
        actions.add_action(self._action)
        toggle = Gio.SimpleAction.new("toggle-source", None)
        toggle.connect("activate", lambda *_a: self.toggle())
        actions.add_action(toggle)

    def camera_file_of(self, raf_path: str | Path | None) -> str | None:
        """The camera file a shot is developed from."""
        if raf_path is None:
            return None
        entry = self._folder.entry_for(str(raf_path))
        if entry is None or not entry.uses_camera_file:
            return None
        return entry.companion

    def status(self) -> str:
        """Say which camera file the open shot is developed from."""
        companion = self.camera_file_of(self._host.open_path())
        label = companion_label(companion) if companion else "file"
        return f"Developed from the camera {label}."

    def toggle(self) -> None:
        """Switch the open shot between its RAW and its camera file."""
        if not self._action.get_enabled():
            return
        current = Source(self._action.get_state().get_string())
        other = Source.RAW if current is Source.CAMERA else Source.CAMERA
        self._action.change_state(GLib.Variant.new_string(other))

    def show_current(self) -> None:
        """Offer the switch when the selected shot is a pair."""
        path = self._host.selected_path()
        entry = self._folder.entry_for(path) if path else None
        companion = entry.companion if entry is not None else None
        paired = companion is not None
        if companion is not None:
            self._camera_button.set_label(companion_label(companion))
        self._switch.set_visible(paired)
        self._action.set_enabled(paired)
        source = entry.source if entry is not None else Source.RAW
        self._action.set_state(GLib.Variant.new_string(source))
        self._host.on_shown(entry is not None and entry.uses_camera_file)

    def _on_picked(
        self, action: Gio.SimpleAction, value: GLib.Variant
    ) -> None:
        """Store the picked source and open the shot from it."""
        open_path = self._host.open_path()
        if open_path is None or value.equal(action.get_state()):
            return
        action.set_state(value)
        path = str(open_path)
        source = Source(value.get_string())
        sidecar.save_source(path, source)
        self._folder.set_source(path, source)
        self._host.reload(path)
